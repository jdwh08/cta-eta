# Codebase Structure

**Analysis Date:** 2026-01-17

## Directory Layout

```
cta-eta/
├── .github/
│   └── workflows/        # CI/CD pipeline
├── .claude/              # Claude IDE settings
├── .cursor/              # Cursor AI rules
├── .vscode/              # VSCode configuration
├── src/
│   └── cta_eta/          # Main source code
├── devtools/             # Development utilities
├── data/                 # Generated data files
├── tests/                # Test directory (empty)
├── .env                  # Environment variables (not in git)
├── .gitignore            # Git ignore rules
├── .pre-commit-config.yaml  # Pre-commit hooks
├── .python-version       # Python 3.13 requirement
├── CLAUDE.md             # Claude AI instructions
├── PLAN.md               # Project roadmap
├── LICENSE               # MIT License
├── pyproject.toml        # Project metadata
├── uv.lock               # UV lockfile
└── stations_weather.csv  # Sample output
```

## Directory Purposes

**src/cta_eta/:**
- Purpose: Main application source code (API clients)
- Contains: Python modules (*.py files)
- Key files:
  - `api_train_position.py` - CTA Train Tracker API client
  - `api_stations_weather.py` - Chicago Data Portal & Open-Meteo client
  - `api_track_shape.py` - Track geometry API documentation
- Subdirectories: None (flat structure, no __init__.py yet)
- Note: No package initialization (__init__.py, __main__.py) - modules are scripts

**devtools/:**
- Purpose: Development tools and utilities
- Contains: `lint.py` - Linting orchestrator
- Key files: `lint.py` - Runs ruff, basedpyright, codespell
- Subdirectories: None

**data/:**
- Purpose: Generated data files from external APIs
- Contains: JSON cache files
- Key files: `cta_track_shape.json` - Track geometry data (155 lines)
- Subdirectories: None

**tests/:**
- Purpose: Test files (framework ready, no tests yet)
- Contains: Empty directory
- Key files: None yet
- Subdirectories: None

**.github/workflows/:**
- Purpose: CI/CD pipeline definitions
- Contains: `ci.yml` - GitHub Actions workflow
- Key files: `ci.yml` - Linting and testing pipeline
- Subdirectories: None

**.vscode/:**
- Purpose: VSCode workspace settings
- Contains: `settings.json`, `launch.json`
- Key files: Editor configuration for Python development
- Subdirectories: None

## Key File Locations

**Entry Points:**
- `devtools/lint.py` - Linting and formatting orchestrator
- `src/cta_eta/api_stations_weather.py` - Data collection script (manual execution)
- `src/cta_eta/api_train_position.py` - Train position fetcher (manual execution)

**Configuration:**
- `pyproject.toml` - Project metadata, dependencies, Python version requirement
- `.python-version` - Python 3.13 enforcement
- `.pre-commit-config.yaml` - Git hook configuration (ruff, codespell, etc.)
- `.env` - Environment variables for API keys (not in git)
- `uv.lock` - UV lockfile for reproducible builds

**Core Logic:**
- `src/cta_eta/api_train_position.py` - CTA API integration
- `src/cta_eta/api_stations_weather.py` - Station + weather data fetching
- `src/cta_eta/api_track_shape.py` - Track shape API documentation

**Testing:**
- `tests/` - Empty directory (no test files yet)
- `.github/workflows/ci.yml` - Configured to run `uv run pytest`

**Documentation:**
- `CLAUDE.md` - Instructions for Claude Code
- `PLAN.md` - 108-line detailed project roadmap
- `LICENSE` - MIT License

**Generated Data:**
- `stations_weather.csv` - Sample output (stations + weather joined)
- `data/cta_track_shape.json` - Track geometry cache

## Naming Conventions

**Files:**
- Snake_case for Python modules: `api_train_position.py`, `api_stations_weather.py`, `lint.py`
- UPPERCASE for important docs: `CLAUDE.md`, `PLAN.md`, `LICENSE`
- Lowercase for config: `pyproject.toml`, `uv.lock`, `.gitignore`

**Directories:**
- Snake_case: `src/`, `cta_eta/`, `devtools/`, `data/`
- Dotfiles: `.github/`, `.vscode/`, `.claude/`, `.cursor/`

**Special Patterns:**
- `api_*.py` - API client modules (descriptive prefix)
- `.pre-commit-config.yaml` - Tool configuration files
- No `__init__.py` or `__main__.py` yet (modules are standalone scripts)

## Where to Add New Code

**New API Client:**
- Primary code: `src/cta_eta/api_{service_name}.py`
- Tests: `tests/test_api_{service_name}.py` (when tests are added)
- Documentation: Update `PLAN.md` with API details

**New Data Processing Module:**
- Implementation: `src/cta_eta/processing_{feature}.py`
- Tests: `tests/test_processing_{feature}.py`
- Future: May create `src/cta_eta/processing/` subdirectory

**New Utility Function:**
- Shared helpers: `src/cta_eta/utils.py` (when needed)
- Geospatial utils: `src/cta_eta/geo_utils.py` (planned for track distance calculations)

**New Model:**
- Implementation: `src/cta_eta/models/` directory (future)
- Tests: `tests/test_models/`
- Config: Model hyperparameters in separate config file

**New Development Tool:**
- Implementation: `devtools/{tool_name}.py`
- Usage: Called from CI or local development

## Special Directories

**data/:**
- Purpose: Cached API responses and generated data
- Source: API client modules write JSON/CSV files here
- Committed: `cta_track_shape.json` is committed (static track data)
- Gitignored: Future parquet files and temporary data

**.venv/:**
- Purpose: Python virtual environment (UV-managed)
- Source: `uv sync` creates this directory
- Committed: No (in `.gitignore`)

---

*Structure analysis: 2026-01-17*
*Update when directory structure changes*
