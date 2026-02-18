---
phase: 10-journal-writer
plan: 01
subsystem: storage
provides: [JournalWriter, create_journal_writer]
affects: [10-02, 10-03]
tech-stack:
  added: [pyarrow.ipc]
  patterns: [JournalWriter append + rotate, IPC stream via pa.ipc.new_stream, hive-style IPC paths]
key-files:
  - src/cta_eta/data_collection/storage_cache/journal_writer.py
  - tests/data_collection/storage_cache/test_journal_writer.py
key-decisions:
  - JournalWriter is standalone (not StorageBackend subclass)
  - Schema inferred from first batch, reused on rotation
  - Local-only writes (cloud upload is Phase 11)
  - No metadata param on append_batch (gap info logged separately)
  - Filename uses microseconds (HHMMSS_μs) to guarantee uniqueness within same second
---

# Phase 10 Plan 01: JournalWriter Summary

**Implemented JournalWriter — Arrow IPC stream writer with time-based rotation and hive-style paths, replacing per-poll Parquet writes.**

## Accomplishments

- Implemented `JournalWriter` class with `append_batch()`, `rotate()`, and `close()` backed by `pa.ipc.new_stream()`
- Implemented `create_journal_writer(config)` factory reading `data_path` and `journal_rotation_minutes` from `config[storage]`
- Added `journal_rotation_minutes = 15` to `config.toml` under `[storage]`
- 16 tests written and passing (100% line coverage on `journal_writer.py`); basedpyright strict mode: 0 errors

## Files Created/Modified

- `src/cta_eta/data_collection/storage_cache/journal_writer.py` - JournalWriter class + factory
- `tests/data_collection/storage_cache/test_journal_writer.py` - TDD tests (16 tests)
- `config.toml` - Added `journal_rotation_minutes = 15` under `[storage]`

## Decisions Made

- **Microseconds in filename**: The plan specified `journal_HHMMSS.ipc` but rotate+append within the same second produced a filename collision. Added `%f` (microseconds) to yield `journal_HHMMSS_μs.ipc`, ensuring uniqueness while preserving the intent. This is an auto-fix for a blocking test failure.
- **`_sink` stored separately**: `pa.OSFile` must be closed independently after `pa.ipc.RecordBatchWriter.close()` to release the file handle. Both are stored as instance attributes (`_writer`, `_sink`).

## Issues Encountered

- One test (`test_rotate_closes_current_and_next_append_opens_new`) initially failed because rotate + re-open within the same second produced the same HHMMSS filename, so `file_before_rotate == file_after_rotate`. Fixed by adding microseconds to the filename format.

## Next Step

Ready for 10-02-PLAN.md and 10-03-PLAN.md (parallel daemon refactors)
