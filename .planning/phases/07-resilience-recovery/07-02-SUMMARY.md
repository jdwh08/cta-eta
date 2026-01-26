---
phase: 07-resilience-recovery
plan: 02
type: tdd
subsystem: infra
tags: [gap-detection, data-quality, parquet-metadata, tdd, temporal-coverage]

# Dependency graph
requires:
  - phase: 07-resilience-recovery
    plan: 01
    provides: CTA error handling and retry logic
provides:
  - Gap detection algorithm with threshold-based logic
  - Parquet file metadata support for gap flagging
  - TrainPositionDaemon integration for real-time gap detection
affects: [07-03-graceful-shutdown]

# Tech tracking
tech-stack:
  added: []
  patterns: [threshold-based-gap-detection, parquet-schema-metadata, tdd-red-green-refactor]

key-files:
  created:
    - src/cta_eta/data_collection/orchestration/gap_detection.py
    - tests/data_collection/orchestration/test_gap_detection.py
  modified:
    - src/cta_eta/data_collection/orchestration/train_position_daemon.py
    - src/cta_eta/data_collection/storage_cache/storage.py

key-decisions:
  - "Threshold-based gap detection: Delta > (poll_interval * threshold_multiplier) triggers gap"
  - "Heuristic gap reason: <10 min = retry_exhausted, >=10 min = downtime"
  - "Missed poll cycles calculation: floor(delta // poll_interval) for accurate count"
  - "Gap metadata attached to next successful Parquet write (file-level, not separate batch)"
  - "Metadata stored in PyArrow schema metadata as JSON-encoded bytes"

patterns-established:
  - "Threshold-based gap detection with configurable multiplier"
  - "Heuristic classification of gap types based on duration"
  - "Pending metadata pattern: detect gap → set pending → attach on next write → clear"
  - "Parquet schema metadata for dataset-level annotations"

issues-created: []

# Metrics
duration: 10min
completed: 2026-01-26
---

# Phase 7 Plan 2: Gap Detection and Metadata (TDD) Summary

**Implemented threshold-based gap detection with Parquet metadata flagging to ensure complete temporal coverage transparency for downstream ML**

## TDD Cycle

**RED - Failing Test:**
- Comprehensive test suite with 17 test cases covering all scenarios and edge cases
- Tests for no gap, retry exhaustion, downtime, multiple intervals, edge cases, input validation
- Failed as expected: ModuleNotFoundError for gap_detection module
- Commit: `ab8d510` test(07-02): add failing tests for gap detection

**GREEN - Implementation:**
- Created detect_gap() function with threshold-based algorithm
- Integrated into TrainPositionDaemon with pending_gap_metadata pattern
- Extended ParquetWriter with metadata support (JSON-encoded in schema metadata)
- All 17 tests passing with 100% coverage on gap_detection.py
- All 23 storage tests passing (backward compatible)
- Commit: `7ff17fd` feat(07-02): implement gap detection with metadata

**REFACTOR:**
- Code reviewed for improvements
- Implementation already clean with minimal repetition
- No refactoring needed - skipped this phase

## Commits

1. **test(07-02)**: add failing tests for gap detection - `ab8d510`
2. **feat(07-02)**: implement gap detection with metadata - `7ff17fd`

## Implementation Details

**Gap Detection Algorithm:**
- Threshold = poll_interval × threshold_multiplier (default 2.0)
- Gap occurs when delta > threshold (strict inequality)
- First poll ever (last_poll_timestamp = None or 0) → no gap
- Input validation: rejects negative delta, zero/negative intervals

**Gap Reason Heuristic:**
- delta < 600 seconds (10 minutes) → "retry_exhausted"
- delta >= 600 seconds → "downtime"

**Missed Poll Cycles:**
- Calculated as int(delta // poll_interval)
- Represents number of poll cycles that would have been scheduled in (last_poll, current]

**TrainPositionDaemon Integration:**
- Call detect_gap() at start of each collection cycle
- If gap detected: log warning and set pending_gap_metadata
- On successful Parquet write: pass pending_gap_metadata to storage
- Clear pending_gap_metadata after successful write
- Gap metadata attaches to next successful batch (not a separate write)

**ParquetWriter Metadata Support:**
- Extended write() and append_batch() with optional metadata parameter
- Metadata dict → JSON-encode values → bytes for PyArrow schema metadata
- Stored at file level (not row level) for efficiency
- Merges with existing schema metadata if present

## Test Coverage

- 17/17 tests passing for gap detection
- 100% coverage on gap_detection.py
- All existing storage tests passing (backward compatible)
- Edge cases covered:
  - No gap (within threshold)
  - Exactly at threshold (not a gap)
  - First poll ever
  - Zero last_poll_timestamp
  - Downtime boundary (exactly 10 minutes)
  - Partial intervals (floor division)
  - Custom threshold multiplier
  - Input validation errors

## Next Phase Readiness

- Gap detection complete and tested
- Metadata attachment pattern established
- Ready for 07-03-PLAN.md (Graceful shutdown and restart recovery)
- State persistence will use gap detection on daemon restart

---
*Phase: 07-resilience-recovery*
*Completed: 2026-01-26*
