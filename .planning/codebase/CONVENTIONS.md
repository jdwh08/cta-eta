# Coding Conventions

**Analysis Date:** 2026-01-19

## Naming Patterns

**Files:**
- `snake_case.py` for all modules (e.g., `api_train_position.py`, `weather_grid_cache.py`)
- `test_*.py` for test files (e.g., `test_config.py`, `test_api_weather_nws.py`)
- `UPPER_CASE.md` for important docs (e.g., `README.md`, `CLAUDE.md`)

**Functions:**
- `snake_case` for all functions (e.g., `load_config()`, `get_train_positions()`, `normalize_train_positions()`)
- No special prefix for async functions
- Descriptive names (e.g., `discover_nws_grid()`, `get_open_meteo_current()`)

**Variables:**
- `snake_case` for variables (e.g., `config`, `client`, `response_data`)
- `UPPER_SNAKE_CASE` for constants with `Final` type hint (e.g., `TRAIN_POSITION_URL`, `MAX_RETRY_ATTEMPTS`)
- `_leading_underscore` for private attributes (e.g., `_cache_file`, `_ttl`, `_fetch_fn`)

**Types:**
- `PascalCase` for classes (e.g., `CachedData`, `BaseDaemon`, `StorageBackend`)
- `PascalCase` for interfaces (no `I` prefix)
- No special pattern for type aliases (use descriptive names)

## Code Style

**Formatting:**
- Ruff formatter (`pyproject.toml` lines 157-162)
- Line length: 88 characters (`pyproject.toml` line 115)
- Indentation: 4 spaces (`pyproject.toml` line 160)
- Quotes: Double quotes for all strings (`pyproject.toml` line 159)
- Semicolons: Not used

**Linting:**
- Ruff linter with "ALL" rules selected (`pyproject.toml` lines 114-162)
- Selective ignores for justified violations (`pyproject.toml` lines 119-149)
- Run: `ruff check src` or via pre-commit hooks
- Pre-commit: `.pre-commit-config.yaml` (Ruff with `--fix`)

## Import Organization

**Order:**
1. `from __future__ import annotations` (first line after docstring)
2. Standard library imports (e.g., `import os`, `from datetime import datetime`)
3. Third-party imports (e.g., `import httpx`, `import stamina`)
4. Own module imports with `### OWN MODULES` comment marker

**Grouping:**
- Blank line between import groups
- Alphabetical within each group (Ruff auto-sorts)

**Path Aliases:**
- None (no custom import paths)

**Examples:**
```python
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Final

import httpx
import stamina

### OWN MODULES
from cta_eta.data_collection.config import load_config
from cta_eta.data_collection.logging import get_logger
```

## Error Handling

**Patterns:**
- Construct error message before raising for clarity
- Raise built-in exceptions (ValueError, OSError, etc.)
- Use stamina retry decorator for transient failures
- Catch exceptions at boundaries (daemon main loop, API clients)

**Example:**
```python
# Good - message constructed first
if not periods:
    msg = "No forecast periods returned from NWS API"
    raise ValueError(msg)

# Bad - inline message
raise ValueError("No forecast periods returned from NWS API")
```

**Error Types:**
- `ValueError` - Invalid input or data
- `OSError` - File system errors
- `httpx.HTTPStatusError` - HTTP errors (caught by stamina)
- No custom exception classes yet

## Logging

**Framework:**
- Python `logging` module with custom formatters (`src/cta_eta/data_collection/logging.py`)
- Dual formatters: `JSONFormatter` (production), `HumanReadableFormatter` (development)

**Patterns:**
- Use `get_logger(__name__)` to get logger for each module
- Use `@log_api_call()` decorator for API functions
- Structured logging with context: `logger.info("Message", extra={"key": "value"})`
- Log levels: `debug`, `info`, `warning`, `error`, `exception`

**Examples:**
```python
from cta_eta.data_collection.logging import get_logger, log_api_call

logger = get_logger(__name__)

@log_api_call(logger)
def fetch_data(client: httpx.Client) -> dict:
    logger.info("Fetching data from API")
    # ...
```

## Comments

**When to Comment:**
- Explain why, not what (code should be self-explanatory)
- Document business logic, algorithms, edge cases
- Avoid obvious comments (e.g., `# increment counter`)
- Use comments for complex logic (e.g., timezone-aware date calculation in `storage.py:317-341`)

**Docstrings:**
- Google-style docstrings for all public functions/classes
- Multi-line with triple double-quotes
- Sections: Summary, Args, Returns, Raises

**Example:**
```python
def discover_nws_grid(client: httpx.Client, latitude: float, longitude: float) -> str:
    """Discover NWS grid identifier from API response.

    Makes API request to NWS Points endpoint and extracts the grid identifier
    (office + X,Y coordinates) for use in subsequent forecast requests.

    Args:
        client: HTTP client for API requests
        latitude: Coordinate latitude
        longitude: Coordinate longitude

    Returns:
        NWS grid identifier (e.g., "LOT,75,73")

    Raises:
        httpx.HTTPStatusError: If API request fails after retries
        ValueError: If response is missing required grid data

    """
```

**TODO Comments:**
- Not used (clean codebase)
- If needed: `# TODO: description` (no username, rely on git blame)

## Function Design

**Size:**
- Keep under 50-100 lines where reasonable
- Extract helpers for complex logic
- Single responsibility principle

**Parameters:**
- Use type hints for all parameters
- Dependency injection for HTTP clients (e.g., `client: httpx.Client`)
- Use `*` for keyword-only arguments where appropriate
- Default values after required parameters

**Return Values:**
- Explicit return type hints
- Return early for guard clauses
- Use None for optional returns (not empty strings)

**Example:**
```python
@stamina.retry(on=httpx.HTTPStatusError, attempts=10)
@log_api_call(logger)
def get_train_positions(client: httpx.Client, *, api_key: str) -> dict[str, Any]:
    """Fetch current train positions from CTA API.

    Args:
        client: HTTP client for requests
        api_key: CTA API key (keyword-only)

    Returns:
        Raw JSON response from API

    Raises:
        httpx.HTTPStatusError: If request fails after retries

    """
    if not api_key:
        msg = "CTA API key is required"
        raise ValueError(msg)

    # ... implementation
```

## Module Design

**Exports:**
- Use explicit `__all__` when needed (rare)
- Most modules export all public functions/classes
- No default exports (Python doesn't have them)

**Organization:**
- Constants at top (after imports)
- Helper functions before public functions
- Classes after functions
- `if __name__ == "__main__":` at bottom (rare, mostly for testing)

**Examples:**
```python
### OWN MODULES
from cta_eta.data_collection.logging import get_logger

logger = get_logger(__name__)

# Constants
OPEN_METEO_URL: Final[str] = "https://api.open-meteo.com/v1/forecast"

# Public functions
@stamina.retry(on=httpx.HTTPStatusError, attempts=1)
@log_api_call(logger)
def discover_open_meteo_grid(client: httpx.Client, latitude: float, longitude: float) -> str:
    # ...
```

## Type Annotations

**Usage:**
- Full type hints on all public functions
- Use modern Python 3.13+ syntax (`list[str]`, not `List[str]`)
- `from __future__ import annotations` to enable forward references
- Generic types with `typing.TypeVar` when needed
- Use `Final` for constants

**TYPE_CHECKING:**
- Conditional imports for type hints only (avoid circular imports)

**Example:**
```python
from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Generator

CONSTANT: Final[str] = "value"

def function(param: str) -> dict[str, Any]:
    # ...
```

## Special Patterns

**Noqa Comments:**
- Use sparingly for justified violations
- Format: `# noqa: RULE_CODE`
- Examples: `# noqa: N801` (intentional lowercase class name for `log_context`)

**Context Managers:**
- Use `with` for resource management (files, HTTP clients)
- Example: `with httpx.Client() as client:` in `api_train_position.py:80`

**Decorators:**
- `@stamina.retry` for retry logic
- `@log_api_call` for API logging
- Stack decorators: retry first, logging second

**Example:**
```python
@stamina.retry(on=httpx.HTTPStatusError, attempts=10)
@log_api_call(logger)
def api_function(client: httpx.Client) -> dict[str, Any]:
    # ...
```

---

*Convention analysis: 2026-01-19*
*Update when patterns change*
