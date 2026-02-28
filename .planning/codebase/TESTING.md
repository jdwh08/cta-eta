# Testing Patterns

**Analysis Date:** 2026-02-28

## Test Framework

**Runner:**
- pytest 9.0.2+
- Config: `pyproject.toml` under `[tool.pytest]`

**Key Settings:**
```toml
addopts = [
    "--import-mode=importlib",      # importlib mode for namespace isolation
    "--strict-markers",             # require marker definition
    "--strict-config",              # error on unknown config keys
    "--cov=src",                    # coverage of src/ only
    "--cov-report=term-missing",    # show uncovered lines
]
strict = true                       # strict config enforcement
python_files = ["test_*.py"]        # test file pattern
python_classes = ["Test*"]          # test class pattern (PascalCase)
python_functions = ["test_*"]       # test function pattern
testpaths = ["tests"]               # test root directory
```

**Assertion Library:**
- `assert` statements (built-in pytest)
- `pytest.raises()` for exception testing
- No separate assertion library needed

**Run Commands:**
```bash
# All tests
pytest

# Watch mode (requires pytest-watch)
pytest-watch

# Coverage report
pytest --cov=src --cov-report=html

# Specific test
pytest tests/data_collection/test_utils.py::TestSafeGetNested::test_success_single_key

# Markers (if defined)
pytest -m asyncio
```

**Additional Tools:**
- `pytest-cov` - Coverage measurement
- `pytest-mock` - Mocking with `mocker` fixture
- `pytest-sugar` - Enhanced output formatting
- `pytest-asyncio` - Async test support

## Test File Organization

**Location:**
- Mirrored structure: `tests/` mirrors `src/cta_eta/`
- Co-located by package: `tests/data_collection/apis/test_api_train_position.py` mirrors `src/cta_eta/data_collection/apis/api_train_position.py`

**Naming:**
- File: `test_<module_name>.py`
- Class: `Test<FunctionName>` or `Test<ComponentName>` (PascalCase)
- Function: `test_<scenario_description>` (describe_behavior pattern)

**Structure Example:**
```
tests/
├── conftest.py                                    # Shared fixtures
├── data_collection/
│   ├── conftest.py                               # Fixtures for this subtree
│   ├── test_config.py
│   ├── test_logging.py
│   ├── test_utils.py
│   ├── apis/
│   │   ├── test_api_train_position.py
│   │   ├── test_api_weather_openweathermap.py
│   │   └── ...
│   ├── orchestration/
│   │   ├── test_daemon_async.py
│   │   ├── test_train_position_daemon.py
│   │   └── ...
│   ├── storage_cache/
│   │   ├── test_cache.py
│   │   ├── test_journal_writer.py
│   │   └── ...
│   └── compaction/
│       ├── test_compact.py
│       ├── test_schema_registry.py
│       └── ...
├── monitoring/
│   ├── test_alerting.py
│   ├── test_health_check.py
│   └── ...
```

## Test Structure

**Suite Organization:**
```python
"""Unit tests for <module>."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


class TestCachedData:
    """Tests for CachedData generic class."""

    @pytest.fixture
    def temp_cache_dir(self, tmp_path: Path) -> Path:
        """Create temporary directory for cache files."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        return cache_dir

    @pytest.fixture
    def mock_fetch_fn(self) -> Mock:
        """Create mock fetch function."""
        return Mock(return_value={"test": "data"})

    def test_init_sets_attributes(self, cache_file: Path) -> None:
        """Test that __init__ sets all attributes correctly."""
        # Arrange
        ttl = 3600

        # Act
        cache = CachedData(cache_file=cache_file, ttl=ttl, fetch_fn=mock_fetch_fn)

        # Assert
        assert cache._cache_file == cache_file
        assert cache._ttl == ttl
```

**Key Patterns:**

1. **Docstring convention:** `"""Verb + condition returns/does expected behavior."""`
   - Example: `"""Returns value for single existing key."""`

2. **AAA Pattern (Arrange-Act-Assert):**
   - Arrange: Set up fixtures, mocks, test data
   - Act: Call the function under test
   - Assert: Check results and side effects

3. **Comments within sections:**
   ```python
   # Arrange
   monkeypatch.setenv("CTA_API_KEY", "test-api-key")

   # Act
   result = safe_get_nested(data, "a")

   # Assert
   assert result == 1
   ```

4. **Fixture dependency injection:**
   - Class methods receive fixtures as parameters
   - Fixtures defined as methods with `@pytest.fixture`
   - Type hints on fixtures: `def mock_fetch_fn(self) -> Mock:`

## Mocking

**Framework:** `pytest-mock` (via `mocker` fixture)

**Patterns:**

1. **Mock async client:**
   ```python
   client = mocker.AsyncMock(spec=httpx.AsyncClient)
   response = httpx_json_response(payload, 200, url)
   client.get.return_value = response
   ```

2. **Mock functions:**
   ```python
   mock_fetch_fn = Mock(return_value={"test": "data"})
   mocker.patch(
       "cta_eta.data_collection.compaction.compact.discover_journals",
       return_value=[]
   )
   ```

3. **Spy on calls:**
   ```python
   client.get.assert_awaited_once()
   assert client.get.call_args.args[0] == expected_url
   assert client.get.call_args.kwargs["params"] == expected_params
   ```

4. **Shared HTTP response fixture in `conftest.py`:**
   ```python
   @pytest.fixture
   def httpx_json_response() -> Callable[[object, int, str], httpx.Response]:
       """Build an httpx.Response with realistic behavior."""
       def _build(payload: object, status_code: int, url: str) -> httpx.Response:
           request = httpx.Request("GET", url)
           return httpx.Response(status_code=status_code, json=payload, request=request)
       return _build
   ```

**What to Mock:**
- External APIs (httpx calls)
- File system operations (when testing logic, not I/O)
- Environment variables (via `monkeypatch`)
- Expensive operations (database calls, external services)

**What NOT to Mock:**
- Core business logic (validate data, transform data)
- Real schemas and data structures (use actual `pa.Table` from `schemas.py`)
- Exception handling paths (test with real exceptions)

## Fixtures and Factories

**Test Data Patterns:**

1. **Fixtures for common setup:**
   ```python
   @pytest.fixture
   def cta_api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
       """Set required CTA API env var for the duration of the test."""
       monkeypatch.setenv("CTA_API_KEY", "test-api-key")
   ```

2. **Table builders for integration tests:**
   ```python
   def make_train_positions_table(rows: int = 1) -> pa.Table:
       """Build a train_positions table matching TRAIN_POSITION_SCHEMA."""
       return pa.table({
           "poll_timestamp": pa.array(
               [datetime(2026, 2, 17, 12, 0, 0, tzinfo=UTC)] * rows,
               type=pa.timestamp("us", tz="UTC"),
           ),
           # ... other fields ...
       }, schema=TRAIN_POSITION_SCHEMA)
   ```

3. **Config builders:**
   ```python
   def minimal_config(tmp_path: Path) -> dict[str, Any]:
       """Minimal config with tmp_path-based dirs (no shared /tmp)."""
       return {
           "storage": {
               "immediate": {
                   "data_path": str(tmp_path / "journals"),
               },
           },
       }
   ```

**Location:**
- Inline in test files if shared within one class
- `conftest.py` if shared across modules
- `tests/data_collection/conftest.py` for data_collection subtree fixtures

## Coverage

**Requirements:**
- No explicit minimum enforced (`--cov-report=term-missing` shows gaps)
- Coverage measured for `src/` only via `--cov=src`

**View Coverage:**
```bash
pytest --cov=src --cov-report=html
# Open htmlcov/index.html
```

**Coverage file:** `.coverage` (gitignored)

**HTML reports:** `.htmlcov/` directory with line-by-line coverage

## Test Types

**Unit Tests:**
- Scope: Single function/method in isolation
- Mocks: All external dependencies
- Location: Test file mirrors module structure
- Speed: Fast (< 1 second per test)
- Example: `TestSafeGetNested` tests `safe_get_nested()` with mocked data

**Integration Tests:**
- Scope: Multiple components working together
- Mocks: Only external services (APIs, cloud storage)
- Data: Real schemas and table structures from `schemas.py`
- Location: Same test file as unit tests, grouped in classes
- Example: `TestCompactIntegrationStyle` in `test_compact.py` uses real Parquet writing

**E2E Tests:**
- Status: Not detected in codebase
- Approach if needed: Would test full daemon lifecycle with real I/O

## Common Patterns

**Async Testing:**
```python
@pytest.mark.asyncio
async def test_get_train_positions_requires_api_key(
    monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """Test that get_train_positions requires CTA_API_KEY environment variable."""
    # Arrange
    monkeypatch.delenv("CTA_API_KEY", raising=False)
    client = mocker.AsyncMock(spec=httpx.AsyncClient)

    # Act / Assert
    with pytest.raises(ConfigurationError, match="CTA_API_KEY"):
        await api_train_position.get_train_positions(client)
```

**Error Testing:**
```python
def test_missing_key_raises_api_response_error(self) -> None:
    """Raises APIResponseError when required key is missing."""
    # Arrange
    data: dict[str, object] = {"a": 1}

    # Act & Assert
    with pytest.raises(
        APIResponseError, match=r"API response missing required field: 'b'"
    ):
        safe_get_nested(data, "b")
```

**Exception Chaining:**
```python
with pytest.raises(APIResponseError, match=r"Expected dict"):
    safe_get_nested(data, "a", "b")
```

**Parameter Testing (AAA per test):**
```python
def test_success_single_key(self) -> None:
    """Returns value for single existing key."""
    # Arrange
    data: dict[str, object] = {"a": 1}

    # Act
    result = safe_get_nested(data, "a")

    # Assert
    assert result == 1

def test_success_nested_keys(self) -> None:
    """Returns value for nested key path."""
    # Arrange
    data: dict[str, object] = {"a": {"b": {"c": "value"}}}

    # Act
    result = safe_get_nested(data, "a", "b", "c")

    # Assert
    assert result == "value"
```
(Separate test per scenario, not parametrized)

**Monkeypatch for Environment:**
```python
@pytest.fixture
def cta_api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required CTA API env var for the duration of the test."""
    monkeypatch.setenv("CTA_API_KEY", "test-api-key")


def test_calls_with_api_key(cta_api_key_env: None) -> None:
    """Automatically gets cta_api_key_env fixture."""
    # Environment variable is set for this test
```

**Tmp Path for File I/O:**
```python
def test_saves_to_file(self, tmp_path: Path) -> None:
    """Verify data persists to file."""
    # Arrange
    cache_file = tmp_path / "cache.json"

    # Act
    cache.get()

    # Assert
    assert cache_file.exists()
```

**Fixture Class Variables:**
```python
class TestCachedData:
    """Test class with shared fixtures."""

    @pytest.fixture
    def cache_file(self, temp_cache_dir: Path) -> Path:
        """Create cache file path for testing."""
        return temp_cache_dir / "test_cache.json"

    # Tests can request cache_file as parameter
    def test_get_loads_from_file(self, cache_file: Path) -> None:
        ...
```

**Setup/Teardown:**
- `conftest.py` sets environment: `os.environ["CTA_API_KEY"] = "test"`
- Fixtures manage cleanup automatically (tmp_path, monkeypatch)
- Generator fixtures with `yield` for resource cleanup:
  ```python
  @pytest.fixture
  def cleanup_state_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
      original_cwd = Path.cwd()
      monkeypatch.chdir(tmp_path)
      yield
      monkeypatch.chdir(original_cwd)
  ```

---

*Testing analysis: 2026-02-28*
