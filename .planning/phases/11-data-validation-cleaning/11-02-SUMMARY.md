---
phase: 11-data-validation-cleaning
plan: "02"
subsystem: data_collection/compaction
tags: [compaction, parquet, fsspec, stamina, retry, archival, upload, cli]
dependency_graph:
  requires:
    - src/cta_eta/data_collection/compaction/ipc_reader.py
    - src/cta_eta/data_collection/compaction/schemas.py
  provides:
    - src/cta_eta/data_collection/compaction/uploader.py
    - src/cta_eta/data_collection/compaction/archiver.py
    - src/cta_eta/data_collection/compaction/compact.py
  affects:
    - "11-03: monitoring subcommand reads JSON sidecar files written by compact.py"
    - "config.toml: [compaction] section now present"
tech_stack:
  added: []
  patterns:
    - fsspec.url_to_fs() + PyFileSystem(FSSpecHandler(fs)) bridge for pluggable cloud upload
    - stamina.retry_context() for 3-attempt exponential backoff upload retry
    - Two-phase safety: upload verified before archive_journals() is called
    - CompactionMetrics dataclass + JSON sidecar always written in finally block
    - argparse --reprocess YYYY-MM-DD flag threading reprocess=True to upload_parquet
key_files:
  created:
    - src/cta_eta/data_collection/compaction/uploader.py
    - src/cta_eta/data_collection/compaction/archiver.py
    - src/cta_eta/data_collection/compaction/compact.py
    - tests/data_collection/compaction/test_compact.py
  modified:
    - config.toml
decisions:
  - "_compact_one_daemon catches upload exceptions and returns failed metrics rather than propagating — enables _write_sidecar finally block to always run and prevents journal archival on failure"
  - "send_compaction_alert called from _compact_one_daemon on upload failure (not just from main) — ensures alert fires precisely when upload fails, not on unrelated exceptions"
metrics:
  duration_seconds: 410
  completed_date: "2026-02-25"
  tasks_completed: 3
  files_created: 4
  files_modified: 1
---

# Phase 11 Plan 02: Compaction Pipeline (uploader.py + archiver.py + compact.py) Summary

Compaction pipeline implementation: pluggable fsspec cloud uploader with stamina 3-attempt retry and row-count verification, post-upload journal archiver with retention pruning, and compact.py CLI orchestrator that merges IPC journals into daily Snappy Parquet and writes JSON sidecar metrics always (even on failure).

## What Was Built

### uploader.py

`make_pyarrow_fs(cloud_url) -> tuple[PyFileSystem, str]`: builds pyarrow-compatible filesystem from any fsspec URL (s3://, gs://, local path) using `fsspec.url_to_fs()` + `PyFileSystem(FSSpecHandler(fs))` bridge.

`upload_parquet(table, cloud_url, *, reprocess=False) -> None`:
- If `reprocess=True`: reads existing remote Parquet metadata, logs existing row count before overwrite.
- Uses `stamina.retry_context(on=Exception, attempts=3, wait_initial=1.0, wait_max=30.0, wait_exp_base=2, timeout=None)` for 3-attempt retry with exponential backoff.
- Each attempt: writes table via `pq.write_table(..., compression="snappy")`, then verifies `pq.read_metadata(...).num_rows == len(table)`.
- Logs attempt number from `attempt.num`.
- Raises (propagates stamina's last exception) if all 3 attempts fail.

### archiver.py

`archive_journals(journal_files, archive_base, target_date) -> None`:
- Creates `archive_base / f"date={target_date.isoformat()}"` directory.
- Moves each file with `shutil.move()`.
- Logs count of archived files at INFO level.

`prune_archive(archive_base, retention_days=7) -> list[Path]`:
- Globs `archive_base / "date=*"` directories.
- Parses date from dirname; skips unparseable names (ValueError).
- Deletes dirs older than cutoff via `shutil.rmtree`; logs OSError at WARNING (doesn't raise).
- Returns list of pruned paths.

### compact.py

**CompactionMetrics dataclass**: 9 fields — date, daemon, status ("success"/"partial"/"failed"), journals_found, journals_repaired, journals_skipped, rows_written, upload_bytes, elapsed_seconds, error (optional).

**`_compact_one_daemon(daemon_name, target_date, config, *, reprocess=False) -> CompactionMetrics`**:
1. Discovers journals via `discover_journals()`.
2. Empty journals: returns status="partial" with 0 rows.
3. Reads each journal with `read_ipc_with_repair()`, tracks repaired/skipped counts.
4. Validates each table's schema against `TRAIN_POSITION_SCHEMA` or `WEATHER_SCHEMA`.
5. `pa.concat_tables()` and writes local staging Parquet to `data/compaction/{daemon}/date={date}/data.parquet`.
6. Calls `upload_parquet()` — if it raises (all retries exhausted): calls `send_compaction_alert()`, returns status="failed" WITHOUT calling `archive_journals()` (two-phase safety invariant).
7. Only on successful upload: calls `archive_journals()`, then `prune_archive()`.

**`_write_sidecar(metrics, compaction_dir)`**: writes `compaction-{date}-{daemon}.json` using `asdict(metrics)`.

**`send_compaction_alert(metrics, config)`**: uses `config["alerting"]` section; calls `_build_email_config()` + `send_email_alert()` from Phase 9 alerting. Gracefully skips if section is absent.

**`main(argv=None)`**: parses `--reprocess YYYY-MM-DD`, loops over `["train_positions", "weather"]`, calls `_compact_one_daemon()`, writes sidecar in finally block.

### config.toml [compaction] section

Added at end of config.toml:
```toml
[compaction]
cloud_url = "s3://CHANGEME/raw"
compaction_dir = "data/compaction"
archive_path = "data/archive"
journal_retention_days = 7
```

### test_compact.py

4 tests across 3 test classes:

1. **`TestArchiveNotCalledOnUploadFailure`**: patches `upload_parquet` to raise, asserts `archive_journals` mock not called, metrics has status="failed".
2. **`TestSidecarAlwaysWritten`**: patches `upload_parquet` to raise, calls `main([])`, asserts `_write_sidecar` mock called at least once.
3. **`TestReprocessFlagThreadedToUpload`** (2 tests): calls `main(["--reprocess", "2026-02-17"])` and asserts `upload_parquet` called with `reprocess=True`; calls `main([])` and asserts `reprocess=False`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] _compact_one_daemon must catch upload exceptions to enforce two-phase safety**
- **Found during:** Task 3 test execution (Test 1 failure)
- **Issue:** Plan specification said "return metrics with status='failed'" on upload failure, but the original implementation let the upload exception propagate to `main()`. This meant `archive_journals` would never be called (since the exception propagates before it), but the test correctly required that `_compact_one_daemon` *return* a failed metrics object rather than raise. The plan's "trigger alert via send_compaction_alert(metrics, config)" also implies the alert call happens inside `_compact_one_daemon`, not only in `main()`.
- **Fix:** Added `try/except Exception` around `upload_parquet()` call in `_compact_one_daemon`; on failure: constructs failed CompactionMetrics, calls `send_compaction_alert(failed_metrics, config)`, returns failed_metrics (no archive call). `main()` still has its own outer try/except for other unexpected exceptions.
- **Files modified:** `src/cta_eta/data_collection/compaction/compact.py`
- **Commit:** 179b545

## Verification

- `uv run python -m cta_eta.data_collection.compaction.compact --help`: shows usage with `--reprocess YYYY-MM-DD` option
- `uv run basedpyright src/cta_eta/data_collection/compaction/`: 0 errors, 0 warnings, 0 notes across all 5 modules
- `uv run pytest tests/data_collection/compaction/test_compact.py -v`: 4/4 passed
- `uv run pytest`: 800/800 passed (796 existing + 4 new; no regressions)
- `grep "[compaction]" config.toml`: section found with all 4 keys
- Import chain: all 3 new modules importable, all exports available

## Self-Check: PASSED
