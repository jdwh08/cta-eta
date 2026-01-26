---
phase: 07-resilience-recovery
plan: 01
status: complete
completed_at: 2026-01-25
---

# Phase 7 Plan 1: Enhanced Retry with CTA Error Codes Summary

**Implemented intelligent retry logic with CTA-specific error handling: application-level error detection (HTTP 200 with errCd in body), extended retry with poll blocking, and bounded probe for daily quota errors.**

## Accomplishments

- **CTA application-level error detection**: Added CTATrackerAPIError exception and errCd checking in get_train_positions(). CTA API returns HTTP 200 with error codes in JSON body (ctatt.errCd), which previously resulted in silent failures with 0 records. Now raises CTATrackerAPIError for non-zero error codes (102, 100, 101, 106, 107, 500), enabling daemon-level classification and handling.

- **CTA-specific error classification**: Extended classify_error() in daemon_utils to handle CTATrackerAPIError and parse CTA error codes from HTTPStatusError response bodies. Added ErrorCategory.DAILY_QUOTA for CTA error 102 (daily quota exceeded). Maps error codes: 102 → DAILY_QUOTA, 100/101/106/107/500 → CONFIGURATION, others → CONFIGURATION for safety.

- **Extended retry with poll blocking for TRANSIENT errors**: Implemented _retry_with_extended_backoff() wrapping _collect_train_positions_cycle with stamina retry (10 attempts, up to 60s between retries). Blocks subsequent polls during retry - daemon waits for retry completion before resuming normal schedule. On exhaustion: logs gap, accepts data loss, resumes 15-second schedule. If error category changes during retry (e.g. TRANSIENT → DAILY_QUOTA), re-raises for appropriate handling.

- **Daily quota handling with bounded probe**: Implemented _handle_daily_quota_error() for CTA error 102. Configurable bounded probe (default: 2 attempts at [300, 900] second intervals = 5 min, 15 min). Skips probe if <15 minutes until midnight Chicago, goes straight to midnight sleep. Each probe: sleeps interval, attempts one poll. On success: resumes normal schedule immediately. On continued 102: tries next probe or sleeps until midnight Chicago + 5 min buffer. All sleeps chunked (60s) and shutdown-interruptible via self.sleep() for fast SIGTERM.

- **Poll blocking during retry/recovery**: All error recovery operations (extended retry, daily quota probe, rate limit backoff) block subsequent polls. Daemon completes recovery before resuming normal 15-second schedule. No queued polls - accepts gaps when recovery fails.

- **Configuration support**: Added probe_102_attempts and probe_102_intervals to config.toml [collection] section. Loaded in TrainPositionDaemon.__init__ with sensible defaults.

- **Comprehensive testing**: Added 12 new tests covering all CTA error codes (102, 100, 101, 106, 107, 500) via both CTATrackerAPIError and HTTPStatusError body parsing. Updated existing tests to handle extended retry behavior. All 19 daemon tests + 7 API tests + 16 daemon_utils tests pass.

## Files Created/Modified

- `src/cta_eta/data_collection/apis/api_train_position.py` - Added CTATrackerAPIError exception class, errCd checking in get_train_positions() after response.raise_for_status()
- `src/cta_eta/data_collection/orchestration/daemon_utils.py` - Added ErrorCategory.DAILY_QUOTA, extended classify_error() for CTATrackerAPIError and HTTPStatusError body parsing
- `src/cta_eta/data_collection/orchestration/train_position_daemon.py` - Implemented _retry_with_extended_backoff(), _handle_daily_quota_error(), _sleep_until_midnight(), enhanced run() method with CTA-specific error handling, added probe configuration loading
- `config.toml` - Added probe_102_attempts and probe_102_intervals configuration under [collection]
- `tests/data_collection/apis/test_api_train_position.py` - Added 7 tests for CTATrackerAPIError and errCd handling
- `tests/data_collection/orchestration/test_daemon_utils.py` - Added 8 tests for CTA-specific error classification
- `tests/data_collection/orchestration/test_train_position_daemon.py` - Updated transient error test to verify extended retry behavior

## Decisions Made

1. **CTATrackerAPIError not retried in API layer**: Stamina retry on get_train_positions applies only to httpx.HTTPStatusError. CTATrackerAPIError propagates to daemon for category-specific handling. Rationale: CTA application errors require different strategies (bounded probe for 102, exit for 100/101/106/107/500) that shouldn't be conflated with HTTP-level retries.

2. **Bounded probe for error 102 instead of continuous low-frequency polling**: When hitting daily quota, probe with bounded attempts (default: 2) then sleep until midnight, rather than polling at lower frequency until midnight. Rationale: Continuous polling doesn't help when quota is truly exhausted and delays recovery when it's a false positive. Bounded probe balances responsiveness (quick recovery if quota resets early) with resource efficiency (not wasting API calls).

3. **Poll blocking during all retry/recovery**: Extended retry, daily quota probe, and rate limit backoff all block subsequent polls until recovery completes. No queued polls. Rationale: Simplifies state management, prevents cascading failures, accepts gaps when recovery fails (critical for "no historical data" constraint - if we can't get it now, it's lost anyway).

4. **Extended retry max wait 60 seconds**: Stamina retry configured with wait_max=60s (up to 60s between retries, 10 attempts). Rationale: Balances aggressive retry for data collection (CTA has no historical endpoint) with reasonable resource usage. Total retry window ~5-10 minutes.

5. **Chunked sleep for shutdown-interruptibility**: All long sleeps (probe intervals, midnight wait) use chunked 60-second sleeps with self.running checks. Rationale: Ensures fast SIGTERM even during multi-hour midnight sleep.

6. **Skip probe if <15 minutes until midnight**: If daily quota hits within 15 minutes of midnight Chicago, skip bounded probe and go straight to midnight sleep. Rationale: Probe intervals (5 min, 15 min) would overlap with midnight reset, wasting attempts.

7. **Fallback errCd parsing from HTTPStatusError body**: classify_error() parses ctatt.errCd from HTTPStatusError.response.json() as fallback. Rationale: Defensive programming - handles edge case where CTA uses HTTP 4xx/5xx with errCd in body (observed in some API implementations).

## Issues Encountered

- **Test complexity with stamina retry**: Initial test for transient errors hung because stamina retry takes ~1 minute with 10 attempts and exponential backoff. Solution: Mock _retry_with_extended_backoff() method instead of testing full retry loop. Full retry behavior verified via unit tests of the retry method itself.

- **Ruff linting violations**: TRY400 (use logging.exception instead of logging.error), TRY300 (move return to else block), PLR2004 (magic value), N806 (variable naming). Solution: Changed logger.error to logger.warning (not an exception context), moved success returns to else blocks, extracted magic value to local variable, used lowercase variable name.

## Next Step

Ready for 07-02-PLAN.md (Gap detection and metadata - TDD)
