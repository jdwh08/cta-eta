---
phase: 06-train-polling
plan: 02
completed: 2026-01-25
commits: [bf10d3e, a999ad4, 823958d]
---

# Phase 6 Plan 2: Rate Limiting and Integration Validation Summary

**Rate limiting integrated with aiometer and end-to-end polling validated with successful 5-minute continuous run**

## Accomplishments

- **Aiometer rate limiting integrated**: Wrapped CTA API calls in `aiometer.run_all()` with config-driven rate limits (0.1 req/sec, max 3 concurrent) to enforce CTA's 50k/day limit
- **Critical bug fixed**: Resolved XML-to-JSON normalization issue where single-train responses were returned as dict instead of list, causing AttributeError
- **Python 3.13 compatibility fixed**: Corrected datetime.UTC import to use module-level `datetime.UTC` instead of `datetime.datetime.UTC`
- **5-minute integration test passed**: Daemon successfully completed 20 polling cycles, collecting 1,038 train position records with graceful shutdown on SIGTERM
- **Parquet storage validated**: 20 files created with correct schema (poll_timestamp, api_timestamp, route, train_id, lat, lon, heading, etc.) in Hive-partitioned structure
- **State persistence verified**: Daemon state correctly persisted across cycles with accurate metrics (last_poll_timestamp, total_records_collected=1038, current_poll_count=20)
- **Diagnostic tracking confirmed**: aiometer events logged with rate limit parameters, span timing recorded for all API calls

## Files Created/Modified

- `src/cta_eta/data_collection/orchestration/train_position_daemon.py` - Added aiometer rate limiting to `_collect_train_positions_cycle()`, fixed datetime.UTC import
- `src/cta_eta/data_collection/apis/api_train_position.py` - Fixed single-train XML-to-JSON dict normalization in `normalize_train_positions()`

## Commits

- `bf10d3e` - refactor(06-02): add aiometer rate limiting to CTA API calls
- `a999ad4` - fix(06-02): correct datetime.UTC import for Python 3.13
- `823958d` - fix(06-02): handle single-train XML-to-JSON dict normalization

## Decisions Made

**Why use aiometer for single API calls:**
- Enforces config-driven rate limiting consistently across all daemon operations
- Provides standardized diagnostic event tracking for monitoring and debugging
- Maintains pattern consistency with WeatherDaemon's multi-source polling approach
- Future-proofs architecture for potential multi-line batching or parallel requests
- Even single calls benefit from max_per_second enforcement to prevent rate limit violations

**Normalization fix approach:**
- Used `isinstance(trains_raw, list)` check to normalize single-train dict responses to list
- Preserves existing iteration logic without major refactoring
- Handles edge case where CTA Train Tracker API returns different structures based on train count

## Issues Encountered

**Issue 1: datetime.UTC import error**
- **Problem**: `datetime.datetime.UTC` attribute not available even in Python 3.13.11
- **Root cause**: `UTC` is a module-level constant (`datetime.UTC`), not a class attribute
- **Solution**: Changed import from `from datetime import datetime, timezone` to `import datetime`, then used `datetime.UTC`

**Issue 2: Single-train XML-to-JSON normalization**
- **Problem**: AttributeError when Purple line and Yellow line had only one train (dict instead of list)
- **Root cause**: XML-to-JSON conversion returns dict for single-element arrays, list for multiple elements
- **Solution**: Added normalization logic to wrap single-train dict in list before iteration
- **Impact**: Initial 5-minute test only completed 5 cycles before discovering bug; fixed and re-ran successfully

## Next Phase Readiness

**Phase 6 complete. Ready for Phase 7: Resilience & Recovery**

Train position polling daemon is production-ready:
- ✅ Continuous 15-second polling (20 cycles in 5 minutes)
- ✅ Rate limiting enforced (0.1 req/sec = 8,640/day, well under 50k/day budget)
- ✅ Parquet storage with daily partitions (1,038 records stored)
- ✅ State persistence across restarts (last_poll_timestamp, total_records_collected, current_poll_count tracked)
- ✅ Graceful shutdown on signals (SIGTERM triggers clean shutdown with final state save)
- ✅ Diagnostic spans and event tracking (aiometer_run events, cta.get_train_positions spans logged)
- ✅ Error classification and handling (transient errors logged, configuration errors exit gracefully)

Next phase will add intelligent retry logic, gap detection, backfill mechanisms, and enhanced recovery from transient failures.
