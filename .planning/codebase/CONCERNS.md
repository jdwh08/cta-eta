# Codebase Concerns

**Analysis Date:** 2026-01-17

## Tech Debt

**No .env.example file:**
- Issue: Environment variables not documented for new developers
- Files: `.env` exists but `.env.example` missing
- Why: Early development stage, single developer workflow
- Impact: New contributors don't know which environment variables are required
- Fix approach: Create `.env.example` with placeholder values:
  ```
  CTA_API_KEY=your_cta_api_key_here
  CHIDATA_APP_TOK=your_chicago_data_token_here
  CHIDATA_APP_SECRET=your_chicago_data_secret_here
  ```

**Unused dependency (tenacity):**
- Issue: `tenacity>=9.1.2` declared in `pyproject.toml` but not imported or used
- Files: `pyproject.toml` (declared), no usage in `src/cta_eta/*.py`
- Why: Likely planned for future use alongside stamina
- Impact: Unnecessary dependency in lock file, minimal impact (~small package)
- Fix approach: Remove from `pyproject.toml` or document planned usage in code

**No package structure:**
- Issue: No `__init__.py` or `__main__.py` files in `src/cta_eta/`
- Files: `src/cta_eta/` directory has no package initialization
- Why: Early development, modules run as scripts
- Impact: Cannot import as package (`from cta_eta import api_train_position` won't work)
- Fix approach: Add `src/cta_eta/__init__.py` and optionally `__main__.py` for package execution

**Module-level execution code:**
- Issue: Data collection code runs at module import in `src/cta_eta/api_stations_weather.py`
- Files: `src/cta_eta/api_stations_weather.py:105-127` (module-level loop and CSV writing)
- Why: Script-based development pattern
- Impact: Cannot import module without executing data collection; not idempotent
- Fix approach: Wrap execution code in `if __name__ == "__main__":` guard

## Known Bugs

**API key handling without default:**
- Symptoms: `os.getenv("CTA_API_KEY")` returns None if .env not loaded, causing silent failures
- Trigger: Missing .env file or incorrect environment variable names
- Files: `src/cta_eta/api_train_position.py:25`, `src/cta_eta/api_stations_weather.py` (multiple locations)
- Workaround: Ensure `.env` file exists with correct keys
- Root cause: No validation that environment variables are set
- Fix: Add validation after `load_dotenv()`:
  ```python
  API_KEY = os.getenv("CTA_API_KEY")
  if not API_KEY:
      raise ValueError("CTA_API_KEY not found in environment")
  ```

**Hardcoded file paths:**
- Symptoms: `stations_weather.csv` written to current working directory
- Trigger: Running script from different directory than project root
- Files: `src/cta_eta/api_stations_weather.py:122` (`with open("stations_weather.csv", "w")`)
- Workaround: Always run from project root
- Root cause: Relative paths without directory validation
- Fix: Use pathlib and project root detection, write to `data/` directory

## Security Considerations

**Environment variables in .env file:**
- Risk: API keys stored in plaintext `.env` file
- Files: `.env` (gitignored but exists locally)
- Current mitigation: `.gitignore` includes `.env` (correctly excluded from git)
- Recommendations: Document that `.env` should have restricted permissions (`chmod 600 .env`)

**No API key rotation strategy:**
- Risk: Long-lived API keys with no expiration or rotation
- Files: `.env` file
- Current mitigation: Keys stored locally only
- Recommendations: Document key rotation process for production deployment

## Performance Bottlenecks

**Sequential weather API calls:**
- Problem: `time.sleep(1)` between each weather API call for 146 stations
- Files: `src/cta_eta/api_stations_weather.py:113` (loop with sleep)
- Measurement: ~146 seconds (2.4 minutes) to fetch weather for all stations
- Cause: Rate limiting mitigation, but could use batch queries
- Improvement path: Use batch lat/lon queries (Open-Meteo supports comma-separated coordinates)

**No async/await pattern:**
- Problem: Synchronous HTTP calls, not utilizing async capabilities of httpx
- Files: All `src/cta_eta/api_*.py` modules use `httpx.Client()` instead of `httpx.AsyncClient()`
- Measurement: Sequential API calls block execution
- Cause: Script-based development, simplicity over performance
- Improvement path: Refactor to `httpx.AsyncClient()` and `asyncio` for concurrent API calls

## Fragile Areas

**No test coverage:**
- Why fragile: All code untested, no safety net for refactoring
- Files: Entire `src/cta_eta/` directory
- Common failures: API schema changes, environment variable issues, file I/O errors
- Safe modification: Add tests before refactoring (see TESTING.md)
- Test coverage: 0% currently

**Retry logic without exponential backoff:**
- Why fragile: `@stamina.retry(attempts=10)` retries immediately, may hit rate limits
- Files: `src/cta_eta/api_train_position.py:20`, `src/cta_eta/api_stations_weather.py:20,68`
- Common failures: Rapid retries may exhaust API rate limits
- Safe modification: Add backoff strategy: `@stamina.retry(on=httpx.HTTPStatusError, attempts=10, timeout=60)`
- Note: stamina has default exponential backoff, but should verify configuration

## Scaling Limits

**Manual execution only:**
- Current capacity: Manual script execution
- Limit: No continuous data collection, no automation
- Symptoms at limit: Gaps in data, manual intervention required
- Scaling path: Deploy to VPS with cron jobs or continuous polling daemon

**Local file storage:**
- Current capacity: CSV/JSON files on local disk
- Limit: Disk space, no backup, single point of failure
- Symptoms at limit: Disk full errors, data loss on system failure
- Scaling path: Implement cloud storage (S3) sync, parquet file format, compression

## Dependencies at Risk

**httpx version pinning:**
- Risk: `httpx>=0.28.1` uses minimum version, may have breaking changes in future
- Impact: API changes could break on dependency updates
- Migration plan: Pin to specific range: `httpx>=0.28.1,<1.0.0` or test with latest

## Missing Critical Features

**No continuous data collection:**
- Problem: Data collection requires manual execution
- Current workaround: Manual script runs
- Blocks: Production ML model training (need continuous data stream)
- Implementation complexity: Medium (deploy to VPS, add scheduling, error handling, monitoring)

**No data validation:**
- Problem: API responses not validated for expected schema
- Current workaround: Trust API responses, fail on JSON parsing errors
- Blocks: Data quality assurance, graceful degradation on schema changes
- Implementation complexity: Low (add pydantic models or JSON schema validation)

**No ETA label generation:**
- Problem: Cannot use CTA `arrT` field for ground truth (see PLAN.md line 90-95)
- Current workaround: Not yet implemented
- Blocks: Model training phase (need labeled training data)
- Implementation complexity: High (geospatial calculations, train position tracking, station arrival detection)

## Test Coverage Gaps

**No API client tests:**
- What's not tested: All API functions (`get_stations()`, `get_weather()`, `get_train_position()`)
- Risk: API schema changes break silently, retry logic untested
- Priority: High (core functionality)
- Difficulty to test: Medium (need to mock httpx responses)

**No error handling tests:**
- What's not tested: Retry behavior, HTTP error handling, exception raising
- Risk: Error paths untested, may fail in production
- Priority: Medium
- Difficulty to test: Medium (need to simulate HTTP errors)

**No integration tests:**
- What's not tested: Full data collection pipeline (fetch → parse → write)
- Risk: Data integrity issues, file I/O failures
- Priority: Medium
- Difficulty to test: Medium (need temp file fixtures)

---

*Concerns audit: 2026-01-17*
*Update as issues are fixed or new ones discovered*
