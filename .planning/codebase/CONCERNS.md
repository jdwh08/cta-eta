# Codebase Concerns

**Analysis Date:** 2026-01-19

## Tech Debt

**Unsafe Array Index Access:**
- Issue: Multiple API response parsers access array indices without bounds checking
- Files:
  - `src/cta_eta/data_collection/apis/api_weather_nws.py:151` - `data["properties"]["periods"][0]` - No check if periods array is non-empty
  - `src/cta_eta/data_collection/apis/api_weather_openweathermap.py:157` - `data["weather"][0]` - No bounds check
  - `src/cta_eta/data_collection/apis/api_weather_openweathermap.py:244` - `data["list"][0]` - No bounds check on forecast list
  - `src/cta_eta/data_collection/apis/api_weather_openweathermap.py:247` - `forecast["weather"][0]` - No bounds check
- Impact: IndexError exceptions on unexpected API responses, causing daemon crashes
- Fix approach: Add bounds checking before array access - check if array exists and has elements

**httpx.Client Resource Leak:**
- Issue: httpx.Client instances created but never closed in WeatherGridCache classes
- Files:
  - `src/cta_eta/data_collection/storage_cache/weather_grid_cache.py:156` - NWSGridCache creates client without closing
  - `src/cta_eta/data_collection/storage_cache/weather_grid_cache.py:202` - OpenMeteoGridCache creates client without closing
- Why: Instance variables created in `__init__` but no context manager or `__del__` method
- Impact: Connection pool exhaustion, memory leaks, file descriptor leaks over time
- Fix approach: Add `__enter__` and `__exit__` methods for context manager support, or add `__del__` to close client

**Cache Save Errors Not Propagated:**
- Issue: OSError during cache save is caught and logged but not raised
- Files: `src/cta_eta/data_collection/storage_cache/cache.py:116-123` - Exception caught but not re-raised
- Impact: Daemon runs but cache updates silently fail, restart may lose cached data
- Fix approach: Consider re-raising or adding retry logic for cache saves

## Known Bugs

**None currently identified** - No active bugs documented in code comments

## Security Considerations

**Environment Variable Exposure:**
- Risk: API keys stored in environment variables could be exposed if daemon state is logged
- Current mitigation: Keys not logged in structured logging
- Recommendations: Ensure state persistence in `.daemon_state/*.json` doesn't include secrets

**.env File Presence:**
- Risk: `.env` file exists in working directory (gitignored but could be accidentally committed)
- Current mitigation: Properly configured in `.gitignore` (line 154)
- Recommendations: Use `.env.local` instead to avoid confusion

## Performance Bottlenecks

**No current bottlenecks identified** - Data collection is I/O bound, not CPU bound

## Fragile Areas

**JSON Key Access Without Validation:**
- Why fragile: API responses may change or omit fields
- Files:
  - `src/cta_eta/data_collection/apis/api_weather_nws.py:154-163` - Dewpoint access without validation
  - `src/cta_eta/data_collection/apis/api_weather_openweathermap.py:155-157` - Weather array access
  - `src/cta_eta/data_collection/apis/api_cta_track_shape.py:183-186` - Geometry access
- Common failures: KeyError on malformed API responses
- Safe modification: Add `.get()` with defaults or validate key existence first
- Test coverage: API client tests exist for happy path, but edge cases not fully covered

**Grid ID Parsing:**
- Why fragile: Assumes "lat,lon" format without validation
- Files:
  - `src/cta_eta/data_collection/apis/api_weather_openweathermap.py:140` - `grid_id.split(",")`
  - `src/cta_eta/data_collection/apis/api_weather_openweathermap.py:228` - `grid_id.split(",")`
- Common failures: ValueError if grid_id has unexpected format (e.g., empty string, wrong separator)
- Safe modification: Validate split result has exactly 2 parts
- Test coverage: Not tested

**Timestamp Type Conversion:**
- Why fragile: Assumes timestamp is string or datetime without validation
- Files: `src/cta_eta/data_collection/storage_cache/storage.py:366-368` - Late conversion
- Common failures: ValueError if `fromisoformat()` receives invalid string format
- Safe modification: Validate format before conversion or use try/except
- Test coverage: Basic tests exist, edge cases not covered

## Scaling Limits

**API Rate Limits:**
- Current capacity:
  - CTA: 50,000 requests/day (sufficient for 15s polling)
  - Open-Meteo: 10,000 requests/day (current: ~4,500/day)
  - OpenWeatherMap: 1,000 requests/day (fallback only)
- Limit: Adding more weather providers or stations requires careful rate limit management
- Symptoms at limit: 429 rate limit errors, API throttling
- Scaling path: Optimize polling intervals, add request batching, upgrade to paid tiers

**Storage Growth:**
- Current capacity: Unlimited (local development)
- Limit: Parquet files grow indefinitely without cleanup
- Symptoms at limit: Disk space exhaustion
- Scaling path: Implement data retention policy, archive old partitions to cold storage

## Dependencies at Risk

**No at-risk dependencies identified** - All dependencies are actively maintained

## Missing Critical Features

**Daemon Orchestration:**
- Problem: No main daemon to coordinate train and weather pollers
- Current workaround: Manual daemon execution
- Blocks: Cannot run full 24/7 data collection pipeline
- Implementation complexity: Medium (inherit from BaseDaemon, coordinate multiple pollers)

**Data Retention Policy:**
- Problem: No automatic cleanup of old Parquet files
- Current workaround: Manual deletion
- Blocks: Cannot run indefinitely without disk cleanup
- Implementation complexity: Low (add cleanup logic to storage backend)

**Graceful Degradation:**
- Problem: No fallback when primary weather API fails
- Current workaround: OpenWeatherMap fallback exists but not fully integrated
- Blocks: Data collection stops if NWS API is down
- Implementation complexity: Low (add try/except with fallback chain)

**Production Deployment:**
- Problem: No deployment scripts or systemd service files
- Current workaround: Manual execution
- Blocks: Cannot easily deploy to cloud VPS
- Implementation complexity: Low (add systemd unit files, deployment scripts)

## Test Coverage Gaps

**Missing API Client Tests:**
- What's not tested:
  - `src/cta_eta/data_collection/apis/api_train_position.py` - No tests for train position fetching
  - `src/cta_eta/data_collection/apis/api_cta_track_shape.py` - No tests for track shape fetching
  - `src/cta_eta/data_collection/apis/api_weather_openweathermap.py` - No tests for OpenWeatherMap client
- Risk: Refactoring may break untested code silently
- Priority: High (API clients are critical path)
- Difficulty to test: Medium (requires mock HTTP responses)

**Missing Cache Tests:**
- What's not tested:
  - `src/cta_eta/data_collection/storage_cache/weather_grid_cache.py` - No tests for NWSGridCache or OpenMeteoGridCache
- Risk: Grid discovery logic may break without detection
- Priority: Medium (cache reduces API calls but not critical for correctness)
- Difficulty to test: Medium (requires mock API clients and file system)

**Edge Case Coverage:**
- What's not tested: Error paths, malformed API responses, network failures
- Risk: Production issues from unexpected inputs
- Priority: Medium (most errors are retried automatically)
- Difficulty to test: Low (pytest parametrization can cover edge cases)

## Documentation Gaps

**Complex Logic Needs Comments:**
- Files:
  - `src/cta_eta/data_collection/storage_cache/storage.py:317-341` - Timezone-aware date calculation (complex, needs more explanation)
  - `src/cta_eta/data_collection/apis/api_cta_track_shape.py:220-234` - Segment ID fingerprinting (clever but underdocumented)
  - `src/cta_eta/data_collection/logging.py:125-182` - Decorator with performance timing (needs examples)
- Impact: Future maintainers may not understand logic
- Fix approach: Add inline comments explaining why logic exists and how it works

**No Deployment Documentation:**
- Missing: How to deploy to cloud VPS, systemd service setup, production configuration
- Impact: Difficult to deploy to production without trial and error
- Fix approach: Add DEPLOYMENT.md with step-by-step instructions

---

*Concerns audit: 2026-01-19*
*Update as issues are fixed or new ones discovered*
