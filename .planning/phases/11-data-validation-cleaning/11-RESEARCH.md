# Phase 11: Data Compaction - Research

**Researched:** 2026-02-24
**Domain:** PyArrow IPC-to-Parquet batch pipeline, fsspec cloud upload, systemd timer scheduling
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

#### Job scheduling & recovery
- Systemd timer — consistent with existing daemon deployment pattern
- Persistent/catch-up timer: if machine is offline at 3am, run immediately on next boot
- Support a `--reprocess <date>` flag to re-compact a past day (overwrites existing Parquet)
- After successful upload, move local journal files to an archive folder with configurable retention period (not deleted immediately, not kept indefinitely)

#### Cloud storage target
- Use a pluggable/abstract interface (fsspec-compatible) — specific cloud provider configured at runtime, not hardcoded
- Hive-style partitioning path: e.g., `raw/train-positions/date=2026-02-17/data.parquet`
- Credentials via environment variables (provider SDK auto-discovery: AWS_*, GCS_*, etc.)
- After upload, verify row count matches local total before marking journals for archival

#### Validation & error handling
- Missing journal files (daemon was down): compact what exists, log which time windows had no data — don't block on gaps
- Corrupt journal files: attempt partial repair (truncate to last valid IPC record) before skipping
- Upload failure: retry 3 times with exponential backoff, then fail and send alert; leave journals in place for next run
- Pre-upload validation: schema validation only — confirm columns and types match expected schema (statistical validation is Phase 12)

#### Monitoring integration
- Add compaction section to existing `cta-monitor` CLI (not a separate command)
- Failed compaction triggers the existing Phase 9 email alerting system
- Core metrics tracked: journal files processed, rows written, upload size, elapsed time, final status
- Metrics persisted as JSON sidecar file (e.g., `compaction-2026-02-17.json`) alongside local compacted output — cta-monitor reads from there

### Claude's Discretion
- Exact retention period default for journal archive folder
- Internal IPC repair implementation details
- Systemd timer unit configuration specifics
- Row group size and other Parquet write options (Snappy compression is fixed)

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

---

## Summary

Phase 11 implements a daily batch compaction job that reads Arrow IPC stream files produced by Phase 10's `JournalWriter`, merges them into a single Snappy-compressed Parquet file per daemon per day, uploads to cloud storage via fsspec, and archives the source journals. The entire stack (pyarrow 22.0.0, fsspec 2026.1.0, stamina 25.2.0) is already installed and familiar from earlier phases.

The most important technical insight from verification testing is that pyarrow's `RecordBatchStreamReader` handles IPC files without an EOS marker (daemon crash) cleanly — it returns a `StopIteration` rather than raising, so partial-write journals from crashes yield all successfully written batches. The repair case requiring actual error handling is files with corrupt trailing bytes, where `read_next_batch()` raises `ArrowInvalid` after yielding all valid batches before the corruption. This means a single batch-by-batch loop with try/except recovers both crash cases and trailing-corruption cases with identical code.

The pluggable cloud interface is built on the existing `CloudStorage`/`StorageBackend` pattern from `storage.py`. For compaction, the upload should use `fsspec.url_to_fs(cloud_url)` directly (project already uses `fsspec.filesystem(type, ...)`) and write via `pq.write_table(table, path, filesystem=pyarrow_fs, compression='snappy')` using pyarrow's `PyFileSystem(FSSpecHandler(fs))` bridge. Row count verification reads `pq.read_metadata(remote_path).num_rows` — metadata-only, no column data transferred.

**Primary recommendation:** Build compaction as a standalone `cta_eta/data_collection/compaction/` module with a `compact.py` entry point, a systemd oneshot service + persistent calendar timer, and a `cmd_compaction` subcommand added to the existing `cta-monitor` CLI that reads the JSON sidecar file.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pyarrow | 22.0.0 | IPC reading, table concat, Parquet writing | Already installed; Phase 10 uses it for IPC write |
| fsspec | 2026.1.0 | Pluggable cloud filesystem abstraction | Already in pyproject.toml; existing CloudStorage uses it |
| s3fs | 2026.1.0 | S3 backend for fsspec | Already installed |
| gcsfs | 2026.1.0 | GCS backend for fsspec | Already installed |
| stamina | 25.2.0 | Retry with exponential backoff | Already installed; used throughout data_collection |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| zoneinfo (stdlib) | Python 3.13 | Chicago timezone for 3am partition boundary | Same pattern used in JournalWriter |
| tomllib (stdlib) | Python 3.13 | Read config.toml for cloud target, retention, archive path | Same pattern as existing config.py |
| argparse (stdlib) | Python 3.13 | `--reprocess <date>` CLI flag | Same pattern as existing monitoring CLI |
| pathlib (stdlib) | Python 3.13 | Local IPC file discovery and archive moves | Already used everywhere |
| json (stdlib) | Python 3.13 | JSON sidecar metrics file write/read | Same pattern as existing daemon state files |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `pq.write_table` (one-shot) | `pq.ParquetWriter` (incremental) | One-shot is correct here — compaction assembles the full table in memory before writing; incremental writer only needed for append workflows |
| `fsspec.url_to_fs` | `fsspec.filesystem(type, ...)` | Either works; `url_to_fs` is more natural for a URL-configured cloud target; existing `CloudStorage` uses `filesystem()`. Either fits. |
| stamina `@retry` decorator | stamina `retry_context` | `retry_context` is better for upload — allows per-attempt logging and row-count verification after each attempt |

**Installation:** All packages already in `pyproject.toml`. No new dependencies required.

---

## Architecture Patterns

### Recommended Project Structure

```
src/cta_eta/data_collection/compaction/
├── __init__.py
├── compact.py          # Entry point: CLI arg parsing, top-level orchestration
├── ipc_reader.py       # IPC file discovery, partial repair, batch reading
├── uploader.py         # fsspec upload with stamina retry + row count verification
└── archiver.py         # Post-upload archive move + retention cleanup
```

Systemd deploy files (alongside existing service/timer files):
```
deploy/
├── cta-compaction.service   # Type=oneshot, runs compact.py
└── cta-compaction.timer     # OnCalendar=*-*-* 03:00:00, Persistent=true
```

Metrics sidecar output (written by compact.py, read by cta-monitor):
```
data/compaction/
└── compaction-{YYYY-MM-DD}.json   # Per-run metrics
```

### Pattern 1: IPC File Discovery (Hive Path)

**What:** Glob for all `journal_*.ipc` files under `{data_path}/{dataset_name}/year=YYYY/month=MM/day=DD/`

**When to use:** Discovering yesterday's journals for compaction. The `--reprocess <date>` flag changes the target date but uses the same discovery logic.

```python
# Source: verified against JournalWriter._open_new_journal path construction
from pathlib import Path
from datetime import date

def discover_journals(data_path: Path, dataset_name: str, target_date: date) -> list[Path]:
    """Discover all IPC journal files for a given date."""
    day_dir = (
        data_path
        / dataset_name
        / f"year={target_date.year}"
        / f"month={target_date.month:02d}"
        / f"day={target_date.day:02d}"
    )
    if not day_dir.exists():
        return []
    return sorted(day_dir.glob("journal_*.ipc"))
```

### Pattern 2: Partial IPC Repair (Recover Valid Batches)

**What:** Read IPC stream batch-by-batch, catching `ArrowInvalid` to salvage batches before corruption. The same loop handles both "no EOS marker" (crash without corruption — verified to produce `StopIteration` cleanly) and "corrupt trailing bytes" (raises `ArrowInvalid` after valid batches).

**Verification findings:**
- A file written by `JournalWriter` without calling `.close()` (daemon crash) reads cleanly with `StopIteration` — pyarrow detects the missing EOS gracefully.
- A file with appended corrupt bytes after valid batches raises `ArrowInvalid` during `read_next_batch()` after returning all valid batches first.
- A file where the initial corruption prevents reading the schema header raises `ArrowInvalid` immediately, yielding 0 batches — this is the "skip file" case.

```python
# Source: verified against pyarrow 22.0.0 behavior
import pyarrow as pa
import pyarrow.ipc as ipc
from pathlib import Path

def read_ipc_with_repair(path: Path) -> tuple[list[pa.RecordBatch], bool]:
    """Read IPC stream, salvaging valid batches on corruption.

    Returns:
        (batches, was_clean) — batches read, and whether file was fully valid.
    """
    batches: list[pa.RecordBatch] = []
    try:
        reader = ipc.open_stream(path)
    except pa.lib.ArrowInvalid:
        # Header/schema corrupt — skip entirely
        return [], False

    while True:
        try:
            batch = reader.read_next_batch()
            batches.append(batch)
        except StopIteration:
            return batches, True  # Clean read (EOS found or graceful missing-EOS)
        except pa.lib.ArrowInvalid:
            return batches, False  # Truncated at batch boundary — salvaged what existed
```

### Pattern 3: Merge + Write Parquet (Snappy)

**What:** Concatenate salvaged tables, validate schema, write Parquet with Snappy compression.

```python
# Source: verified against pyarrow 22.0.0
import pyarrow as pa
import pyarrow.parquet as pq

EXPECTED_SCHEMA: pa.Schema = pa.schema([...])  # define per-daemon

def merge_and_write(
    tables: list[pa.Table],
    output_path: str,
    filesystem,  # pyarrow filesystem (PyFileSystem wrapping fsspec)
) -> int:
    """Merge tables, validate schema, write Parquet. Returns row count."""
    merged = pa.concat_tables(tables)
    # Schema validation: check columns and types match expected
    if not merged.schema.equals(EXPECTED_SCHEMA):
        raise ValueError(f"Schema mismatch: {merged.schema} != {EXPECTED_SCHEMA}")
    pq.write_table(merged, output_path, filesystem=filesystem, compression="snappy")
    return len(merged)
```

### Pattern 4: Pluggable Cloud Upload via fsspec

**What:** Use `fsspec.url_to_fs` to get provider from URL, bridge to pyarrow via `PyFileSystem(FSSpecHandler(fs))`.

```python
# Source: verified against fsspec 2026.1.0 + pyarrow 22.0.0
import fsspec
import pyarrow.fs as pafs
import pyarrow.parquet as pq

def get_pyarrow_filesystem(cloud_url: str):
    """Get pyarrow-compatible filesystem from a cloud URL.

    cloud_url examples:
        's3://my-bucket/raw/train-positions/date=2026-02-17/data.parquet'
        'gs://my-bucket/raw/train-positions/date=2026-02-17/data.parquet'
        '/local/path/raw/...'   (for testing)
    """
    fs, path = fsspec.url_to_fs(cloud_url)
    return pafs.PyFileSystem(pafs.FSSpecHandler(fs)), path
```

### Pattern 5: Row Count Verification (Metadata Only)

**What:** After upload, read Parquet file metadata to verify row count — no column data transferred.

```python
# Source: verified against pyarrow 22.0.0
import pyarrow.parquet as pq

def verify_upload_row_count(remote_path: str, filesystem, expected_rows: int) -> bool:
    """Verify uploaded Parquet row count matches local count via metadata-only read."""
    meta = pq.read_metadata(remote_path, filesystem=filesystem)
    return meta.num_rows == expected_rows
```

### Pattern 6: Stamina Upload Retry (3 attempts, exponential backoff)

**What:** Retry upload up to 3 times with exponential backoff before failing and alerting.

```python
# Source: verified against stamina 25.2.0 API
import stamina

for attempt in stamina.retry_context(
    on=Exception,
    attempts=3,
    wait_initial=1.0,
    wait_max=30.0,
    wait_exp_base=2,
    timeout=None,  # attempts-based, not time-based
):
    with attempt:
        upload_parquet(table, cloud_path, filesystem)
        if not verify_upload_row_count(cloud_path, filesystem, expected_rows):
            raise ValueError(f"Row count mismatch after upload to {cloud_path}")
```

### Pattern 7: Systemd Persistent Calendar Timer

**What:** Run at 3am Chicago time daily; catch up immediately if machine was offline.

```ini
# cta-compaction.timer
[Unit]
Description=CTA Data Compaction Timer
Requires=cta-compaction.service

[Timer]
OnCalendar=America/Chicago *-*-* 03:00:00
Persistent=true
Unit=cta-compaction.service

[Install]
WantedBy=timers.target
```

```ini
# cta-compaction.service
[Unit]
Description=CTA Data Compaction Job
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=cta-eta
WorkingDirectory=/opt/cta-eta
EnvironmentFile=/opt/cta-eta/.env
ExecStart=/opt/cta-eta/.venv/bin/python -m cta_eta.data_collection.compaction.compact
StandardOutput=journal
StandardError=journal
SyslogIdentifier=cta-compaction
```

**Key:** `Persistent=true` + `OnCalendar` means systemd stores the last run time in `/var/lib/systemd/timers/`. On next boot, if the scheduled time was missed, the timer fires immediately.

**Note on timezone in OnCalendar:** Systemd supports timezone prefix `TZ=America/Chicago *-*-* 03:00:00` syntax — verify systemd version supports this (available since systemd 233). Alternative: use `OnCalendar=*-*-* 09:00:00 UTC` (UTC 9am = Chicago 3am CST, 4am CDT — imprecise for DST transitions). Best practice: use the explicit TZ= syntax if systemd version supports it, otherwise schedule slightly later to handle DST gaps safely.

### Pattern 8: Journal Archival

**What:** After verified upload, move source journals to archive directory. Delete old archives past retention threshold.

```python
import shutil
from datetime import date, timedelta
from pathlib import Path

ARCHIVE_RETENTION_DAYS = 7  # Default: 1 week safety window

def archive_journals(
    journal_files: list[Path],
    archive_base: Path,
    target_date: date,
) -> None:
    """Move compacted journals to archive directory."""
    archive_dir = archive_base / f"date={target_date.isoformat()}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    for f in journal_files:
        shutil.move(str(f), archive_dir / f.name)

def prune_archive(archive_base: Path, retention_days: int = ARCHIVE_RETENTION_DAYS) -> None:
    """Delete archive directories older than retention_days."""
    cutoff = date.today() - timedelta(days=retention_days)
    for archive_dir in archive_base.glob("date=*"):
        try:
            dir_date = date.fromisoformat(archive_dir.name.removeprefix("date="))
            if dir_date < cutoff:
                shutil.rmtree(archive_dir)
        except (ValueError, OSError):
            pass
```

### Pattern 9: JSON Sidecar Metrics

**What:** Write per-run metrics to a sidecar JSON file for `cta-monitor` to read.

```python
import json
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path

@dataclass
class CompactionMetrics:
    date: str            # "2026-02-17"
    daemon: str          # "train_positions"
    status: str          # "success" | "partial" | "failed"
    journals_found: int
    journals_repaired: int
    journals_skipped: int
    rows_written: int
    upload_bytes: int
    elapsed_seconds: float
    error: str | None = None

def write_metrics(metrics: CompactionMetrics, compaction_dir: Path) -> None:
    sidecar = compaction_dir / f"compaction-{metrics.date}.json"
    sidecar.write_text(json.dumps(asdict(metrics), indent=2))
```

### Anti-Patterns to Avoid

- **Reading all IPC data into memory before schema check:** Always read schema first via `ipc.open_stream(path).schema`, validate before reading all batches. For the expected data volume (a day of 15-minute journals = ~96 files) the full-table-in-memory approach is fine, but schema validation should gate concat to avoid silent type promotion.
- **Using `pa.concat_tables` with `promote_options='default'`:** Testing shows promotion fails for incompatible types (int32 vs int64) with `ArrowTypeError`. The correct behavior is to raise loudly on schema mismatch, not silently coerce.
- **Hardcoding UTC 9am for Chicago 3am:** Chicago observes DST; use explicit timezone in `OnCalendar` or use `zoneinfo` to compute the target UTC time dynamically.
- **Archiving before upload verification:** The row count check must complete successfully before any journal is moved. Use a two-phase approach: upload → verify → archive.
- **Writing the sidecar only on success:** Write sidecar in a `finally` block so `cta-monitor` always has a status to display, even for failed runs.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Retry with backoff | Custom sleep/loop | `stamina.retry_context` | Already used in project; handles jitter, logging, instrumentation |
| Cloud filesystem abstraction | Custom S3/GCS clients | `fsspec` + `fsspec.url_to_fs` | Already installed; works with both S3 and GCS; existing CloudStorage pattern |
| pyarrow ↔ fsspec bridge | Manual stream open/upload | `pafs.PyFileSystem(pafs.FSSpecHandler(fs))` | Verified working; lets `pq.write_table` stream directly to cloud |
| Parquet row count check | Re-read full file | `pq.read_metadata(path).num_rows` | Metadata-only read; no column data transferred |
| Timezone-aware partition date | Custom date math | `JournalWriter._calculate_partition_date` pattern (or extract to shared util) | Already implemented in `storage.py:ParquetWriter` |

**Key insight:** pyarrow's `PyFileSystem(FSSpecHandler(fs))` bridge means the upload step is just `pq.write_table(table, path, filesystem=pa_fs, compression='snappy')` — no manual stream management needed.

---

## Common Pitfalls

### Pitfall 1: Systemd Timezone in OnCalendar

**What goes wrong:** Using `OnCalendar=*-*-* 09:00:00` (hardcoded UTC 9am) to approximate Chicago 3am breaks during DST transitions — runs at 4am Chicago time for half the year.

**Why it happens:** Systemd `OnCalendar` uses the system clock; timezone-prefixed syntax requires systemd >= 233.

**How to avoid:** Use `OnCalendar=America/Chicago *-*-* 03:00:00` if systemd version supports TZ= prefix. Verify with `systemd --version`. WSL2 typically runs a recent systemd; production Debian should be fine.

**Warning signs:** Compaction JSON sidecar timestamps show 4am during summer months.

### Pitfall 2: IPC File Left Open by Running Daemon

**What goes wrong:** The running daemon's current journal file is still open for writing when compaction runs at 3am. Reading it while the daemon writes can produce a corrupt or incomplete read.

**Why it happens:** JournalWriter rotates every 15 minutes; a rotation that started just before 3am may still be open at 3am.

**How to avoid:** The journal path embeds `journal_HHMMSS_microseconds.ipc`. Yesterday's journals all have timestamps from the previous calendar day's hive path (`day=DD-1`). Compaction targets `day=DD-1` only — the running daemon writes to `day=DD`. This is safe by construction. For `--reprocess` of today's date, document that the daemon should be stopped first.

**Warning signs:** Incomplete row counts or ArrowInvalid errors on files with today's date.

### Pitfall 3: Schema Mismatch Across Journal Files

**What goes wrong:** `pa.concat_tables` raises `ArrowInvalid` if two IPC files have different schemas (e.g., after a daemon restart with a schema change).

**Why it happens:** JournalWriter infers schema from the first batch; if schema changes between daemon restarts, different journal files for the same day may have incompatible schemas.

**How to avoid:** Compare each file's schema against a known expected schema before concat. Files that don't match should be logged and skipped, not silently coerced. Consider adding a `EXPECTED_SCHEMA` constant per dataset type.

**Warning signs:** `ArrowInvalid: Schema at index N was different` during concat.

### Pitfall 4: Partial Archive on Retry

**What goes wrong:** Upload fails on attempt 2 (after 1 journal was already archived), leaves the system in a split state.

**Why it happens:** Archiving happens per-file during upload rather than atomically after full verification.

**How to avoid:** Collect all journal paths in a list. Run the full upload + verify cycle atomically. Only call `archive_journals(all_files, ...)` after the full day's Parquet is verified. The sidecar file documents what was archived.

**Warning signs:** Journal files missing from source directory but upload failure logged in sidecar.

### Pitfall 5: `--reprocess` Overwrites Without Guard

**What goes wrong:** Running `--reprocess 2026-02-17` twice silently overwrites the cloud object, losing any manually corrected version.

**Why it happens:** No existence check before upload.

**How to avoid:** On `--reprocess`, check if the cloud object already exists and log a warning with the existing row count. Proceed with overwrite (as designed), but make it auditable. The sidecar file records the reprocess event.

**Warning signs:** Silent overwrites with no log record.

---

## Code Examples

Verified patterns from official sources:

### Read IPC Stream Batch-by-Batch

```python
# Source: verified against pyarrow 22.0.0
import pyarrow.ipc as ipc
import pyarrow as pa
from pathlib import Path

reader = ipc.open_stream(path)  # raises ArrowInvalid if header corrupt
batches = []
while True:
    try:
        batches.append(reader.read_next_batch())
    except StopIteration:
        break  # Clean: EOS found OR unclosed file (daemon crash)
    except pa.lib.ArrowInvalid:
        break  # Salvage: corrupt at batch boundary
# batches contains all valid record batches
```

### Concat and Write Parquet

```python
# Source: verified against pyarrow 22.0.0
import pyarrow as pa
import pyarrow.parquet as pq

tables = [pa.Table.from_batches(batches_i) for batches_i in all_batches]
merged = pa.concat_tables(tables)  # raises ArrowInvalid on schema mismatch
pq.write_table(merged, output_path, filesystem=pa_fs, compression="snappy")
```

### fsspec URL-to-Filesystem Bridge

```python
# Source: verified against fsspec 2026.1.0 + pyarrow 22.0.0
import fsspec
import pyarrow.fs as pafs

def make_pyarrow_fs(cloud_url: str):
    fs, path = fsspec.url_to_fs(cloud_url)
    return pafs.PyFileSystem(pafs.FSSpecHandler(fs)), path

# Works for:
#   s3://bucket/raw/train-positions/date=2026-02-17/data.parquet
#   gs://bucket/raw/train-positions/date=2026-02-17/data.parquet
#   /local/path/...
```

### Stamina Retry with Manual Context

```python
# Source: verified against stamina 25.2.0
import stamina

for attempt in stamina.retry_context(
    on=Exception,
    attempts=3,
    wait_initial=1.0,
    wait_max=30.0,
    wait_exp_base=2,
    timeout=None,
):
    with attempt:
        # upload and verify
        do_upload(...)
```

### Row Count Verification (Metadata Only)

```python
# Source: verified against pyarrow 22.0.0
import pyarrow.parquet as pq

meta = pq.read_metadata(remote_path, filesystem=pa_fs)
assert meta.num_rows == local_row_count
```

### Persistent Calendar Timer (systemd)

```ini
# Source: https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html
# Persistent=true fires immediately on boot if last run was missed
[Timer]
OnCalendar=America/Chicago *-*-* 03:00:00
Persistent=true
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Per-poll Parquet files (5,760/day) | IPC journal rotation (96 files/day) → daily Parquet | Phase 10 | Compaction is now the only consumer of IPC journals |
| Manual S3/GCS boto3 clients | fsspec abstraction | Established in storage.py | Same abstraction carries forward to compaction |
| Custom retry loops | stamina decorator/context | Established in project | Consistent retry pattern across all phases |

**Deprecated/outdated:**
- Phase 10's old per-poll Parquet write approach: replaced by JournalWriter; compaction is its downstream consumer
- `storage.py:ParquetWriter` (per-poll writer): not used by Phase 11; compaction writes the single daily Parquet directly

---

## Open Questions

1. **Systemd TZ= prefix support on target deployment**
   - What we know: `OnCalendar=America/Chicago *-*-* 03:00:00` requires systemd >= 233; WSL2/Debian testing should be fine
   - What's unclear: Exact systemd version on production host (WSL2 version may vary)
   - Recommendation: Add systemd version check to deploy README; fallback `OnCalendar=*-*-* 09:00:00 UTC` is acceptable for planning (document DST caveat)

2. **Expected schema constants per daemon**
   - What we know: Schema validation requires a known expected schema to compare against
   - What's unclear: Where to canonically define the expected schema for each daemon's Parquet output
   - Recommendation: Define `TRAIN_POSITION_SCHEMA` and `WEATHER_SCHEMA` constants in `compaction/schemas.py`; planner should include this as a task

3. **Retention period default**
   - What we know: Claude's discretion; needs a concrete default
   - What's unclear: N/A — this is a pure policy decision
   - Recommendation: **7 days** — matches existing `_DEFAULT_DAYS_WINDOW = 7` in `cli.py`, gives a one-week safety window after upload. Configurable via `config.toml` `[compaction] journal_retention_days = 7`

4. **Row group size for Parquet write**
   - What we know: Default `None` means min(table_rows, 1M); a day of train positions at 15-second intervals ~5,760 polls × ~300 trains = up to ~1.7M rows
   - What's unclear: Actual daily row count depends on collection uptime
   - Recommendation: Use `row_group_size=None` (default); let pyarrow choose. At ~1-2M rows, a single row group is fine for raw storage. Phase 12 (cleaning) can re-partition if needed.

---

## Sources

### Primary (HIGH confidence)

- pyarrow 22.0.0 — verified via `uv run python -c "import pyarrow; ..."`:
  - `ipc.open_stream`, `RecordBatchStreamReader.read_next_batch` behavior on corrupt/unclosed files
  - `pa.concat_tables` schema mismatch behavior
  - `pq.write_table` with `compression='snappy'`, `filesystem=` parameter
  - `pq.read_metadata` for row count verification
  - `pafs.PyFileSystem(pafs.FSSpecHandler(fs))` bridge pattern
- fsspec 2026.1.0 — verified via `uv run python`:
  - `fsspec.url_to_fs` for S3 (`s3://`), GCS (`gs://`), local (`/path`)
  - `fsspec.filesystem('s3')`, `fsspec.filesystem('gcs')` instantiation
- stamina 25.2.0 — verified via `uv run python`:
  - `stamina.retry_context` signature and parameters
- `/home/jdwh08/projects/cta-eta/src/cta_eta/data_collection/storage_cache/journal_writer.py` — JournalWriter IPC path structure: `{data_path}/{dataset_name}/year=YYYY/month=MM/day=DD/journal_HHMMSS_microseconds.ipc`
- `/home/jdwh08/projects/cta-eta/src/cta_eta/data_collection/storage_cache/storage.py` — existing StorageBackend/CloudStorage pattern using fsspec
- `/home/jdwh08/projects/cta-eta/src/cta_eta/monitoring/cli.py` — existing cta-monitor CLI structure for adding compaction subcommand
- `/home/jdwh08/projects/cta-eta/src/cta_eta/monitoring/alerting.py` — existing email alerting for compaction failure
- `/home/jdwh08/projects/cta-eta/deploy/cta-alerts.timer` + `cta-train-daemon.service` — existing systemd patterns

### Secondary (MEDIUM confidence)

- [systemd.timer man page](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html) — `Persistent=true` + `OnCalendar` catch-up behavior confirmed
- [PyArrow IPC docs v23.0.1](https://arrow.apache.org/docs/python/ipc.html) — RecordBatchStreamReader usage patterns
- [fsspec features docs](https://filesystem-spec.readthedocs.io/en/latest/features.html) — fsspec transaction support, url_to_fs patterns
- [PyArrow Filesystem Interface](https://arrow.apache.org/docs/python/filesystems.html) — FSSpecHandler integration

### Tertiary (LOW confidence)

- None significant — all key claims verified against installed library versions.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages already installed and verified via direct Python introspection
- Architecture: HIGH — IPC read/write pipeline, fsspec bridge, retry pattern all verified with working code
- Pitfalls: HIGH for IPC corruption/schema cases (directly tested); MEDIUM for systemd DST timezone (tested conceptually, systemd version not verified on production host)

**Research date:** 2026-02-24
**Valid until:** 2026-03-24 (pyarrow, fsspec, stamina are stable; systemd behavior stable)
