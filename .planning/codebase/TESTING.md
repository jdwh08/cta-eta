# Testing Patterns

**Analysis Date:** 2026-01-19

## Test Framework

**Runner:**
- pytest 9.0.2 (`pyproject.toml` line 73)
- Config: `pyproject.toml` lines 197-213 (pytest configuration section)

**Assertion Library:**
- pytest built-in `assert` statements
- No additional assertion libraries

**Run Commands:**
```bash
uv run pytest                              # Run all tests
uv run pytest --watch                      # Watch mode (requires pytest-watch)
uv run pytest tests/data_collection/apis/test_api_weather_nws.py  # Single file
uv run pytest --cov=src --cov-report=term-missing  # Coverage report
```

## Test File Organization

**Location:**
- Tests in separate `tests/` directory mirroring `src/cta_eta/` structure
- No `__init__.py` files in tests directory (pytest namespace packages)

**Naming:**
- `test_*.py` for test modules (`pyproject.toml` line 206)
- `Test*` for test classes (`pyproject.toml` line 208)
- `test_*` for test functions (`pyproject.toml` line 210)

**Structure:**
```
tests/
├── conftest.py                          # Shared fixtures
└── data_collection/
    ├── test_config.py                   # Config tests
    ├── test_logging.py                  # Logging tests
    ├── apis/
    │   ├── test_api_weather_nws.py
    │   └── test_api_weather_open_meteo.py
    ├── storage_cache/
    │   ├── test_cache.py
    │   └── test_storage.py
    └── orchestration/
        └── test_daemon.py
```

## Test Structure

**Suite Organization:**
```python
"""Unit tests for module."""

from __future__ import annotations

import pytest

# Test class groups related tests
class TestLoadConfig:
    """Test cases for load_config function."""

    def test_load_config_success(
        self, temp_config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test successful config loading with valid TOML and env vars."""
        # Arrange
        monkeypatch.setenv("CTA_API_KEY", "test_key")

        # Act
        config = _load_config_from_path(temp_config_file)

        # Assert
        assert "collection" in config
        assert config["secrets"]["cta_api_key"] == "test_key"
```

**Patterns:**
- Class-based test organization with `Test*` prefix
- Descriptive test method names (`test_*_success`, `test_*_raises_error`)
- Arrange-Act-Assert comments in complex tests
- One logical assertion focus per test (multiple `assert` statements OK)

## Mocking

**Framework:**
- pytest-mock (`pytest.MockFixture`) for mocking
- unittest.mock for low-level mocking
- pytest's `monkeypatch` for environment variables

**Patterns:**
```python
def test_api_call(mocker: pytest.MockFixture, httpx_json_response: Callable) -> None:
    """Test API call with mocked httpx client."""
    # Mock httpx.Client
    client = mocker.Mock(spec=httpx.Client)
    client.get.return_value = httpx_json_response(
        {"data": "value"}, 200, "OK"
    )

    # Call function
    result = api_function(client)

    # Verify
    assert result["data"] == "value"
    client.get.assert_called_once()
```

**What to Mock:**
- HTTP clients (httpx.Client) - always mocked in unit tests
- File system operations (for unit tests, not integration tests)
- External API calls
- Environment variables (via `monkeypatch`)

**What NOT to Mock:**
- Pure functions and utilities
- Internal business logic
- Type annotations

## Fixtures and Factories

**Shared Fixtures:**
```python
# tests/conftest.py
@pytest.fixture
def httpx_json_response() -> Callable[[dict, int, str], httpx.Response]:
    """Factory fixture for creating httpx.Response objects."""
    def _create(data: dict, status: int, reason: str) -> httpx.Response:
        return httpx.Response(
            status_code=status,
            json=data,
            request=httpx.Request("GET", "https://example.com"),
        )
    return _create
```

**Module Fixtures:**
```python
# In test file
@pytest.fixture
def temp_config_file(tmp_path: Path) -> Path:
    """Create temporary config.toml file."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
        [collection]
        train_interval_seconds = 15
        """
    )
    return config_file
```

**Patterns:**
- Factory fixtures (return callable) for flexible object creation
- `tmp_path` pytest fixture for temporary files/directories
- `monkeypatch` for environment variable manipulation
- `mocker.Mock(spec=Class)` for type-safe mocks

**Location:**
- Shared fixtures: `tests/conftest.py`
- Module-specific fixtures: Inline in test files

## Coverage

**Requirements:**
- No enforced minimum coverage
- Coverage tracked for awareness
- Focus on critical paths (API clients, config, cache, daemon)

**Configuration:**
- Coverage enabled by default: `--cov=src --cov-report=term-missing` (`pyproject.toml` lines 202-203)
- Excludes: pytest cache, `.ruff_cache`, `__pycache__`

**View Coverage:**
```bash
uv run pytest --cov=src --cov-report=term-missing
uv run pytest --cov=src --cov-report=html
# Open htmlcov/index.html
```

## Test Types

**Unit Tests:**
- Scope: Test single function/class in isolation
- Mocking: Mock all external dependencies (HTTP, file system)
- Speed: Fast (<100ms per test)
- Examples: `test_config.py`, `test_cache.py`, `test_logging.py`

**Integration Tests:**
- Scope: Test multiple modules together
- Mocking: Mock only external boundaries (APIs, file system)
- Examples: `test_api_weather_nws.py` (tests API client + normalization)

**E2E Tests:**
- Not currently implemented

## Common Patterns

**Async Testing:**
- Not currently used (no async code yet)

**Error Testing:**
```python
def test_function_raises_on_invalid_input() -> None:
    """Test that function raises ValueError on invalid input."""
    with pytest.raises(ValueError, match="Invalid input"):
        function_under_test(None)
```

**Parametrized Testing:**
```python
@pytest.mark.parametrize(
    ("input_value", "expected_output"),
    [
        (1, "one"),
        (2, "two"),
        (3, "three"),
    ],
)
def test_conversion(input_value: int, expected_output: str) -> None:
    """Test number to word conversion."""
    assert convert(input_value) == expected_output
```

**Fixture Composition:**
```python
@pytest.fixture
def configured_daemon(
    mock_logger: MagicMock, sample_config: dict
) -> ConcreteDaemon:
    """Create daemon with mock logger and sample config."""
    return ConcreteDaemon(sample_config, mock_logger)
```

**Temporary Directory Pattern:**
```python
def test_file_operations(tmp_path: Path) -> None:
    """Test file read/write."""
    # Arrange
    test_file = tmp_path / "test.txt"

    # Act
    test_file.write_text("content")

    # Assert
    assert test_file.read_text() == "content"
```

**Monkeypatch Environment:**
```python
def test_config_with_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test config loading with environment variables."""
    # Arrange
    monkeypatch.setenv("CTA_API_KEY", "test_key")

    # Act
    config = load_config()

    # Assert
    assert config["secrets"]["cta_api_key"] == "test_key"
```

**Context Manager Testing:**
```python
def test_daemon_lifecycle(configured_daemon: ConcreteDaemon) -> None:
    """Test daemon starts and stops cleanly."""
    # Act
    configured_daemon.start()
    time.sleep(0.1)
    configured_daemon.stop()

    # Assert
    assert configured_daemon.run_called
    assert not configured_daemon.running
```

## Pytest Configuration

**Settings (`pyproject.toml` lines 197-213):**
- Import mode: `importlib` for proper module handling
- Strict markers and config enforcement
- Default coverage on src/
- Test discovery: `tests/` directory
- Test patterns: `test_*.py`, `Test*`, `test_*`

**Plugins Used:**
- pytest-cov (coverage)
- pytest-sugar (enhanced output)
- pytest-mock (mocking utilities)

## Test Coverage Gaps

**Missing Tests:**
- `api_train_position.py` - No tests for train API client
- `api_cta_track_shape.py` - No tests for track shape fetching
- `api_weather_openweathermap.py` - No tests for OpenWeatherMap client
- `weather_grid_cache.py` - No tests for grid cache classes

**Tested Modules:**
- `config.py` - Comprehensive config loading tests
- `logging.py` - Logging formatter tests
- `cache.py` - Generic cache tests
- `storage.py` - Storage backend tests
- `daemon.py` - Daemon lifecycle tests
- `api_weather_nws.py` - NWS API client tests
- `api_weather_open_meteo.py` - Open-Meteo API client tests

---

*Testing analysis: 2026-01-19*
*Update when test patterns change*
