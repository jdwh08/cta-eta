# Testing Patterns

**Analysis Date:** 2026-01-17

## Test Framework

**Runner:**
- pytest - Configured in CI but no test files exist yet
- Config: No pytest.ini, setup.cfg, or pytest configuration file yet
- Framework ready but not actively used

**Assertion Library:**
- pytest built-in expect (when tests are added)

**Run Commands:**
```bash
uv run pytest                        # Run all tests (when implemented)
uv run pytest --cov                  # Coverage (when implemented)
```

## Test File Organization

**Location:**
- `tests/` directory exists but is empty
- No test files currently exist
- No `test_*.py` or `*_test.py` files found

**Naming:**
- Not yet established (no tests written)
- Expected: `test_api_*.py` for API client tests

**Structure:**
```
tests/                               # Empty directory
```

## Test Structure

**Suite Organization:**
- Not yet established (no tests exist)
- Expected pattern: pytest-style `test_*` functions

**Patterns:**
- To be determined when tests are implemented

## Mocking

**Framework:**
- Not yet established (no tests exist)
- Expected: pytest fixtures or unittest.mock for API mocking

**Patterns:**
- To be determined when tests are implemented

**What to Mock:**
- External APIs (CTA, Chicago Data Portal, Open-Meteo)
- HTTP requests (httpx.Client calls)
- Environment variables (os.getenv)
- File system operations (CSV/JSON writes)

**What NOT to Mock:**
- Pure functions (JSON parsing)
- Type annotations
- Constants

## Fixtures and Factories

**Test Data:**
- Not yet established (no tests exist)
- Future: Fixtures for sample API responses, station data, weather data

**Location:**
- Expected: `tests/fixtures/` or `tests/conftest.py` for shared fixtures

## Coverage

**Requirements:**
- No enforced coverage target currently
- CI pipeline configured but no baseline established

**Configuration:**
- No coverage configuration file yet
- Expected: pytest-cov plugin when tests are added

**View Coverage:**
```bash
uv run pytest --cov                  # When implemented
```

## Test Types

**Unit Tests:**
- Not yet implemented
- Expected: Test individual API functions in isolation
- Mock all external dependencies (HTTP calls, file I/O)

**Integration Tests:**
- Not yet implemented
- Expected: Test API modules together (e.g., fetch + parse + write)

**E2E Tests:**
- Not planned currently

## Common Patterns

**Async Testing:**
- Not applicable (no async functions yet)
- httpx.Client is sync, not httpx.AsyncClient

**Error Testing:**
- Not yet implemented
- Expected: Test retry logic, HTTP error handling, exception raising

**File System Testing:**
- Not yet implemented
- Expected: Test CSV/JSON file writing with temp files

## CI/CD Integration

**GitHub Actions:**
- Configured in `.github/workflows/ci.yml`
- Step: `- name: Run tests` with `run: uv run pytest`
- Platform: ubuntu-latest with Python 3.13
- Status: **Pipeline ready but no tests to run**

**CI Pipeline Structure:**
1. Checkout code
2. Install uv
3. Setup Python via uv
4. Install all dependencies with `uv sync --all-extras`
5. Run linting (`uv run python devtools/lint.py`)
6. Run tests (`uv run pytest`) — **No tests yet**

## Current Status

**Testing Infrastructure:**
- ✅ CI/CD pipeline configured
- ✅ pytest framework available (implicit via CI)
- ❌ No test files exist
- ❌ No test coverage
- ❌ No mocking strategy defined
- ❌ No fixtures created

**Next Steps:**
- Create `tests/test_api_train_position.py` for CTA API tests
- Create `tests/test_api_stations_weather.py` for station/weather tests
- Add pytest and pytest-cov to `pyproject.toml` dev dependencies
- Create `tests/conftest.py` with shared fixtures
- Mock HTTP requests with pytest fixtures or responses library
- Establish baseline coverage target

---

*Testing analysis: 2026-01-17*
*Update when test patterns change*
