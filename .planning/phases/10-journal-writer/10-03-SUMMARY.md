---
phase: 10-journal-writer
plan: 03
subsystem: orchestration
requires: [10-01]
provides: [WeatherDaemon-journal]
affects: []
tech-stack:
  added: []
  patterns: [daemon storage swap pattern]
key-files:
  - src/cta_eta/data_collection/orchestration/weather_daemon.py
  - tests/data_collection/orchestration/test_weather_daemon.py
key-decisions:
  - Weather write interval (30 min) > journal rotation (15 min): each poll may open new journal (correct)
  - _pre_shutdown_hook override calls storage.close() to flush IPC EOS marker on shutdown
---

# Phase 10 Plan 03: WeatherDaemon Refactor Summary

**Swapped ParquetWriter for JournalWriter in WeatherDaemon, completing the clean break from per-poll Parquet writes for the weather daemon.**

## Accomplishments

- Replaced `create_parquet_writer` import with `create_journal_writer` from `journal_writer` module
- Updated TYPE_CHECKING import: `ParquetWriter` → `JournalWriter`
- Updated class attribute annotation: `storage: ParquetWriter` → `storage: JournalWriter`
- Updated `__init__` to call `create_journal_writer(config)` instead of `create_parquet_writer(config)`
- Added `_pre_shutdown_hook` override to call `self.storage.close()` on daemon shutdown, ensuring IPC EOS marker is written cleanly
- Updated log messages in `_store_merged_records` to say "IPC journal" instead of "Parquet"
- Fixed test fixture to mock `create_journal_writer` (removed reference to deleted `create_parquet_writer`)
- 16 weather daemon tests pass; 730 total tests pass (excluding 10-02's train daemon tests which have pre-existing mock issues from parallel work)

## Files Created/Modified

- `src/cta_eta/data_collection/orchestration/weather_daemon.py` - Swapped ParquetWriter → JournalWriter, added _pre_shutdown_hook
- `tests/data_collection/orchestration/test_weather_daemon.py` - Fixed mock to use create_journal_writer

## Decisions Made

- **_pre_shutdown_hook override**: The base class `_pre_shutdown_hook` tries to call `storage.flush()`, which JournalWriter doesn't implement. Added an override to call `self.storage.close()` directly, which calls `rotate()` to flush the IPC EOS marker.
- **append_batch call unchanged**: The existing call `self.storage.append_batch(merged_records, dataset_name="weather")` was already compatible with JournalWriter's API (no metadata param was used).

## Issues Encountered

- **Test mock pointing to removed function**: `test_weather_daemon.py` mocked `create_parquet_writer` which no longer exists in `weather_daemon.py`. Auto-fixed per Rule 1: updated mock to patch `create_journal_writer` instead.
- **Parallel 10-02 work**: `test_train_position_daemon.py` tests fail due to 10-02's uncommitted changes to `train_position_daemon.py` (mock still references `create_parquet_writer` but 10-02's partial work already removed that import). This is 10-02's responsibility to fix; all weather daemon and other tests pass.

## Next Step

Phase 10 complete. Both daemons write IPC journals. Ready for Phase 11: Data Compaction.
