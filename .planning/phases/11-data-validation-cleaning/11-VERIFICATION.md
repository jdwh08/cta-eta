---
phase: 11-data-validation-cleaning
verified: 2026-02-25T14:30:00Z
status: passed
score: 12/12 must-haves verified
re_verification: false
---

# Phase 11: Data Validation & Cleaning Verification Report

**Phase Goal:** Daily batch job (3am Chicago time) that merges yesterday's IPC journal files into a single validated, Snappy-compressed Parquet file per daemon per day, then uploads to cloud storage as immutable raw data
**Verified:** 2026-02-25T14:30:00Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| #  | Truth                                                                                 | Status     | Evidence                                                                                              |
|----|---------------------------------------------------------------------------------------|------------|-------------------------------------------------------------------------------------------------------|
| 1  | IPC files written by JournalWriter are discovered correctly by date and dataset       | VERIFIED   | `discover_journals()` in `ipc_reader.py` uses identical hive path: `year=YYYY/month=MM/day=DD/`      |
| 2  | Crash-written IPC files (no EOS marker) return all valid batches cleanly              | VERIFIED   | `read_ipc_with_repair()` catches `StopIteration` as `was_clean=True`; test `test_crash_file_*` passes |
| 3  | IPC files with corrupt trailing bytes return valid batches before the corruption      | VERIFIED   | `except (pa.lib.ArrowInvalid, OSError)` salvages prior batches; test `test_corrupt_trailing_*` passes |
| 4  | IPC files with corrupt headers yield zero batches and `was_clean=False`               | VERIFIED   | `except pa.lib.ArrowInvalid` at `open_stream()` returns `[], False`; test `test_corrupt_header_*` passes |
| 5  | Schema validation correctly passes matching schemas and rejects mismatches            | VERIFIED   | `table.schema.equals(expected_schema)` in `_compact_one_daemon()`; schema test class passes           |
| 6  | Running compact.py for a date reads all journals, merges, writes Snappy Parquet       | VERIFIED   | `_compact_one_daemon()` calls `discover_journals`, `read_ipc_with_repair`, `pa.concat_tables`, `pq.write_table(..., compression="snappy")` |
| 7  | Upload retries 3 times with exponential backoff on failure before alerting            | VERIFIED   | `stamina.retry_context(on=Exception, attempts=3, wait_initial=1.0, wait_max=30.0, wait_exp_base=2)` in `uploader.py` |
| 8  | Row count verified against remote Parquet metadata before journals are archived        | VERIFIED   | `pq.read_metadata(path, filesystem=pa_fs).num_rows == expected_rows` assertion inside stamina loop    |
| 9  | Journal files moved to archive only after verified upload                             | VERIFIED   | `archive_journals()` called only on successful `upload_parquet()` path; `TestArchiveNotCalledOnUploadFailure` confirms two-phase safety |
| 10 | JSON sidecar metrics file written in finally block (always, even on failure)          | VERIFIED   | `finally: if metrics is not None: _write_sidecar(metrics, compaction_dir)` in `main()`; `TestSidecarAlwaysWritten` confirms |
| 11 | `--reprocess <date>` flag re-compacts a past day with `reprocess=True` to upload     | VERIFIED   | `argparse` `--reprocess` flag threads `is_reprocess` to `upload_parquet(..., reprocess=is_reprocess)`; `TestReprocessFlagThreadedToUpload` confirms |
| 12 | Systemd timer fires at 3am Chicago time with `Persistent=true` catch-up              | VERIFIED   | `deploy/cta-compaction.timer` has `OnCalendar=America/Chicago *-*-* 03:00:00` and `Persistent=true`  |

**Score:** 12/12 truths verified

---

### Required Artifacts

#### Plan 11-01

| Artifact                                                              | Expected                                          | Status     | Details                                                           |
|-----------------------------------------------------------------------|---------------------------------------------------|------------|-------------------------------------------------------------------|
| `src/cta_eta/data_collection/compaction/__init__.py`                  | Package marker                                    | VERIFIED   | Exists; empty package init                                        |
| `src/cta_eta/data_collection/compaction/schemas.py`                  | `TRAIN_POSITION_SCHEMA` and `WEATHER_SCHEMA`      | VERIFIED   | 15-field train schema, 24-field weather schema; substantive, not stub |
| `src/cta_eta/data_collection/compaction/ipc_reader.py`               | `discover_journals()` and `read_ipc_with_repair()` | VERIFIED  | Both functions implemented with all 5 repair cases + hive path discovery |
| `tests/data_collection/compaction/__init__.py`                        | Package marker                                    | VERIFIED   | Exists                                                            |
| `tests/data_collection/compaction/test_ipc_reader.py`                 | TDD tests covering all repair/discovery paths     | VERIFIED   | 21 tests across 3 test classes (476 lines); all pass              |

#### Plan 11-02

| Artifact                                                              | Expected                                                    | Status     | Details                                                                       |
|-----------------------------------------------------------------------|-------------------------------------------------------------|------------|-------------------------------------------------------------------------------|
| `src/cta_eta/data_collection/compaction/uploader.py`                  | `upload_parquet()` with stamina retry + row count verify    | VERIFIED   | `stamina.retry_context(attempts=3)` + `pq.read_metadata` assertion in loop    |
| `src/cta_eta/data_collection/compaction/archiver.py`                  | `archive_journals()` + `prune_archive()`                    | VERIFIED   | Both functions implemented; `shutil.move` + `shutil.rmtree` with retention    |
| `src/cta_eta/data_collection/compaction/compact.py`                   | CLI + `CompactionMetrics` + orchestration                   | VERIFIED   | `main()`, `CompactionMetrics` dataclass, `_compact_one_daemon()`, all wired   |
| `config.toml`                                                         | `[compaction]` section with 4 keys                          | VERIFIED   | Lines 173-182: `cloud_url`, `compaction_dir`, `archive_path`, `journal_retention_days` |
| `tests/data_collection/compaction/test_compact.py`                    | 3+ safety invariant tests                                   | VERIFIED   | 4 tests in 3 classes; all pass                                                |

#### Plan 11-03

| Artifact                                                              | Expected                                                    | Status     | Details                                                                       |
|-----------------------------------------------------------------------|-------------------------------------------------------------|------------|-------------------------------------------------------------------------------|
| `deploy/cta-compaction.service`                                       | Systemd oneshot service running `compact.py`                | VERIFIED   | `Type=oneshot`, `ExecStart=...python -m cta_eta.data_collection.compaction.compact` |
| `deploy/cta-compaction.timer`                                         | Persistent calendar timer at 3am Chicago                    | VERIFIED   | `Persistent=true`, `OnCalendar=America/Chicago *-*-* 03:00:00`                |
| `src/cta_eta/monitoring/cli.py`                                       | `compaction` subcommand added to `cta-monitor`              | VERIFIED   | `cmd_compaction()`, `_add_compaction_command()` defined; wired in `main()` at line 811 |
| `pyproject.toml`                                                      | `cta-compact` script entrypoint                             | VERIFIED   | `cta-compact = "cta_eta.data_collection.compaction.compact:main"` at line 95  |

---

### Key Link Verification

#### Plan 11-01 Key Links

| From                       | To                          | Via                                                         | Status   | Details                                                                  |
|----------------------------|-----------------------------|-------------------------------------------------------------|----------|--------------------------------------------------------------------------|
| `ipc_reader.py`            | `schemas.py`                | `from.*schemas import`                                      | VERIFIED | `test_ipc_reader.py` imports `TRAIN_POSITION_SCHEMA, WEATHER_SCHEMA` from schemas; ipc_reader uses same hive path |
| `journal_writer.py`        | `ipc_reader.py`             | Same hive path: `year=YYYY/month=MM/day=DD/journal_*.ipc`   | VERIFIED | JournalWriter: `f"year={now.year}"` / `f"month={now.month:02d}"` / `f"day={now.day:02d}"` / `f"journal_{now.strftime('%H%M%S_%f')}.ipc"`; `discover_journals` uses identical structure |

#### Plan 11-02 Key Links

| From                       | To                          | Via                                                         | Status   | Details                                                                  |
|----------------------------|-----------------------------|-------------------------------------------------------------|----------|--------------------------------------------------------------------------|
| `compact.py`               | `ipc_reader.py`             | `from.*ipc_reader import`                                   | VERIFIED | Line 30-33: `from cta_eta.data_collection.compaction.ipc_reader import discover_journals, read_ipc_with_repair` |
| `compact.py`               | `uploader.py`               | `from.*uploader import`                                     | VERIFIED | Line 38: `from cta_eta.data_collection.compaction.uploader import upload_parquet` |
| `compact.py`               | `archiver.py`               | `from.*archiver import`                                     | VERIFIED | Line 29: `from cta_eta.data_collection.compaction.archiver import archive_journals, prune_archive` |
| `compact.py`               | `schemas.py`                | `from.*schemas import`                                      | VERIFIED | Lines 34-37: `from cta_eta.data_collection.compaction.schemas import TRAIN_POSITION_SCHEMA, WEATHER_SCHEMA` |
| `uploader.py`              | `fsspec + pyarrow.fs`       | `url_to_fs\|PyFileSystem\|FSSpecHandler`                    | VERIFIED | `fsspec.url_to_fs(cloud_url)` + `pafs.PyFileSystem(pafs.FSSpecHandler(fs))` in `make_pyarrow_fs()` |

#### Plan 11-03 Key Links

| From                            | To                                  | Via                            | Status   | Details                                                              |
|---------------------------------|-------------------------------------|--------------------------------|----------|----------------------------------------------------------------------|
| `deploy/cta-compaction.service` | `compact.py`                        | `compaction.compact`           | VERIFIED | `ExecStart=/opt/cta-eta/.venv/bin/python -m cta_eta.data_collection.compaction.compact` |
| `src/cta_eta/monitoring/cli.py` | `data/compaction/compaction-*.json` | `compaction.*\.json` glob      | VERIFIED | `compaction_dir.glob("compaction-*.json")` in `cmd_compaction()` at line 669 |

---

### Requirements Coverage

No specific requirement IDs were declared in any of the three plan files (`requirements: []` in all). No phase-level requirements mapped in `REQUIREMENTS.md`. Not applicable.

---

### Anti-Patterns Found

No anti-patterns detected. Checked all 5 compaction module files for:
- TODO/FIXME/placeholder comments: none
- Empty implementations (`return null`, `return {}`, `=> {}`): none (the `return []` and `return [], False` at lines 47 and 78 of `ipc_reader.py` are correct intentional behavior for the missing-directory and corrupt-header cases)
- Console/print-only handlers: none (all use `logging.getLogger(__name__)`)
- Unconnected stubs: none (all key links verified wired)

---

### Human Verification Required

#### 1. Cloud upload against real object store

**Test:** Configure `config.toml` with a real S3/GCS bucket URL and run `cta-compact` against a day with actual journal data.
**Expected:** Parquet file appears in the bucket, row count matches local count, `cta-monitor compaction` shows the run as "success".
**Why human:** Cannot exercise real fsspec cloud backends in automated verification; requires cloud credentials and a live bucket.

#### 2. Systemd timer activation and timing

**Test:** On the production host, run `systemctl enable --now cta-compaction.timer` and verify `systemctl list-timers cta-compaction.timer` shows next trigger at 03:00:00 America/Chicago.
**Expected:** Timer fires at 3am local Chicago time, handles DST transitions correctly, `Persistent=true` fires immediately on boot if 3am was missed.
**Why human:** Systemd timer behavior with timezone prefixes and DST cannot be verified without a running systemd instance.

#### 3. `cta-monitor compaction` display with real sidecar data

**Test:** After a successful `cta-compact` run, run `cta-monitor compaction` and `cta-monitor compaction --json`.
**Expected:** Human-readable table with date, status, journals, rows, upload MB, elapsed time; `--json` outputs a valid JSON list; exit code 1 if any failed run in window.
**Why human:** Requires real sidecar JSON files on disk to verify display formatting.

---

### Gaps Summary

No gaps. All 12 observable truths verified, all artifacts pass all three levels (exists, substantive, wired), all key links confirmed wired, no anti-patterns found.

The phase fully achieves its goal: a daily batch job that discovers IPC journals by hive-style path, reads and repairs them with partial-corruption recovery, validates schemas, merges into a Snappy-compressed Parquet file per daemon per day, uploads with 3-attempt retry and row-count verification, archives journals only after verified upload, writes a JSON sidecar always (even on failure), and is deployed as a systemd oneshot service triggered by a Persistent timer at 3am Chicago time with a `cta-monitor compaction` subcommand for observability.

---

_Verified: 2026-02-25T14:30:00Z_
_Verifier: Claude (gsd-verifier)_
