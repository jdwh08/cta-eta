# Codebase Concerns

**Analysis Date:** 2026-02-28

## Tech Debt

**Large Daemon Files (Code Complexity):**
- Issue: `train_position_daemon.py` (891 lines) and `weather_daemon.py` (844 lines) are monolithic with multiple responsibilities bundled into single classes
- Files: `src/cta_eta/data_collection/orchestration/train_position_daemon.py`, `src/cta_eta/data_collection/orchestration/weather_daemon.py`
- Impact: Makes testing individual behaviors difficult, increases maintenance burden, harder to debug specific features in isolation
- Fix approach: Extract polling logic, gap detection, error handling, and storage management into separate composable classes. Consider using dependency injection to reduce coupling.

**Broad Exception Handling in Daemons:**
- Issue: Multiple `except Exception as e:` blocks without specific exception types in critical paths
- Files: `src/cta_eta/data_collection/orchestration/train_position_daemon.py` (lines 365, 417), `src/cta_eta/data_collection/orchestration/weather_daemon.py`, `src/cta_eta/data_collection/orchestration/daemon_async.py`
- Impact: Masks bugs and makes error categorization less reliable; classification may fail silently
- Fix approach: Use specific exception types (APIResponseError, ConfigurationError, CTATrackerAPIError, etc.) instead of bare `Exception`. Reserve `except Exception` only for catch-all that re-raises after logging.

**Storage Failure Handling with Incomplete Recovery:**
- Issue: Storage failures trigger backoff via `storage_backoff_until` but the counter is never reset, leading to potential false positives after recovery
- Files: `src/cta_eta/data_collection/orchestration/train_position_daemon.py` (lines 418-423)
- Impact: If storage recovers after N failures, the counter remains non-zero, so next failure immediately re-triggers backoff even if it was a one-off transient error
- Fix approach: Reset `storage_failure_count` to 0 on successful write, not just on backoff threshold. Alternatively, use exponential backoff with timestamp-based reset.

**CTA Error 102 (Daily Quota) Handling with Unbounded Sleep:**
- Issue: When CTA daily quota is exceeded (error 102), daemon sleeps until next midnight Chicago time with no upper bound verification
- Files: `src/cta_eta/data_collection/orchestration/train_position_daemon.py` (lines 600-700+, `_handle_daily_quota_exceeded` method)
- Impact: If quota resets occur unexpectedly (e.g., API hotfix), daemon wastes monitoring/alerting bandwidth staying silent. No heartbeat during sleep window.
- Fix approach: Add bounded probes during the midnight sleep window (already implemented via `probe_102_attempts`), but consider adding explicit heartbeat logging every 30 minutes during sleep to alert operators to stale daemon.

## Fragile Areas

**Journal Rotation Timing at 3 AM Partition Boundary:**
- Files: `src/cta_eta/data_collection/storage_cache/journal_writer.py` (partition_hour=3), `src/cta_eta/data_collection/orchestration/train_position_daemon.py`
- Why fragile: Hard partition at 3 AM America/Chicago timezone. If train runs cross the partition boundary, records are split across two journal files. Compaction must merge these correctly.
- Safe modification: When modifying partition hour or timezone handling, verify that compaction `discover_journals` finds all files for a given date, including those from the previous partition day. Add test covering the 3 AM boundary explicitly.
- Test coverage: `tests/data_collection/storage_cache/test_journal_writer.py` has partition tests, but needs explicit 3 AM boundary tests.

**File Archival Without Verification Lock:**
- Files: `src/cta_eta/data_collection/compaction/archiver.py` (lines 23-50), `src/cta_eta/data_collection/compaction/compact.py`
- Why fragile: After verifying upload row count, journals are moved to archive via `shutil.move()` with no atomic lock. If compaction restarts between verification and move, journals could be orphaned or deleted prematurely.
- Safe modification: Consider using a two-phase commit pattern: write a `.verified` marker file in the staging area AFTER upload verification, then move journals only if marker exists. Or use atomic rename operations with explicit error handling.
- Test coverage: `tests/data_collection/compaction/` lacks tests for partial failure scenarios (upload succeeds but archive fails).

**IPC Reader Graceful Degradation on Corruption:**
- Files: `src/cta_eta/data_collection/compaction/ipc_reader.py` (lines 74-93)
- Why fragile: File corruption is silently handled by salvaging valid batches. If a journal file becomes corrupted mid-day, compaction appears to succeed with partial data, but the loss is invisible unless row counts are explicitly monitored.
- Safe modification: When compaction detects corrupted journals (`was_clean=False` from `read_ipc_with_repair`), it should emit a CRITICAL log with the count of salvaged vs. expected batches. Add explicit alerting on journal corruption.
- Test coverage: Tests exist for salvage behavior but lack end-to-end scenarios with corrupted intermediate batch.

**Asyncio Daemon Signal Handling with Event Loop Dependency:**
- Files: `src/cta_eta/data_collection/orchestration/daemon_async.py` (signal registration in `start()`)
- Why fragile: Signal handlers registered only if event loop is running. If `start()` is called from a context where the event loop is unavailable, signal registration is silently skipped.
- Safe modification: Check that `asyncio.get_running_loop()` succeeds before registering handlers; raise `RuntimeError` if no loop. Add explicit exception if signals cannot be registered in production.
- Test coverage: Signal handling tests (`test_daemon_async.py`) need explicit coverage for no-event-loop scenarios.

## Performance Bottlenecks

**Synchronous Parquet Metadata Reads in Upload Verification:**
- Problem: After each upload attempt, code reads entire uploaded file back to verify row count via `backend.get()` + `pq.read_metadata()`
- Files: `src/cta_eta/data_collection/compaction/uploader.py` (lines 85-86)
- Cause: With 3 retry attempts, this means up to 3 full file reads from cloud storage. For large compacted Parquet files (100MB+), each read is slow.
- Improvement path: Use cloud provider metadata APIs (S3 Select, GCS metadata) to read row count without downloading entire file. Or cache metadata on local disk before upload, then verify via byte-level checksums instead of re-reading.

**Discovery Phase Fills Weather Grid Cache Synchronously:**
- Problem: When weather grid cache is cold, daemon discovers 50+ grid points one at a time via weather API calls
- Files: `src/cta_eta/data_collection/orchestration/weather_grid_discovery.py` (lines 248+), `src/cta_eta/data_collection/storage_cache/weather_grid_cache.py`
- Cause: Rate limiting (Open-Meteo: 0.1 req/sec max) forces sequential discovery, taking ~500 seconds (8+ minutes) on first run
- Improvement path: Batch discovery calls using aiometer's concurrent request support. Pre-warm cache on daemon startup for known stations, or use a background refresh task separate from main polling loop.

**Diagnostic Event Ring Buffer No Overflow Protection:**
- Problem: Diagnostics event buffer (`max_recent_events=64`) is in-memory and unbounded by event size
- Files: `src/cta_eta/data_collection/orchestration/diagnostics.py` (config: `max_recent_events=64`)
- Cause: Large events (e.g., span timing with many fields) could exhaust memory after many poll cycles
- Improvement path: Implement size-based eviction or ring buffer with maximum total bytes, not just event count. Add memory monitoring for daemon diagnostics.

## Scaling Limits

**IPC Journal File Count Per Day:**
- Current capacity: With 15-minute rotation, daemon writes 4 journal files per hour × 24 = 96 files per day per daemon
- Limit: Hive-style directory structure `year=YYYY/month=MM/day=DD/journal_*.ipc` has no issue at 100 files, but compaction must glob and sort all at once, which becomes slow at 500+ files
- Scaling path: At multi-daemon scale (5+ daemons × 96 files/day), implement sharded discovery that processes journal directories in parallel. Consider moving to a manifest file listing all journal files for a date rather than globbing.

**Weather Grid Cache with Unbounded Growth:**
- Current capacity: Cache stores up to ~50 grid mappings (stations → weather grids), persisted to JSON on disk
- Limit: No expiration for individual entries beyond TTL; if station mapping changes, stale entries accumulate
- Scaling path: Add automatic cleanup of stale cache entries based on last-access time. Implement versioning to detect schema changes and rebuild cache if needed.

**Archival Retention Pruning at Fixed 7 Days:**
- Current capacity: With 100 MB/day Parquet per daemon, 7-day retention = 700 MB per daemon. Multi-daemon setup could reach 1-2 GB without cleanup.
- Limit: Pruning runs synchronously in compaction job; if archive grows large (1000+ directories), glob + delete becomes slow
- Scaling path: Implement async background pruning task separate from compaction. Use S3 lifecycle policies or cloud native retention instead of application-level deletion.

## Missing Critical Features

**No Data Loss Detection Between Daemon Restart and Compaction:**
- Problem: If daemon crashes and misses polls before next compaction, there's no alerting. Compaction finds fewer files than expected but logs a warning only.
- Blocks: Cannot guarantee data completeness for model training. Gaps due to crashes are not visible in dashboards.
- Fix approach: Add explicit "expected vs. actual journal count" check in compaction. Compare against baseline polls-per-hour and surface mismatches as alerts. Store daemon state snapshots in a metrics database.

**No Schema Version Enforcement in IPC Writes:**
- Problem: JournalWriter infers schema from first batch and reuses it. If API response structure changes mid-day, subsequent records may fail to write or silently coerce types.
- Blocks: Cannot handle API breaking changes without manual intervention.
- Fix approach: Validate each batch schema against a versioned schema stored in config before write. Raise an error if schema changes during daemon run (forcing restart to pick up new schema).

**No Explicit Health Check for Daemon Crash Detection:**
- Problem: Monitoring system must watch logs/process status manually. No built-in mechanism for daemon to report "still alive" periodically.
- Blocks: Stale daemons sleeping during quota exceeded (error 102) can go undetected for hours.
- Fix approach: Add periodic heartbeat writes to a `.daemon_state/{daemon_name}.heartbeat` file with timestamp. Monitoring can check file age to detect stale daemons. Already partially implemented via `pending_gap_metadata` state file; extend this pattern.

## Security Considerations

**Unencrypted API Credentials in Memory:**
- Risk: API keys (CTA_API_KEY, OPENWEATHERMAP_API_KEY, etc.) loaded into process memory with no encryption
- Files: `src/cta_eta/data_collection/config.py` (lines 80-150), API client modules
- Current mitigation: Credentials stored in `.env` (git-ignored). SENSITIVE_KEYS redacted in logging via `_sanitize_config_for_logging()`.
- Recommendations: Use a secrets manager (Vault, AWS Secrets Manager) instead of .env. Rotate API keys periodically. Add audit logging for credential access.

**Cloud Storage Credentials via Environment Variables:**
- Risk: S3_BUCKET, GCS_BUCKET, AZURE_BUCKET, and cloud API credentials passed via env vars, visible in process listings
- Files: `src/cta_eta/data_collection/storage_cache/storage.py` (fsspec initialization), `config.toml` [storage] section
- Current mitigation: Cloud backend uses fsspec abstraction, which reads credentials from environment or IAM roles.
- Recommendations: Prefer cloud IAM roles (EC2 instance profiles, GKE service accounts) over explicit credentials. If explicit credentials required, use temporary STS tokens with short TTL. Audit cloud access logs for unusual patterns.

**No Input Validation on Latitude/Longitude Coordinates:**
- Risk: User-supplied station coordinates could be invalid, causing API calls to wrong locations
- Files: `src/cta_eta/data_collection/utils.py` (validate_lat_lon), weather API modules
- Current mitigation: `validate_lat_lon()` exists and is used before API calls
- Recommendations: Ensure all coordinate sources (CTA stations API, hardcoded configs) are validated. Add bounds checking for Chicago area (41-43°N, 87-88°W). Log and alert on out-of-bounds stations.

**Email Alert Recipients Not Validated:**
- Risk: `config.toml` contains `smtp_to = ["oncall@example.com"]` unvalidated
- Files: `config.toml` [alerting] section, `src/cta_eta/monitoring/alerting.py`
- Current mitigation: Email provider (Mailjet) validates recipient during send
- Recommendations: Validate email format in config loading. Add unit tests for malformed email lists. Consider environment-specific alert recipients (dev vs. prod).

## Test Coverage Gaps

**Untested Scenario: Daemon Crash During Journal Rotation:**
- What's not tested: If daemon process crashes exactly during `journal_writer.append_batch()` when the journal file is being rotated, what happens to the in-flight batch?
- Files: `src/cta_eta/data_collection/storage_cache/journal_writer.py`, `src/cta_eta/data_collection/orchestration/train_position_daemon.py`
- Risk: Batch could be partially written to old file and partially to new file, causing compaction to skip or duplicate records
- Priority: High - affects data integrity

**Untested Scenario: Compaction Upload Retries with Storage Backend Failures:**
- What's not tested: If cloud storage `put()` fails on retry 2 of 3, then succeeds on retry 3, and `get()` for verification fails, does archival still happen?
- Files: `src/cta_eta/data_collection/compaction/uploader.py`, `src/cta_eta/data_collection/compaction/compact.py`
- Risk: Journals could be archived despite failed verification
- Priority: High - causes data loss

**Untested Scenario: Weather Grid Discovery Cache Miss with Multiple Concurrent Daemons:**
- What's not tested: If two daemon instances discover the same station simultaneously, can the cache writes race and corrupt the file?
- Files: `src/cta_eta/data_collection/storage_cache/kv_cache.py` (uses tempfile for atomic writes, but not tested)
- Risk: Stale weather mappings or file corruption
- Priority: Medium - requires multi-daemon deployment to trigger

**Untested Scenario: Schema Drift Detection with Large Parquet Files:**
- What's not tested: Does `classify_drift()` correctly detect all drift types when dealing with tables > 100 MB?
- Files: `src/cta_eta/data_collection/compaction/schema_registry.py`
- Risk: Subtle schema changes (e.g., field nullability changes) could be missed
- Priority: Medium - only occurs with production data volumes

**Untested Scenario: Gap Detection Boundary at Midnight Chicago Time:**
- What's not tested: Gap detection when last poll is 11:59:59 PM and next poll is 12:00:01 AM (midnight boundary)
- Files: `src/cta_eta/data_collection/orchestration/gap_detection.py`
- Risk: Midnight boundary could trigger false gap detection due to timezone handling
- Priority: Medium - specific to daily partitioning logic

## Known Bugs

**Storage Failure Counter Not Reset on Success:**
- Symptoms: After 3 storage failures trigger backoff, the counter stays at 3. The next write succeeds, but counter remains elevated. If storage fails once more shortly after, backoff is re-triggered immediately (no gradual recovery).
- Files: `src/cta_eta/data_collection/orchestration/train_position_daemon.py` (line 418: `self.storage_failure_count += 1`, but never reset)
- Trigger: 1. Write succeeds after threshold hit, causing backoff. 2. After backoff, next write succeeds. 3. Third write fails - counter is still 3, backoff re-triggers.
- Workaround: Manually restart daemon to reset counter, or wait for `storage_failure_backoff_seconds` to elapse.

**Gap Reason Override Not Cleared on Retry:**
- Symptoms: If `gap_reason_override` is set by an error handler, but the next poll succeeds without a gap, the override is cleared. However, if multiple gaps occur in succession, the override from the first gap may be incorrectly applied to the second.
- Files: `src/cta_eta/data_collection/orchestration/train_position_daemon.py` (lines 310-336: override handling)
- Trigger: 1. Error occurs, `gap_reason_override` set to "Network timeout". 2. Next poll retries and succeeds (no gap). 3. Override cleared. 4. Gap detected immediately after (real gap). 5. Logs show override was cleared, not applied to real gap.
- Workaround: Override is idempotent — set it again if the error recurs. Logs show when override is cleared vs. applied.

---

*Concerns audit: 2026-02-28*
