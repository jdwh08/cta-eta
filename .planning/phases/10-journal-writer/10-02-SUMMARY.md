---
phase: 10-journal-writer
plan: 02
subsystem: orchestration
requires: [10-01]
provides: [TrainPositionDaemon-journal]
affects: []
tech-stack:
  added: []
  patterns: [daemon storage swap pattern]
key-files:
  - src/cta_eta/data_collection/orchestration/train_position_daemon.py
key-decisions:
  - pending_gap_metadata retained on daemon for logging; not passed to JournalWriter
  - _pre_shutdown_hook overridden to call storage.close() directly (JournalWriter has close, not flush)
  - Test file updated to match new JournalWriter API (no metadata= kwarg on append_batch)
---

# Phase 10 Plan 02: TrainPositionDaemon Refactor Summary

**Swapped ParquetWriter for JournalWriter in TrainPositionDaemon, removing all Parquet write path references and adding clean IPC journal teardown.**

## Accomplishments

- Replaced `create_parquet_writer`/`ParquetWriter` with `create_journal_writer`/`JournalWriter` in all imports, type annotations, `__init__`, and docstrings
- Dropped `metadata=` kwarg from `append_batch` call — JournalWriter has no metadata param; gap info is logged separately (pending_gap_metadata field retained on daemon for logging)
- Added `_pre_shutdown_hook` override calling `self.storage.close()` to write the IPC EOS marker on clean shutdown (base class hook looks for `flush`, JournalWriter has `close`)
- Updated 7 test patches from `create_parquet_writer` to `create_journal_writer` and updated 5 test assertions that checked for `metadata=` in `append_batch` call kwargs; all 775 tests pass

## Files Created/Modified

- `src/cta_eta/data_collection/orchestration/train_position_daemon.py` - Swapped ParquetWriter → JournalWriter; added `_pre_shutdown_hook` override
- `tests/data_collection/orchestration/test_train_position_daemon.py` - Updated mock patches and assertions for JournalWriter API

## Decisions Made

- **`_pre_shutdown_hook` override**: The base `AsyncBaseDaemon._pre_shutdown_hook` looks for a `flush` method on `self.storage`. JournalWriter has `close()` not `flush()`. Rather than relying on the generic base hook to silently warn "Storage has no flush method", a `_pre_shutdown_hook` override was added to `TrainPositionDaemon` that calls `self.storage.close()` directly, ensuring the IPC EOS marker is always written on clean shutdown.
- **Test file as deviation**: The plan specified only `train_position_daemon.py` as a file to modify, but the existing test file patched `create_parquet_writer` and asserted on `metadata=` in `append_batch`. These tests would have been broken without updating them. Fixed immediately per Rule 1 (auto-fix bug/blocker).

## Issues Encountered

- Test file `test_train_position_daemon.py` had 7 patches of `create_parquet_writer` (now `create_journal_writer`) and 5 assertions checking for `metadata=` kwarg in `append_batch` calls (now removed from JournalWriter API). Fixed immediately — all 775 tests pass after update.

## Next Step

10-03 (weather daemon refactor) can run in parallel. Both complete → Phase 10 done.
