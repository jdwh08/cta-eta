# Coding Conventions

**Analysis Date:** 2026-01-22

## Naming Patterns

**Files:**
- snake_case for all modules: `api_train_position.py`, `weather_daemon.py`, `cache.py`
- API modules prefixed: `api_<name>.py` (e.g., `api_weather_nws.py`)
- Test files: `test_<module>.py` (mirrors source filename)

**Functions:**
- snake_case for all functions: `get_train_positions()`, `normalize_train_positions()`, `create_cached_data()`
- Private/internal functions: `_load_from_file()`, `_is_expired()`, `_get_auth_header()`
- Async functions: `async def` with descriptive names (no special async prefix)

**Variables:**
- snake_case for variables: `poll_timestamp`, `cache_file`, `fetch_fn`
- Complete type annotations on all parameters and returns

**Types:**
- PascalCase for classes: `CachedData`, `BaseDaemon`, `WeatherDaemon`, `AsyncBaseDaemon`
- Private classes use underscore prefix: `_StationGridMapping`
- Generic type parameters in brackets: `class CachedData[T]:`
- Modern Python 3.13+ generic syntax (no `Generic[T]` inheritance)

**Constants:**
- SCREAMING_SNAKE_CASE with `Final` type hint
- Examples:
  - `TRAIN_POSITION_URL: Final[str] = "..."`
  - `CTA_LINES: Final[list[str]] = [...]`
  - `NWS_POINTS_URL: Final[str] = "https://api.weather.gov/points"`

## Code Style

**Formatting:**
- Tool: Ruff formatter (`pyproject.toml` [tool.ruff])
- Line length: 88 characters maximum
- Quotes: Double quotes for all strings
- Indentation: 4 spaces (no tabs)
- Line endings: Auto (platform-dependent)

**Linting:**
- Tool: Ruff with nearly all rules enabled (`select = ["ALL"]`)
- Config: `pyproject.toml` [tool.ruff.lint]
- Per-file ignores for tests: PLR2004 (magic values), S101 (assert), SLF001 (private access)
- Disabled rules: G004 (f-strings in logging - allowed)

**Modern Python 3.13+ Features:**
- Generic types: `dict[str, Any]` instead of `Dict[str, Any]` (PEP 585)
- Union syntax: `str | int | None` instead of `Union[str, int, None]` (PEP 604)
- Generic classes: `class CachedData[T]:` instead of `Generic[T]` (PEP 695)
- Future annotations: `from __future__ import annotations` in all files

## Import Organization

**Order:**
1. Future imports: `from __future__ import annotations`
2. Built-in modules: `import asyncio`, `import json`, `import os`
3. Third-party packages: `import httpx`, `import stamina`, `import pandas`
4. Own modules: `from cta_eta.data_collection.config import load_config`
5. TYPE_CHECKING imports: `if TYPE_CHECKING:` block for avoiding circular imports

**Grouping:**
- Blank line between groups
- Own modules marked with `### OWN MODULES` comment
- TYPE_CHECKING imports used to avoid circular dependencies

**Example Pattern:**
```python
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import stamina

### OWN MODULES
from cta_eta.data_collection.config import load_config
from cta_eta.data_collection.logging import get_logger

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable
```

## Error Handling

**Patterns:**
- Explicit error messages with `msg = "..."` pattern before raising
- Example:
  ```python
  if not api_key:
      msg = "CTA_API_KEY must be set in environment variables"
      raise ValueError(msg)
  ```
- Use `ValueError` for configuration errors, not `KeyError`

**Retry Logic:**
- `@stamina.retry(on=httpx.HTTPStatusError, attempts=10)` decorator on API calls
- Exponential backoff with configurable max attempts
- Only retries on HTTP 4xx/5xx status errors

**Exception Propagation:**
- Let exceptions bubble up to daemon main loop
- Daemon catches all exceptions, logs, and continues polling
- Partial failures don't stop entire collection cycle

## Logging

**Framework:**
- Structured JSON logging via custom formatters
- Files: `src/cta_eta/data_collection/logging.py`

**Patterns:**
- Log decorators: `@log_api_call(logger)` for automatic API timing
- Context manager: `log_context()` for correlation IDs
- Formatters: JSONFormatter (production) + HumanReadableFormatter (dev)

**F-strings in Logging:**
- Allowed (ruff rule G004 disabled in config)
- Example: `logger.info(f"Cache hit for station {station_id}: {grid_id}")`

## Comments

**When to Comment:**
- Explain "why" not "what" - code should be self-explanatory
- Document business logic, complex algorithms, non-obvious patterns
- Mark own modules with `### OWN MODULES` comment before imports
- Avoid obvious comments

**Module Docstrings:**
- Format: Google-style docstrings (PEP 257 extended)
- Multi-line with blank line after opening quotes
- Comprehensive module-level context and usage examples
- Example:
  ```python
  """CTA Train Position API client with retry logic and response normalization.

  This module provides functions to fetch train positions from the CTA Train Tracker API
  and normalize nested JSON responses into flat records for Parquet storage.

  All functions accept an httpx.AsyncClient parameter for dependency injection.
  """
  ```

**Function Docstrings:**
- Summary line, blank line, then full description
- Sections: Description, Args, Returns, Raises, Example (if applicable)
- Complete type hints in signature (docstring types optional)

**Inline Comments:**
- Linter suppressions: `# noqa: <rule>` or `# type: ignore` when necessary
- Mark intentional patterns with explanatory comments
- Avoid redundant comments that just restate the code

## Function Design

**Dependency Injection:**
- HTTP clients passed as parameters: `async def get_xxx(client: httpx.AsyncClient)`
- Enables connection pooling management and testability
- Pattern used in all API client functions

**Decorator Stacking:**
- Order matters: `@log_api_call(logger)` outer, `@stamina.retry(...)` inner
- Allows unwrapping via `.__wrapped__` for testing
- Example:
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

**Return Values:**
- Explicit return types in all functions
- Use `None` for no return value
- Union types for multiple return types: `dict[str, object] | None`

## Module Design

**Exports:**
- Named exports only (no default exports)
- Public API defined by what's not prefixed with underscore
- No `__all__` declarations (rely on underscore prefix convention)

**Private Members:**
- Leading underscore for internal use: `_load_from_file()`, `_client`
- Double underscore for name mangling (rare, not used in this codebase)

---

*Convention analysis: 2026-01-22*
*Update when patterns change*
