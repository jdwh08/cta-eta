# Codebase Concerns

**Analysis Date:** 2026-01-22

## Tech Debt

**Unguarded API Response Parsing:**
- Issue: Multiple API client files assume specific response structures without defensive validation
- Files affected:
  - `src/cta_eta/data_collection/apis/api_weather_nws.py` (lines 78, 159-189)
  - `src/cta_eta/data_collection/apis/api_weather_open_meteo.py` (lines 97-98, 171-189)
  - `src/cta_eta/data_collection/apis/api_weather_openweathermap.py` (lines 95-96, 166-192)
- Why: Rapid prototyping during initial development, assumed well-formed API responses
- Impact: KeyError or IndexError if API response structure changes or is malformed
- Fix approach: Wrap parsing in try-except blocks or use `.get()` with defaults consistently

**Pandas Overhead for Simple Dict Merging:**
- Issue: Weather merger creates pandas DataFrames just to merge dictionaries
- File: `src/cta_eta/data_collection/merging/weather_merger.py` (lines 89-104)
- Why: Initial implementation used pandas for all data operations
- Impact: Performance overhead on every polling cycle (~100 stations × 15-30 min intervals)
- Fix approach: Replace pandas concat with simple dict merging logic

**Hardcoded Rate Limit Constants:**
- Issue: API rate limits hardcoded in daemon without linking to actual API documentation
- File: `src/cta_eta/data_collection/orchestration/weather_daemon.py` (lines 62-63)
- Values: `_OPEN_METEO_MAX_PER_SECOND = 0.1`, `_OPEN_METEO_MAX_AT_ONCE = 3`
- Why: Quick implementation without configuration abstraction
- Impact: Manual code updates needed if API limits change
- Fix approach: Move to `config.toml` with comments linking to API docs

## Known Bugs

**Inconsistent Error Types for Missing Credentials:**
- Symptoms: `KeyError` raised instead of `ValueError` for missing env vars
- Trigger: Running without CTA_API_KEY set in environment
- File: `src/cta_eta/data_collection/apis/api_train_position.py` (line 85)
- Workaround: Set all required env vars in `.env` file
- Root cause: Inconsistent error handling pattern across API clients
- Fix: Change `raise KeyError(msg)` to `raise ValueError(msg)` for consistency

**Secrets Default to Empty Strings:**
- Symptoms: Application starts but fails later during API calls with cryptic errors
- Trigger: Missing required env vars in `.env` file
- File: `src/cta_eta/data_collection/config.py` (lines 39-47)
- Workaround: Validate `.env` file against `.env.template` manually
- Root cause: Defaults allow partial pipeline operation (by design)
- Fix: Add startup validation distinguishing required vs optional credentials

## Security Considerations

**Hardcoded Placeholder User-Agent:**
- Risk: NWS API requires proper User-Agent, placeholder may not be overridden
- File: `src/cta_eta/data_collection/storage_cache/weather_grid_cache.py` (line 109)
- Code: `"User-Agent": "(cta-eta, contact@example.com)"  # Will be overridden by actual config`
- Current mitigation: Comment suggests override, but mechanism not evident
- Recommendations: Verify override path or inject proper User-Agent from config

**Environment Variable Exposure:**
- Risk: API keys logged in plaintext if debug logging enabled
- Files: All API client files accessing `os.getenv()`
- Current mitigation: Structured logging doesn't log env vars by default
- Recommendations: Audit log statements to ensure secrets never logged

## Performance Bottlenecks

**Pandas DataFrame Creation Per-Record:**
- Problem: Creating DataFrames for every weather record merge
- File: `src/cta_eta/data_collection/merging/weather_merger.py` (lines 89-104)
- Measurement: ~100 stations × merge overhead × polling frequency
- Cause: Using pandas for simple dictionary merging operations
- Improvement path: Replace pandas concat with native dict operations

**Grid Discovery Timeout Complexity:**
- Problem: Complex nested async logic with multiple timeout scenarios
- File: `src/cta_eta/data_collection/orchestration/weather_daemon.py` (lines 677-819)
- Measurement: 143-line method with nested context managers
- Cause: Handling multiple concurrent discovery calls with individual timeouts
- Improvement path: Extract to separate `WeatherGridDiscoverer` class

## Fragile Areas

**Weather Daemon Discovery Logic:**
- File: `src/cta_eta/data_collection/orchestration/weather_daemon.py` (lines 677-819)
- Why fragile: 143-line method `_discover_open_meteo_grids_for_stations()` with complex async logic
- Common failures: Timeout handling across multiple concurrent tasks
- Safe modification: Extract grid discovery to separate class before modifying
- Test coverage: Needs verification (test file exists but coverage unknown)

**Configuration Access Without Guards:**
- Files: `src/cta_eta/data_collection/storage_cache/weather_grid_cache.py` (lines 191-192, 207-209)
- Why fragile: Direct dictionary key access assumes config structure
- Example: `config["cache"]["directory"]` - crashes with KeyError if missing
- Common failures: KeyError on startup if config.toml structure changes
- Safe modification: Use `.get()` with defaults or validate config schema at load time
- Test coverage: Config validation tests needed

## Scaling Limits

**Open-Meteo API Rate Limit:**
- Current capacity: 10,000 calls/day
- Limit: ~50 grid points × 48 polls/day = 2,400 calls/day (24% utilization)
- Symptoms at limit: 429 rate limit errors, missing weather data
- Scaling path: Add retry with exponential backoff, consider premium tier

**CTA API Rate Limit:**
- Current capacity: 50,000 requests/day
- Limit: 15-second polling = 5,760 requests/day (12% utilization)
- Symptoms at limit: 403 forbidden errors, missing train position data
- Scaling path: Increase polling interval or request higher rate limit from CTA

## Dependencies at Risk

**No High-Risk Dependencies Detected:**
- All major dependencies actively maintained (httpx, pandas, pytest)
- Python 3.13 requirement may limit deployment platforms
- Recommendation: Monitor pyarrow for breaking changes (rapid development)

## Missing Critical Features

**Startup Configuration Validation:**
- Problem: No validation of required env vars at daemon startup
- Current workaround: Errors discovered during first API call
- Blocks: Fast failure for misconfiguration, clear error messages
- Implementation complexity: Low (add validation in `config.py` load_config)

**Comprehensive Error Classification:**
- Problem: Generic `except Exception` in daemon main loop doesn't distinguish error types
- File: `src/cta_eta/data_collection/orchestration/weather_daemon.py` (lines 236-237, 435, 483, 530)
- Current workaround: Manual log inspection to determine error type
- Blocks: Automated error handling (retry transient, exit on config errors)
- Implementation complexity: Medium (classify exceptions, add specific handlers)

## Test Coverage Gaps

**Error Path Coverage:**
- What's not tested: Malformed API responses, retry behavior under failures
- Risk: Error handling code paths may not work as expected
- Priority: High (error handling is critical for 24/7 operation)
- Difficulty to test: Medium (need mock responses simulating failures)

**Integration Testing:**
- What's not tested: Full daemon lifecycle with real caches and storage
- Risk: Integration issues between components may not be caught
- Priority: Medium (unit tests provide good coverage)
- Difficulty to test: Medium (requires test fixtures for daemons)

## Documentation Gaps

**Complex Method Docstrings Missing:**
- Files:
  - `src/cta_eta/data_collection/orchestration/weather_daemon.py` (_collect_weather_cycle, _get_station_grid_mappings, _discover_open_meteo_grids_for_stations)
- Impact: Difficult to understand multi-phase collection logic without reading full implementation
- Fix: Add comprehensive docstrings explaining high-level flow and edge cases

**API Rate Limit Documentation:**
- Issue: Rate limit comments in code don't link to official API documentation
- Files: All `api_*.py` files with rate limit comments
- Impact: Can't verify limits without manual research
- Fix: Add URLs to official API docs in comments

## Input Validation Gaps

**Grid ID Validation:**
- Problem: Minimal validation of grid identifier format
- File: `src/cta_eta/data_collection/apis/api_weather_open_meteo.py` (lines 141-152)
- Example: Accepts `"999999,999999"` as valid lat/lon
- Impact: Invalid grid IDs may cause downstream errors
- Fix: Add range validation for latitude (-90 to 90) and longitude (-180 to 180)

**Config Structure Assumptions:**
- Problem: Config access assumes nested dictionary structure exists
- Files: All cache factory functions (`get_nws_grid_cache`, etc.)
- Example: `config["cache"]["directory"]` with no `.get()` fallback
- Impact: KeyError on startup if config structure changes
- Fix: Use `.get()` with defaults or validate config schema at load time

---

*Concerns audit: 2026-01-22*
*Update as issues are fixed or new ones discovered*
