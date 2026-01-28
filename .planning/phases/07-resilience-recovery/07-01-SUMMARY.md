---
phase: 07-resilience-recovery
plan: 01
subsystem: infra
tags: [error-handling, retry-logic, cta-api, stamina, asyncio]

# Dependency graph
requires:
  - phase: 06-train-polling
    provides: TrainPositionDaemon with basic polling and error classification
provides:
  - CTA-specific error detection and classification
  - Extended retry logic with poll blocking
  - Daily quota handling with bounded probe and midnight sleep
  - Shutdown-interruptible long sleeps
affects: [07-02-gap-detection, 07-03-graceful-shutdown]

# Tech tracking
tech-stack:
  added: []
  patterns: [bounded-probe-before-backoff, poll-blocking-retry, chunked-interruptible-sleep]

key-files:
  created: []
  modified:
    - src/cta_eta/data_collection/apis/api_train_position.py
    - src/cta_eta/data_collection/orchestration/daemon_utils.py
    - src/cta_eta/data_collection/orchestration/train_position_daemon.py
    - config.toml

key-decisions:
  - "Error detection in API layer: CTA returns HTTP 200 with errCd in JSON. Raise CTATrackerAPIError in get_train_positions() to ensure errors never produce silent 0 records"
  - "Bounded probe before midnight sleep: Error 102 could be transient. Use configurable probe (2 attempts at 5/15 min) before committing to sleep until midnight"
  - "Poll blocking during retry: All retry strategies block subsequent polls until resolved to prevent cascading failures"

patterns-established:
  - "CTATrackerAPIError for application-level API errors (HTTP 200 with errCd)"
  - "Bounded probe pattern: configurable attempts/intervals before long backoff"
  - "Chunked sleeps with self.running checks for shutdown-interruptible waits"

issues-created: []

# Metrics
duration: 3min
completed: 2026-01-26
---

# Phase 7 Plan 1: Enhanced Retry with CTA Error Codes Summary

**Implemented intelligent retry logic with CTA-specific error classification, extended backoff, and daily quota handling**

## Performance

- **Duration:** 3 min
- **Started:** 2026-01-26T15:35:50Z
- **Completed:** 2026-01-26T15:38:52Z
- **Tasks:** 3
- **Files modified:** 7

## Accomplishments

- Added CTATrackerAPIError exception class to capture CTA application-level errors (errCd, errNm) from HTTP 200 responses
- Enhanced get_train_positions() to detect and raise CTATrackerAPIError when ctatt.errCd is non-zero, preventing silent 0-record responses
- Extended ErrorCategory enum with DAILY_QUOTA for CTA error 102 (daily quota exceeded)
- Implemented CTA-specific error classification in classify_error() for all 6 CTA error codes (102, 100, 101, 106, 107, 500)
- Added HTTPStatusError body parsing fallback to detect CTA errCd in non-200 responses
- Implemented extended retry with stamina (5-10 min backoff) for TRANSIENT errors with poll blocking
- Created bounded probe strategy for DAILY_QUOTA (error 102) with configurable attempts and intervals
- Implemented sleep-until-midnight Chicago time for persistent 102 errors with chunked, shutdown-interruptible sleeps
- Added probe_102_attempts and probe_102_intervals configuration to config.toml
- All error handling preserves poll blocking (no queued missed polls), accepts gaps, and resumes 15-second schedule

## Task Commits

Each task was committed as part of a cohesive implementation:

1. **Tasks 1a, 1b, 2: CTA error detection, classification, and extended retry** - `338a6e2` (feat)

_Note: Implementation done as single cohesive commit due to tight integration_

## Files Created/Modified

- `src/cta_eta/data_collection/apis/api_train_position.py` - CTATrackerAPIError class, errCd detection and raising in get_train_positions()
- `src/cta_eta/data_collection/orchestration/daemon_utils.py` - DAILY_QUOTA category, classify_error() CTA code handling, HTTPStatusError body parsing
- `src/cta_eta/data_collection/orchestration/train_position_daemon.py` - Extended retry (_retry_with_extended_backoff), DAILY_QUOTA handler (_handle_daily_quota_error), bounded probe, midnight sleep, run() error routing
- `config.toml` - Added [collection] probe_102_attempts and probe_102_intervals configuration
- `tests/data_collection/apis/test_api_train_position.py` - Tests for CTATrackerAPIError, all 6 CTA error codes, errCd=0 and missing handling
- `tests/data_collection/orchestration/test_daemon_utils.py` - Tests for CTA error classification (102, 100/101/106/107/500), HTTPStatusError body parsing
- `tests/data_collection/orchestration/test_train_position_daemon.py` - Tests for TRANSIENT extended retry, DAILY_QUOTA handling, CONFIGURATION exit, run loop error routing

## Decisions Made

**Error detection in API layer:** CTA returns HTTP 200 with errCd in JSON body. Detect and raise CTATrackerAPIError in get_train_positions() (not in normalize) to ensure API errors never silently produce 0 records and can be classified by the daemon.

**No retry in API for CTATrackerAPIError:** stamina.retry on get_train_positions applies only to httpx.HTTPStatusError. CTATrackerAPIError propagates to daemon for category-specific handling (DAILY_QUOTA gets bounded probe + midnight sleep, CONFIGURATION exits).

**Bounded probe before midnight sleep:** Error 102 could be a false positive (transient API issue). Use configurable bounded probe (default: 2 attempts at 5 min, 15 min intervals) before committing to sleep until midnight. Skip probe if <15 minutes until midnight to avoid wasted time.

**Poll blocking during retry:** All retry strategies (TRANSIENT extended backoff, DAILY_QUOTA probe, midnight sleep) block subsequent polls until resolved. This prevents cascading failures and maintains 15-second schedule fidelity when recovery succeeds.

**Chunked shutdown-interruptible sleeps:** All long sleeps (probe intervals, midnight wait) use self.sleep() or chunked asyncio.sleep with self.running checks so SIGTERM can complete gracefully during extended waits.

**Midnight calculation in Chicago time:** CTA daily quota resets at midnight America/Chicago (not UTC). Use ZoneInfo("America/Chicago") for all quota reset calculations with 5-minute buffer to account for clock skew.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## Next Phase Readiness

- Enhanced retry logic complete and tested (66 tests passing)
- Ready for 07-02-PLAN.md (Gap detection with TDD)
- CTA error handling enables 24/7 operation within API limits

---
*Phase: 07-resilience-recovery*
*Completed: 2026-01-26*
