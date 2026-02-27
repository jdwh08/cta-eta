---
phase: 12-storage-optimization
plan: "02"
subsystem: data-collection
tags: [pyarrow, compaction, schema-validation, drift-detection, parquet, ipc]

# Dependency graph
requires:
  - phase: 12-01
    provides: schema_registry module with DriftResult, classify_drift, bootstrap_registry, load_registry

provides:
  - Schema-drift-aware compaction loop in _compact_one_daemon
  - send_drift_alert helper for email alerting on breaking drift
  - Parquet file-level schema_drift and drift_summary metadata annotation
  - Registry bootstrap after first successful compaction
  - Integration tests for drift detection (4 new test classes, 27 total in file)

affects:
  - 12-03 (monitoring/CLI that reads compaction sidecars and may display drift status)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - continue-on-drift policy: all journals merged regardless of drift type
    - first-occurrence alerting guard: drift_alert_sent prevents duplicate emails per day
    - Parquet metadata annotation: schema_drift=true + drift_summary JSON stored as bytes in metadata
    - registry bootstrap: idempotent call after each successful upload

key-files:
  created: []
  modified:
    - src/cta_eta/data_collection/compaction/compact.py
    - tests/data_collection/compaction/test_compact.py

key-decisions:
  - "continue-on-drift: breaking drift journals are still merged (not skipped) — matches plan spec"
  - "cast breaking columns to registry type before concat; ArrowInvalid cast failures logged and kept as-is"
  - "promote_options=default in concat_tables handles additive drift (new fields filled with null)"
  - "test_schema_mismatch_skips_journal renamed to test_breaking_drift_journal_still_merged to reflect new policy"

patterns-established:
  - "Drift-aware schema check: classify_drift() replaces strict equality skip"
  - "IPC test files use ipc.new_stream (not ipc.new_file) to match read_ipc_with_repair's ipc.open_stream reader"

requirements-completed: []

# Metrics
duration: 5min
completed: 2026-02-27
---

# Phase 12 Plan 02: Schema Registry Integration into Compaction Summary

**Drift-aware compaction loop with per-journal classify_drift, single-alert guard, Parquet schema_drift metadata annotation, and registry bootstrap after first successful run**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-02-27T01:11:40Z
- **Completed:** 2026-02-27T01:16:55Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- Replaced strict schema equality skip in `_compact_one_daemon` with drift-aware `classify_drift` call per journal
- Breaking drift: alerts once (drift_alert_sent guard), casts columns to registry type, continues merging
- Additive drift: logs at INFO level, no alert, merged using `promote_options="default"`
- Merged Parquet annotated with `schema_drift=true` and `drift_summary` JSON bytes on breaking drift
- Registry bootstrapped via `bootstrap_registry` after first successful upload (no-op if exists)
- 4 new integration test classes covering all drift scenarios (27 total tests in file, all pass)

## Task Commits

1. **Task 1: Schema validation loop and drift alerting in compact.py** - `6c963c1` (feat)
2. **Task 2: Integration tests for drift detection in compaction** - `47acc9f` (test)

**Plan metadata:** (final docs commit — see below)

## Files Created/Modified

- `/home/jdwh08/projects/cta-eta/src/cta_eta/data_collection/compaction/compact.py` - Added registry_path load, drift-aware journal loop, drift annotation, bootstrap call, promote_options
- `/home/jdwh08/projects/cta-eta/tests/data_collection/compaction/test_compact.py` - Updated test_schema_mismatch_skips_journal + 4 new drift test classes + _write_temp_ipc helper

## Decisions Made

- **continue-on-drift**: all journals merged regardless of drift type (breaking or additive) — matches "continue-on-drift policy" from plan
- **cast before concat**: breaking-typed columns cast to registry type before concat; `ArrowInvalid` failures log warning and keep as-is (safe fallback)
- **promote_options="default"**: handles additive drift tables by filling new fields with null during concat
- **test updated**: `test_schema_mismatch_skips_journal` renamed to `test_breaking_drift_journal_still_merged` and rewritten to reflect the new merge-on-drift behavior

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test for changed schema-mismatch behavior**
- **Found during:** Task 1 (schema validation loop implementation)
- **Issue:** `test_schema_mismatch_skips_journal` tested the old skip behavior (journals_skipped=1, status=partial). With the new continue-on-drift policy, a breaking-drift journal is merged, not skipped.
- **Fix:** Renamed to `test_breaking_drift_journal_still_merged`; added required mocks (load_registry, upload_parquet, archive_journals, prune_archive, bootstrap_registry); updated assertions to status="success", journals_skipped=0, rows_written=1.
- **Files modified:** `tests/data_collection/compaction/test_compact.py`
- **Verification:** `uv run pytest tests/data_collection/compaction/test_compact.py -v` — 23 tests pass (0 failures after fix)
- **Committed in:** `6c963c1` (Task 1 commit)

**2. [Rule 1 - Bug] Fixed _write_temp_ipc to use ipc.new_stream instead of ipc.new_file**
- **Found during:** Task 2 (integration tests)
- **Issue:** `_write_temp_ipc` used `ipc.new_file` (IPC file format), but `read_ipc_with_repair` uses `ipc.open_stream` (IPC stream format). Result: all test journals reported 0 batches (skipped), tests failed.
- **Fix:** Changed `_write_temp_ipc` to use `ipc.new_stream` with explicit `pa.OSFile` sink, matching the stream format the compaction reader expects.
- **Files modified:** `tests/data_collection/compaction/test_compact.py`
- **Verification:** All 4 new drift test classes pass after fix
- **Committed in:** `47acc9f` (Task 2 commit, same file)

---

**Total deviations:** 2 auto-fixed (2x Rule 1 - Bug)
**Impact on plan:** Both fixes essential for test correctness. First reflects the intentional behavior change from the plan. Second fixes an IPC format mismatch in the test helper. No scope creep.

## Issues Encountered

None beyond the two auto-fixed deviations documented above.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Schema enforcement runtime is complete: classify_drift runs per journal, alerts fire on breaking drift, Parquet files carry drift metadata, registry bootstraps on first run
- Ready for Phase 12 Plan 03 (operational integration / CLI schema update subcommand)
- The `src/cta_eta/schemas/` directory exists and is the correct location for registry JSON files (created in Phase 12-01)

---
*Phase: 12-storage-optimization*
*Completed: 2026-02-27*

## Self-Check: PASSED

- FOUND: src/cta_eta/data_collection/compaction/compact.py
- FOUND: tests/data_collection/compaction/test_compact.py
- FOUND: .planning/phases/12-storage-optimization/12-02-SUMMARY.md
- FOUND commit: 6c963c1 (feat: schema registry integration)
- FOUND commit: 47acc9f (test: drift detection tests)
