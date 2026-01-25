# Testing Patterns

**Analysis Date:** 2026-01-22

## Test Framework

**Runner:**
- pytest 9.0.2+
- Config: `pyproject.toml` [tool.pytest] section

**Assertion Library:**
- pytest built-in `assert` statements
- Standard comparison: `assert result == expected`
- Exception checking: `pytest.raises(ExceptionType)`

**Run Commands:**
```bash
pytest                                        # Run all tests with coverage
pytest tests/data_collection/apis/            # Run API tests only
pytest tests/data_collection/apis/test_api_train_position.py  # Single file
pytest --no-cov                               # Skip coverage
pytest -v                                     # Verbose output
pytest -k "test_name_pattern"                 # Run tests matching pattern
```

## Test File Organization

**Location:**
- Test files mirror source structure exactly
- Pattern: `tests/` mirrors `src/cta_eta/`

**Naming:**
- Unit tests: `test_<module>.py` (e.g., `test_cache.py`, `test_api_train_position.py`)
- Only `test_*.py` pattern used (no `*_test.py` variant)

**Structure:**
```
tests/
├── conftest.py                          # Shared pytest fixtures
└── data_collection/
    ├── apis/
    │   ├── test_api_train_position.py  # 709 lines, comprehensive
    │   ├── test_api_weather_nws.py
    │   └── ...
    ├── storage_cache/
    │   ├── test_cache.py               # 733 lines, 100+ test methods
    │   ├── test_kv_cache.py
    │   ├── test_storage.py
    │   └── test_weather_grid_cache.py
    ├── orchestration/
    │   ├── test_daemon.py
    │   ├── test_daemon_async.py
    │   ├── test_diagnostics.py
    │   └── test_weather_daemon.py
    ├── merging/
    │   └── test_weather_merger.py
    ├── test_config.py
    └── test_logging.py
```

**File Count:**
- 23 Python source files in `src/cta_eta/`
- 19 test files in `tests/`

## Test Structure

**Suite Organization:**
```python
import pytest
from pytest_mock import MockerFixture

class TestCachedData:
    """Tests for CachedData generic cache."""

    def test_get_returns_none_when_cache_empty(self, cache_file):
        # Arrange
        cache = CachedData(cache_file, ttl=300, fetch_fn=mock_fetch)

        # Act
        result = cache.get()

        # Assert
        assert result is None

    def test_get_returns_cached_value_when_not_expired(self, cache_file):
        # Test implementation
        ...
```

**Patterns:**
- Test classes group related tests: `TestCachedData`, `TestCreateCachedData`
- Each test method prefixed with `test_`
- Descriptive test names: `test_<method>_<scenario>`
- Arrange-Act-Assert pattern consistently used

**Async Tests:**
- Marked with `@pytest.mark.asyncio` decorator
- Use `async def test_...()` pattern
- Example from `test_api_train_position.py`:
  ```python
  @pytest.mark.asyncio
  async def test_get_train_positions_requires_api_key():
      # Test implementation
  ```

## Mocking

**Framework:**
- pytest-mock (MockerFixture)
- `mocker.AsyncMock()` for async functions
- `mocker.Mock(spec=...)` for type-safe mocks

**Patterns:**
```python
def test_api_call_with_mock(mocker: MockerFixture):
    # Mock HTTP client
    mock_client = mocker.Mock(spec=httpx.AsyncClient)
    mock_response = httpx.Response(200, json={"data": "..."})
    mock_client.get.return_value = mock_response

    # Call function with mock
    result = await get_data(mock_client)

    # Verify mock interactions
    mock_client.get.assert_called_once_with("https://api.example.com/data")
```

**Unwrapping Decorated Functions:**
- Retry decorators bypassed for testing: `func.__wrapped__.__wrapped__`
- Example: `api_train_position.get_train_positions.__wrapped__.__wrapped__`
- Allows testing error paths without retry delays

**What to Mock:**
- HTTP clients (`httpx.AsyncClient`, `httpx.Client`)
- File system operations (via temporary directories, not mocking)
- Time for TTL expiration testing (via `mocker.patch`)
- External API responses (via mock HTTP responses)

**What NOT to Mock:**
- Pure functions and utilities
- Internal business logic (test actual implementation)
- Type hints and annotations

## Fixtures and Factories

**Shared Fixtures (`tests/conftest.py`):**
```python
@pytest.fixture
def httpx_json_response():
    """Factory for creating httpx.Response objects with JSON payload."""
    def _factory(status_code: int, json_data: dict) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            json=json_data,
            headers={"content-type": "application/json"},
        )
    return _factory
```

**Per-Module Fixtures:**
- Environment variable mocking: `cta_api_key_env` fixture
- Temporary directories: `temp_cache_dir`, `cache_file` fixtures
- Mock fetch functions: `mock_fetch_fn` for cache testing
- Complex test data: Factory functions in test files

**Fixture Examples:**
```python
@pytest.fixture
def cache_file(tmp_path):
    """Provide a temporary cache file path."""
    return tmp_path / "test_cache.json"

@pytest.fixture
def mock_fetch_fn(mocker):
    """Mock fetch function for cache testing."""
    return mocker.AsyncMock(return_value={"test": "data"})
```

## Coverage

**Requirements:**
- Coverage enabled by default: `--cov=src` in pytest addopts
- Coverage report: `--cov-report=term-missing` shows missing lines
- No enforced minimum coverage percentage

**Configuration:**
- Tool: pytest-cov (wraps coverage.py)
- Source: `src/` directory only
- Excludes: Test files automatically excluded

**View Coverage:**
```bash
pytest                          # Shows coverage in terminal
pytest --cov-report=html        # Generate HTML report
open htmlcov/index.html         # View HTML report
```

## Test Types

**Unit Tests:**
- Scope: Test single function or class in isolation
- Mocking: Mock all external dependencies (HTTP, file system)
- Speed: Fast (<100ms per test)
- Examples: `test_api_train_position.py` (API client functions)

**Integration Tests:**
- Scope: Test multiple modules together
- Mocking: Mock external APIs, use real caches and storage
- Examples: `test_weather_daemon.py` (daemon with multiple dependencies)

**E2E Tests:**
- Not present in current test suite
- Recommendation: Add for full pipeline testing

## Common Patterns

**Async Testing:**
```python
@pytest.mark.asyncio
async def test_async_operation():
    result = await async_function()
    assert result == "expected"
```

**Error Testing:**
```python
def test_raises_value_error_on_missing_api_key():
    with pytest.raises(ValueError, match="CTA_API_KEY must be set"):
        get_api_key()

@pytest.mark.asyncio
async def test_async_error():
    with pytest.raises(httpx.HTTPStatusError):
        await api_call_that_fails()
```

**Parametrized Tests:**
```python
@pytest.mark.parametrize("input,expected", [
    (0, False),
    (1, True),
    (2, True),
])
def test_multiple_inputs(input, expected):
    assert is_valid(input) == expected
```

**Temporary Files:**
```python
def test_with_temp_file(tmp_path):
    # tmp_path is a pytest fixture providing temporary directory
    cache_file = tmp_path / "cache.json"
    cache = CachedData(cache_file, ...)
    # Test implementation
```

**Edge Case Testing:**
- Comprehensive coverage of edge cases with descriptive names
- Examples from `test_api_train_position.py`:
  - `test_normalize_handles_missing_optional_fields()`
  - `test_normalize_handles_empty_routes_list()`
  - `test_normalize_handles_invalid_json_structure()`
  - `test_normalize_converts_string_numbers_to_typed_values()`

## Test Configuration

**pytest Configuration (`pyproject.toml`):**
```toml
[tool.pytest]
addopts = [
    "--import-mode=importlib",   # Use importlib for imports
    "--strict-markers",           # Strict marker validation
    "--strict-config",            # Strict configuration
    "--cov=src",                  # Coverage for src/
    "--cov-report=term-missing",  # Show missing lines
]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
testpaths = ["tests"]
```

**Linting for Tests:**
```toml
[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = [
    "PLR2004",  # Magic values allowed in tests
    "S101",     # Assert statements allowed
    "SLF001",   # Private method access allowed
    "INP001",   # Implicit namespace packages
]
```

## TYPE_CHECKING Pattern

**Avoiding Circular Imports:**
```python
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pytest_mock import MockerFixture

def test_example(mocker: MockerFixture):
    # Type hint works, but import only happens during type checking
    ...
```

---

*Testing analysis: 2026-01-22*
*Update when test patterns change*
