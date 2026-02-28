# Coding Conventions

**Analysis Date:** 2026-02-28

## Naming Patterns

**Files:**
- Lowercase with underscores: `api_train_position.py`, `daemon_async.py`
- Test files: `test_<module>.py` (e.g., `test_cache.py`, `test_api_train_position.py`)
- Modules grouped by feature/layer: `apis/`, `orchestration/`, `storage_cache/`, `compaction/`

**Functions:**
- Lowercase with underscores: `get_train_positions()`, `safe_get_nested()`, `rotate_file_if_needed()`
- Async functions use `async def`: `async def get_train_positions(client: httpx.AsyncClient)`
- Decorators for cross-cutting concerns: `@stamina.retry()`, `@log_api_call(logger)`, `@override`

**Variables:**
- Lowercase with underscores: `max_retry_attempts`, `cache_file`, `poll_timestamp`
- Constants use UPPERCASE: `MAX_LAT`, `MIN_LON`, `TRAIN_POSITION_URL`, `CTA_LINES`
- Constants marked with `Final` type hint: `MAX_RETRY_ATTEMPTS: Final[int] = 10`
- Private members use leading underscore: `_memory_cache`, `_cache_file`, `_ttl`

**Types:**
- PascalCase for classes: `CachedData`, `JSONFormatter`, `ConfigurationError`, `CTATrackerAPIError`
- Descriptive exception classes: `ConfigurationError`, `APIResponseError`, `DaemonNotStartedError`
- Type hints are comprehensive: `dict[str, Any]`, `list[str]`, `path: Path`, `client: httpx.AsyncClient`
- Generics with square brackets: `CachedData[T]`, `dict[str, object]`

## Code Style

**Formatting:**
- Tool: Ruff formatter
- Line length: 88 characters
- Quote style: Double quotes (`"string"`)
- Indent: 4 spaces
- Trailing commas: Enabled for multi-line structures

**Linting:**
- Tool: Ruff linter
- Configuration: `tool.ruff` in `pyproject.toml`
- Most rules enabled (`select = ["ALL"]`)
- Notable ignored rules:
  - `E402` (module import not at top) - allows lazy imports
  - `E731` (lambda assignment) - allows lambda usage
  - `FBT` (boolean traps) - disabled for flexibility
  - `G004` (f-string logging) - allows f-strings in logs
  - `RET504` (unnecessary assignment before return) - allows for debugging
- Per-file test ignores in `tests/**/*.py`:
  - `D102` (missing docstring in public method)
  - `S101` (assert detected) - asserts are fine for tests
  - `SLF001` (private method access) - allowed in tests

**Type Checking:**
- Tool: basedpyright
- Python: 3.13
- Error on missing imports: `reportMissingImports = "error"`
- Relaxed type stubs: `reportMissingTypeStubs = false`
- Special execution environment for tests: `tests/` allows private usage

## Import Organization

**Order:**
1. `from __future__ import annotations` (always first, enables modern type hints)
2. Standard library: `import os`, `from pathlib import Path`
3. Third-party: `import httpx`, `import pytest`, `import pyarrow as pa`
4. Local imports: `from cta_eta.data_collection.config import load_config`

**Path Aliases:**
- None detected; full module paths used throughout
- Example: `from cta_eta.data_collection.config import load_config`

**TYPE_CHECKING Block Pattern:**
Used to avoid circular imports and improve startup time:
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pytest_mock import MockerFixture
```

## Error Handling

**Patterns:**
- Custom exception hierarchy in `cta_eta.data_collection.exceptions`:
  - `ConfigurationError(ValueError)` - for missing/invalid config
  - `APIResponseError(Exception)` - for API parsing errors
  - `CTATrackerAPIError(Exception)` - for CTA API error codes in response body
  - `DaemonNotStartedError(RuntimeError)` - for lifecycle violations
- Exceptions provide context: `CTATrackerAPIError` stores `err_cd` and `err_nm` attributes
- Suppress known I/O errors at debug level: `except (OSError, json.JSONDecodeError): logger.debug(...)`
- Chaining with `from e`: `raise APIResponseError(msg) from e`
- Validation before use: `validate_lat_lon(lat, lon)` checks bounds before processing

**Example from `safe_get_nested()`:**
```python
if not isinstance(current, dict):
    msg = f"{api_name} response parsing error: Expected dict..."
    raise APIResponseError(msg)
```

## Logging

**Framework:** Python `logging` module

**Setup:**
- Custom formatters in `logging.py`:
  - `JSONFormatter` - JSON output for production
  - `HumanReadableFormatter` - Human-readable for dev
- Structured context with `log_context` manager for thread-safe correlation

**Patterns:**
- Get logger by module name: `logger = get_logger(__name__)`
- Log extra fields with decorator: `@log_api_call(logger)` instruments API calls
- Context manager for request correlation:
  ```python
  with log_context(request_id=123, source="daemon"):
      logger.info("Processing request")  # logs include context
  ```
- Debug-level for best-effort I/O: `logger.debug("Could not save...") if OSError`

**Best Practices:**
- Log level INFO for key events (API calls, daemon lifecycle)
- Log level ERROR for exceptions and failures
- Use f-strings allowed per ruff config: `f"message with {variable}"`

## Comments

**When to Comment:**
- Module-level docstrings explain purpose, API docs, and key behaviors
- Explain *why*, not *what*: "Refresh if expired or missing" (why) not "check if expired" (what)
- Edge case handling: `# NOTE(jdwh08): once we get enough utils, break into separate files`
- Complex logic paths: State machine transitions, retry semantics

**JSDoc/TSDoc Style:**
- Google-style docstrings (4 sections: summary, Args, Returns, Raises)
- Example from `get_train_positions()`:
  ```python
  """Fetch current train positions for all CTA lines from the Train Tracker API.

  Makes a single API call to retrieve positions for all 8 CTA train lines at once.
  Uses stamina retry decorator for resilience against transient HTTP errors.

  Args:
      client: HTTP client for API requests

  Returns:
      dict[str, Any]: Raw JSON response from the API.

  Raises:
      httpx.HTTPStatusError: After max retry attempts exhausted
      CTATrackerAPIError: When CTA returns error code in response body
      ConfigurationError: If CTA_API_KEY environment variable not set

  """
  ```
- Type hints in docstring match actual function signature

## Function Design

**Size:**
- Most functions 20-80 lines; some larger orchestrators reach 300+ lines
- Small focused functions preferred but practical size acceptable
- `@override` decorator used on subclass methods for clarity

**Parameters:**
- Explicit over implicit: `client: httpx.AsyncClient`, `ttl: int`
- Keyword-only for clarity: `def rotate_file_if_needed(path: Path, *, max_bytes: int, backups: int)`
- Dependency injection pattern: functions accept clients/loggers as params
- Configuration as dict: `config: dict[str, Any]` or typed sections `config: dict[str, dict[str, ...]]`

**Return Values:**
- Explicit None for void functions
- Tuple unpacking for multiple returns: `tuple[list[pa.RecordBatch], bool]`
- Optional for nullable: `float | None`, `dict[str, str] | None`
- Descriptive types not `Any`: Prefer `dict[str, object]` to `dict[str, Any]`

## Module Design

**Exports:**
- No explicit `__all__` detected; public API is all non-underscore names
- Modules are single-purpose: `cache.py` = caching, `logging.py` = logging setup

**Barrel Files:**
- Minimal use; most imports are direct: `from cta_eta.data_collection.apis import api_train_position`
- `__init__.py` files exist but minimal re-exports

**Async Patterns:**
- Base class for daemons: `AsyncBaseDaemon` in `daemon_async.py`
- Subclasses override `async def run()` method
- Signal handling through `_shutdown` event
- State persistence with `_get_state()` dict
- Timeout handling with `asyncio.wait_for()`

---

*Convention analysis: 2026-02-28*
