---
phase: 11-data-validation-cleaning
plan: "01"
subsystem: data_collection/compaction
tags: [ipc, reader, schema, tdd, pyarrow, compaction]
dependency_graph:
  requires: []
  provides:
    - src/cta_eta/data_collection/compaction/__init__.py
    - src/cta_eta/data_collection/compaction/schemas.py
    - src/cta_eta/data_collection/compaction/ipc_reader.py
  affects:
    - "11-02: compaction pipeline builds on discover_journals + read_ipc_with_repair"
tech_stack:
  added: []
  patterns:
    - pyarrow IPC stream batch-by-batch reading with ArrowInvalid/OSError catch
    - hive-style path structure for journal discovery (matches JournalWriter)
key_files:
  created:
    - src/cta_eta/data_collection/compaction/__init__.py
    - src/cta_eta/data_collection/compaction/schemas.py
    - src/cta_eta/data_collection/compaction/ipc_reader.py
    - tests/data_collection/compaction/__init__.py
    - tests/data_collection/compaction/test_ipc_reader.py
  modified: []
decisions:
  - "Catch OSError in addition to ArrowInvalid in read_ipc_with_repair: pyarrow 22.0.0 raises OSError for body read failures during truncation, not only ArrowInvalid"
  - "Crash files (no EOS marker) return was_clean=True: verified pyarrow StopIteration behavior is correct, not ArrowInvalid"
  - "poll_timestamp stored as pa.timestamp('us', tz='UTC') matching pyarrow inference from Python datetime"
metrics:
  duration_seconds: 361
  completed_date: "2026-02-25"
  tasks_completed: 3
  files_created: 5
  files_modified: 0
---

# Phase 11 Plan 01: IPC Reader (schemas.py + ipc_reader.py) Summary

TDD implementation of pyarrow schema constants and IPC reader module providing file discovery and partial-repair reading as the foundational data layer for the Phase 11 compaction pipeline.

## What Was Built

### schemas.py

Defines two pyarrow Schema constants derived from daemon source code:

- `TRAIN_POSITION_SCHEMA` (15 fields): matches `normalize_train_positions()` output — `poll_timestamp` (timestamp[us, tz=UTC]), `api_timestamp`, `route`, `train_id`, `lat`/`lon` (float64), `heading` (int64), station/destination name+id fields, `prediction_time`, `predicted_arrival_time`, `is_approaching`/`is_delayed` (bool)
- `WEATHER_SCHEMA` (24 fields): matches `WeatherDaemon._merge_station_weather()` output — station identity fields, NWS forecast fields (temperature, humidity, wind, precip), Open-Meteo supplementary fields (visibility, snow depth, pressure, gusts, apparent temp, rain/showers/snowfall), `collection_timestamp` (float64)

### ipc_reader.py

Two functions:

**`discover_journals(data_path, dataset_name, target_date) -> list[Path]`**
- Uses `pathlib.Path.glob("journal_*.ipc")` on hive-style day directory
- Returns sorted list (chronological by filename) or `[]` for missing directories
- Path format exactly matches JournalWriter: `{data_path}/{dataset_name}/year=YYYY/month=MM/day=DD/`

**`read_ipc_with_repair(path) -> tuple[list[pa.RecordBatch], bool]`**
- Opens stream with `ipc.open_stream()`, reads batch-by-batch
- Handles all 5 cases: clean (EOS), crash (no-EOS/StopIteration), truncated (ArrowInvalid), corrupt body (OSError), corrupt header, empty
- `was_clean=True` for clean + crash files; `was_clean=False` for all corruption cases

## TDD Cycle

**RED** (commit `79f4fd7`): 21 failing tests covering all 5 repair cases, 4 discovery cases, schema validation — import fails because modules don't exist yet.

**GREEN** (commit `285a3b4`): Implemented `schemas.py` and `ipc_reader.py`. Discovered that corrupt trailing bytes test had wrong assumption (appending after EOS vs truncating mid-file) and that pyarrow raises `OSError` (not just `ArrowInvalid`) for body read failures — fixed both.

**REFACTOR** (commit `55a7c12`): Minor docstring correction only — updated "three cases" to "five cases" in `read_ipc_with_repair`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Caught OSError in addition to ArrowInvalid in read_ipc_with_repair**
- **Found during:** GREEN phase testing
- **Issue:** RESEARCH.md stated ArrowInvalid for all corruption cases, but pyarrow 22.0.0 raises `OSError` ("Expected to be able to read N bytes for message body, got M") for body read failures at 60% file truncation. Only `ArrowInvalid` is raised for metadata/header corruption.
- **Fix:** Added `OSError` to the except clause in the batch-reading loop; updated test helper to use 80% truncation (where ArrowInvalid is raised instead) to reliably salvage first batch in tests
- **Files modified:** `src/cta_eta/data_collection/compaction/ipc_reader.py`, `tests/data_collection/compaction/test_ipc_reader.py`
- **Commit:** 285a3b4

**2. [Rule 1 - Bug] Fixed corrupt trailing bytes test helper**
- **Found during:** GREEN phase testing
- **Issue:** Original `_write_corrupt_trailing_ipc()` appended bytes after the EOS marker — pyarrow stops reading at EOS so ignores trailing garbage, returning `was_clean=True`. This is not the "corrupt trailing bytes" case described in RESEARCH.md.
- **Fix:** Changed helper to truncate file mid-stream (at 80%) instead of appending after EOS, which correctly triggers ArrowInvalid during batch reading
- **Files modified:** `tests/data_collection/compaction/test_ipc_reader.py`
- **Commit:** 285a3b4

## Verification

- `uv run pytest tests/data_collection/compaction/test_ipc_reader.py -v`: 21/21 passed
- `uv run basedpyright src/cta_eta/data_collection/compaction/`: 0 errors, 0 warnings, 0 notes
- `uv run pytest`: 796/796 passed (no regressions)
- Import verification: both modules importable, `TRAIN_POSITION_SCHEMA` prints 15-field schema

## Self-Check: PASSED

All created files exist. All task commits verified. All 796 tests pass. 0 basedpyright errors.
