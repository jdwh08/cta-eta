# Coding Conventions

**Analysis Date:** 2026-01-24

## Naming Patterns

**Files:**
- snake_case for all modules: `api_train_position.py`, `weather_daemon.py`, `cache.py`
- API modules prefixed: `api_<name>.py` (e.g., `api_weather_nws.py`, `api_cta_stations.py`)
- Test files: `test_<module>.py` (mirrors source filename, e.g., `test_api_weather_open_meteo.py`)
- Config files: lowercase with extension: `config.toml`, `.env.template`

**Functions:**
- snake_case for all functions: `get_train_positions()`, `normalize_train_positions()`, `create_cached_data()`
- Private/internal functions: `_load_from_file()`, `_is_expired()`, `_parse_discover_grid_response()`
- Async functions: `async def` with descriptive names (no special async prefix like "async_")
- Purpose-prefixed: `get_*`, `discover_*`, `normalize_*`, `merge_*`
- Examples from `api_weather_open_meteo.py`:
  - `discover_open_meteo_grid()` - Grid point discovery
  - `get_open_meteo_current()` - Fetch current weather
  - `_parse_discover_grid_response()` - Internal parsing helper

**Variables:**
- snake_case for variables: `poll_timestamp`, `cache_file`, `fetch_fn`, `actual_lat`, `grid_lon`
- Complete type annotations on all parameters and returns
- Descriptive names preferred over abbreviations: `visibility_mi` not `vis`
- Single-letter variables only in tight mathematical contexts (rare)

**Types:**
- PascalCase for classes: `CachedData`, `BaseDaemon`, `WeatherDaemon`, `AsyncBaseDaemon`
- Private classes use underscore prefix: `_StationGridMapping` (frozen dataclass)
- Generic type parameters in brackets: `class CachedData[T]:`
- Modern Python 3.13+ generic syntax (no `Generic[T]` inheritance needed)

**Constants:**
- SCREAMING_SNAKE_CASE with `Final` type hint
- Examples from various modules:
  - `TRAIN_POSITION_URL: Final[str] = "..."`
  - `CTA_LINES: Final[list[str]] = ["Red", "Blue", "Brown", ...]`
  - `NWS_POINTS_URL: Final[str] = "https://api.weather.gov/points"`
  - `OPEN_METEO_URL: Final[str] = "https://api.open-meteo.com/v1/forecast"`
  - `MIN_LAT: Final[float] = -90.0` (from `utils.py`)
  - `MAX_RETRY_ATTEMPTS = 10` (from retry configuration)

## Code Style

**Formatting:**
- Tool: Ruff formatter (`pyproject.toml` [tool.ruff])
- Line length: 88 characters maximum (standard Python black-compatible)
- Quotes: Double quotes for all strings (enforced: `quote-style = "double"`)
- Indentation: 4 spaces (no tabs)
- Line endings: Auto (platform-dependent, enforced: `line-ending = "auto"`)
- Trailing commas: Preserved by formatter (`skip-magic-trailing-comma = false`)

**Linting:**
- Tool: Ruff with nearly all rules enabled (`select = ["ALL"]`)
- Config: `pyproject.toml` [tool.ruff.lint]
- Extends: B (flake8-bugbear) for additional checks
- Per-file ignores for tests:
  - PLR2004 (magic values - allowed in tests)
  - S101 (assert - allowed in tests)
  - SLF001 (private access - allowed in tests)
- Disabled rules globally:
  - G004 (f-strings in logging - explicitly allowed)
  - E501 (line-too-long - handled by formatter)
  - FBT (boolean-trap - disabled for API compatibility)

**Modern Python 3.13+ Features:**
- Generic types: `dict[str, Any]` instead of `Dict[str, Any]` (PEP 585)
- Union syntax: `str | int | None` instead of `Union[str, int, None]` (PEP 604)
- Generic classes: `class CachedData[T]:` instead of `class CachedData(Generic[T])` (PEP 695)
- Future annotations: `from __future__ import annotations` in all files (enables forward references)
- Required-python: `">=3.13,<4.0"` in `pyproject.toml`

## Import Organization

**Order:**
1. Future imports: `from __future__ import annotations`
2. Built-in modules: `import asyncio`, `import json`, `import os`, `from pathlib import Path`
3. Third-party packages: `import httpx`, `import stamina`, `import pandas`, `import pyarrow`
4. Own modules: `from cta_eta.data_collection.config import load_config`
5. TYPE_CHECKING imports: `if TYPE_CHECKING:` block for avoiding circular imports

**Grouping:**
- Blank line between groups
- Own modules marked with `### OWN MODULES` comment
- TYPE_CHECKING imports used to avoid circular dependencies
- Alphabetical within each group (not strictly enforced)

**Example Pattern:**
```python
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Final

import httpx
import stamina

### OWN MODULES
from cta_eta.data_collection.config import load_config
from cta_eta.data_collection.logging import get_logger

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable
```

**Path Aliases:**
- None defined (no @ aliases)
- Relative imports only within same package

## Error Handling

**Patterns:**
- Explicit error messages with `msg = "..."` pattern before raising
- Example from validation code:
  ```python
  if not api_key:
      msg = "CTA_API_KEY must be set in environment variables"
      raise ValueError(msg)
  ```
- Use `ValueError` for configuration/validation errors, not `KeyError`
- Custom errors: Extend `Error` class with named exceptions (e.g., `ValidationError`)

**Retry Logic:**
- `@stamina.retry(on=httpx.HTTPStatusError, attempts=10)` decorator on API calls
- Exponential backoff with configurable max attempts (default: 10)
- Only retries on HTTP 4xx/5xx status errors
- Example from `api_weather_open_meteo.py`:
  ```python
  @stamina.retry(on=httpx.HTTPStatusError, attempts=MAX_RETRY_ATTEMPTS)
  @log_api_call(logger)
  async def get_open_meteo_current(client: httpx.AsyncClient, ...) -> dict[str, Any]:
      ...
  ```

**Exception Propagation:**
- Let exceptions bubble up to daemon main loop
- Daemon catches all exceptions, logs with context, and continues polling
- Partial failures don't stop entire collection cycle
- `ErrorCategory` enum in `daemon_utils.py` for error classification

## Logging

**Framework:**
- Structured JSON logging via custom formatters in `logging.py`
- Formatters: `JSONFormatter` (production) + `HumanReadableFormatter` (dev)
- Context variables: Thread-safe using `contextvars` module

**Patterns:**
- Log decorators: `@log_api_call(logger)` for automatic API timing
- Context manager: `log_context()` for correlation IDs
- Example from `api_weather_open_meteo.py`:
  ```python
  @stamina.retry(on=httpx.HTTPStatusError, attempts=MAX_RETRY_ATTEMPTS)
  @log_api_call(logger)
  async def discover_open_meteo_grid(...) -> str:
      logger.info(f"Discovering Open-Meteo grid for {latitude}, {longitude}")
      ...
  ```

**F-strings in Logging:**
- Allowed (ruff rule G004 disabled in config)
- Example: `logger.info(f"Cache hit for station {station_id}: {grid_id}")`
- Structured context preferred for production: `logger.info("Cache hit", extra={"station_id": station_id})`

## Comments

**When to Comment:**
- Explain "why" not "what" - code should be self-explanatory
- Document business logic, complex algorithms, non-obvious patterns
- Mark own modules with `### OWN MODULES` comment before imports
- Avoid obvious comments (e.g., `# increment counter`)
- Implementation notes: `NOTE(username):` pattern for important notes
  - Example: `# NOTE(jdwh08): Keep discovery retries bounded:`

**Module Docstrings:**
- Format: Google-style docstrings (PEP 257 extended)
- Multi-line with blank line after opening quotes
- Comprehensive module-level context with:
  - Brief one-line summary
  - Extended description with implementation details
  - API documentation links
  - Rate limit information
  - Example raw JSON responses
- Example from `api_weather_open_meteo.py` (lines 1-50):
  ```python
  """CTA Train Position API client with retry logic and response normalization.

  This module provides functions to fetch train positions from the CTA Train Tracker API
  and normalize nested JSON responses into flat records for Parquet storage.

  All functions accept an httpx.AsyncClient parameter for dependency injection.

  API Documentation: https://www.transitchicago.com/developers/ttarrivals/
  Rate Limits: 50,000 requests per day
  """
  ```

**Function Docstrings:**
- Google-style with sections: Description, Args, Returns, Raises, Example (if applicable)
- Summary line, blank line, then full description
- Complete type hints in signature (docstring types optional)
- Example from `api_weather_open_meteo.py` (lines 90-107):
  ```python
  """Discover Open-Meteo grid identifier from API response.

  Makes minimal API request to Open-Meteo and extracts the actual
  coordinates used by the API (they round/snap to their grid).

  Args:
      client: HTTP client for API requests
      latitude: Coordinate latitude (must be between -90 and 90)
      longitude: Coordinate longitude (must be between -180 and 180)

  Returns:
      Open-Meteo grid identifier (e.g., "41.88,-87.63")

  Raises:
      httpx.HTTPStatusError: If API request fails after retries
      ValueError: If latitude or longitude is out of valid range
  """
  ```

**Inline Comments:**
- Linter suppressions: `# noqa: <rule>` (ruff) or `# type: ignore[code]` (basedpyright)
  - Example: `# noqa: ARG002` (unused argument)
  - Example: `# noqa: BLE001` (blind except)
  - Example: `# type: ignore[arg-type]` (basedpyright type mismatch)
- Mark intentional patterns with explanatory comments
- Avoid redundant comments that just restate the code

## Function Design

**Dependency Injection:**
- HTTP clients passed as parameters: `async def get_xxx(client: httpx.AsyncClient)`
- Enables connection pooling management and testability
- Pattern used in all API client functions
- Example: `async def get_train_positions(client: httpx.AsyncClient) -> dict[str, Any]:`

**Decorator Stacking:**
- Order matters: `@stamina.retry` outer, `@log_api_call(logger)` inner
- Allows unwrapping via `.__wrapped__` for testing
- Example stacking pattern:
  ```python
  @stamina.retry(on=httpx.HTTPStatusError, attempts=10)
  @log_api_call(logger)
  async def get_train_positions(client: httpx.AsyncClient) -> dict[str, Any]:
      ...
  ```

**Type Hints:**
- Complete annotations on all function signatures
- Use `TYPE_CHECKING` imports for avoiding circular dependencies
- Modern Python 3.13+ syntax: `dict[str, Any]`, `str | None`
- Generic functions: `def create_cached_data[T](...) -> CachedData[T]:`
- Return type always specified (use `-> None` for no return value)

**Parameters:**
- Max 3-4 parameters preferred
- Use options object for 4+ parameters (rare in this codebase)
- Destructure in parameter list when useful

**Return Values:**
- Explicit return types in all functions
- Use `None` for no return value
- Union types for multiple return types: `dict[str, object] | None`
- Prefer single return type when possible

## Module Design

**Exports:**
- Named exports only (no default exports)
- Public API defined by what's not prefixed with underscore
- No `__all__` declarations (rely on underscore prefix convention)

**Private Members:**
- Leading underscore for internal use: `_load_from_file()`, `_parse_response()`, `_client`
- Double underscore for name mangling (rare, not used in this codebase)
- Private classes: `_StationGridMapping(frozen=True)` (internal dataclass)

**Barrel Files:**
- `__init__.py` present in all packages for initialization
- No re-exports from `__init__.py` (import from specific modules)

---

*Convention analysis: 2026-01-24*
*Update when patterns change*
