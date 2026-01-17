# Coding Conventions

**Analysis Date:** 2026-01-17

## Naming Patterns

**Files:**
- Snake_case for all Python files
- Examples: `api_stations_weather.py`, `api_train_position.py`, `api_track_shape.py`, `lint.py`
- Located in `src/cta_eta/` directory structure

**Functions:**
- Snake_case for all functions
- Action verb prefix pattern: `get_stations()`, `get_weather()`, `get_train_position()`
- Examples: `run()`, `main()` in `devtools/lint.py`

**Variables:**
- Snake_case for all variables
- Descriptive names: `stations_url`, `client`, `weather_info`, `stations_list`, `stations_weather`
- Type-annotated: `stations_list: list[dict[str, str | float]]`

**Constants:**
- UPPER_SNAKE_CASE for all constants
- Marked with `Final` type annotation: `CTA_LINES: Final[list[str]]`
- Examples: `CTA_LINES` in `src/cta_eta/api_train_position.py:17`, `SRC_PATHS`, `DOC_PATHS` in `devtools/lint.py`

**Types:**
- Modern Python 3.13+ built-in generic syntax
- No typing module imports (List, Dict) - uses built-ins (list, dict)
- Union types with `|` operator (PEP 604): `str | float`, `str | int`

## Code Style

**Formatting:**
- Indentation: 4 spaces (standard Python)
- Quotes: Double quotes for strings throughout
- Line length: Not explicitly enforced (ruff default ~88-100 chars)
- Formatting tool: ruff-format (configured in `.pre-commit-config.yaml`)

**Linting:**
- Tool: ruff (latest version, `.pre-commit-config.yaml`)
- Runs with `--fix` flag for auto-fixing
- Additional tools: basedpyright (type checking), codespell (spell checking)
- Run: `uv run python devtools/lint.py`

**Type Hints:**
- Required for all function signatures
- Modern Python 3.13+ syntax (built-in generics)
- Examples:
  - `def get_stations() -> list[dict[str, str | float]]:`
  - `def get_weather(latitude: float, longitude: float) -> dict[str, str | float]:`
  - `def run(cmd: list[str]) -> int:`
- Module-level variables also type-hinted: `CTA_LINES: Final[list[str]]`

## Import Organization

**Order:**
1. Standard library (os, time, csv, subprocess, sys)
2. Third-party packages (httpx, stamina, dotenv, rich, funlog)
3. Local modules (none currently - no internal imports)
4. Type imports (from typing import Final)

**Grouping:**
- Blank lines between groups (standard, third-party, local)
- No explicit alphabetical sorting within groups
- Examples from `src/cta_eta/api_train_position.py`:
  ```python
  import os
  from typing import Final

  import httpx
  import stamina
  from dotenv import load_dotenv
  ```

**Path Aliases:**
- None defined (no complex package structure yet)

## Error Handling

**Patterns:**
- Retry decorator: `@stamina.retry(on=httpx.HTTPStatusError, attempts=10)`
- HTTP status checking: `.raise_for_status()` after requests
- Try/except with specific exceptions: `subprocess.CalledProcessError`, `KeyboardInterrupt`
- Example from `devtools/lint.py`:
  ```python
  try:
      subprocess.run(cmd, text=True, check=True)
  except KeyboardInterrupt:
      rprint("[yellow]Keyboard interrupt - Cancelled[/yellow]")
  except subprocess.CalledProcessError as e:
      rprint(f"[bold red]Error: {e}[/bold red]")
  ```

**Error Types:**
- Raise exceptions with `.raise_for_status()` on HTTP errors
- Retry automatically on `httpx.HTTPStatusError` (up to 10 attempts)
- Return error codes from CLI tools (`def run(cmd: list[str]) -> int:`)

## Logging

**Framework:**
- Console output with rich library in development tools (`rich.print`, `rprint`)
- Decorator logging: `@log_calls(level="warning", show_timing_only=True)` from funlog

**Patterns:**
- Development: `rprint()` for formatted console output in `devtools/lint.py`
- Production: No logging framework currently (early stage)

## Comments

**When to Comment:**
- Section headers: `### VERY ROUGH CODE TO GET CTA STATIONS AND THEIR WEATHER`
- Section separators: `########################################################`
- Explanatory notes: `# note that we should check the units for dewpoint...`
- Example output: Multi-line comments documenting API responses

**TODO/NOTE Conventions:**
- `TODO(username):` for attributed tasks
  - Example: `# TODO(jwang15): Every X time period (1 day or longer)...` in `src/cta_eta/api_stations_weather.py`
- `NOTE(username):` for explanatory notes
  - Example: `# NOTE(jdwh08):` in `src/cta_eta/api_stations_weather.py`

**Docstrings:**
- Triple double-quotes: `"""Linting and formatting utilities."""`
- Minimal usage currently (early development)
- Example: `devtools/lint.py` has module docstring

**Lint Suppression:**
- Ruff noqa comments: `# noqa: INP001`, `# noqa: S603`
- Used in `devtools/lint.py` for specific rule exceptions

## Function Design

**Size:**
- Keep functions focused and concise
- Current functions: 10-30 lines typically
- Extract helpers when complexity grows (not yet needed)

**Parameters:**
- Type-hinted parameters: `latitude: float`, `longitude: float`
- Environment variables via `os.getenv()` rather than parameters
- Example: `def get_train_position(line: str) -> dict[str, str | float]:`

**Return Values:**
- Explicit type hints for returns
- Complex types: `dict[str, str | float]`, `list[dict[str, str | float]]`
- Return code pattern: `def main() -> int:` in `devtools/lint.py`

## Module Design

**Exports:**
- No explicit exports (no `__all__`)
- Functions defined at module level
- No package initialization (`__init__.py`) yet

**Module Structure:**
- Imports at top
- Constants/configuration after imports
- Function definitions
- Module-level execution code at bottom (in data collection scripts)

**Decorators:**
- `@stamina.retry(on=httpx.HTTPStatusError, attempts=10)` - API resilience
- `@log_calls(level="warning", show_timing_only=True)` - Timing in dev tools

---

*Convention analysis: 2026-01-17*
*Update when patterns change*
