# Codebase Structure

**Analysis Date:** 2026-01-19

## Directory Layout

```
cta-eta/
├── src/cta_eta/                              # Main source package
│   ├── __init__.py                          # Package root
│   └── data_collection/                     # Data collection subsystem
│       ├── __init__.py                      # Package documentation
│       ├── config.py                        # Hybrid config loader (TOML + env)
│       ├── logging.py                       # Structured logging (JSON/human)
│       │
│       ├── apis/                            # API clients (stateless)
│       │   ├── __init__.py
│       │   ├── api_train_position.py        # CTA Train Tracker API client
│       │   ├── api_weather_nws.py           # National Weather Service API
│       │   ├── api_weather_open_meteo.py    # Open-Meteo weather API
│       │   ├── api_weather_openweathermap.py # OpenWeatherMap fallback
│       │   └── api_track_shape.py           # Chicago Open Data track geometry
│       │
│       ├── storage_cache/                   # Storage & caching infrastructure
│       │   ├── __init__.py
│       │   ├── cache.py                     # CachedData[T]: generic TTL cache
│       │   ├── storage.py                   # StorageBackend: local/S3/GCS
│       │   └── weather_grid_cache.py        # WeatherGridCache: lazy discovery
│       │
│       └── orchestration/                   # Daemon lifecycle management
│           ├── __init__.py
│           └── daemon.py                    # BaseDaemon: abstract daemon class
│
├── tests/                                    # Comprehensive test suite
│   ├── conftest.py                          # Pytest fixtures
│   └── data_collection/                     # Tests mirror src structure
│       ├── test_config.py                   # Config loader tests
│       ├── test_logging.py                  # Logging tests
│       │
│       ├── apis/                            # API client tests
│       │   ├── test_api_weather_nws.py
│       │   └── test_api_weather_open_meteo.py
│       │
│       ├── orchestration/                   # Daemon tests
│       │   └── test_daemon.py
│       │
│       └── storage_cache/                   # Cache/storage tests
│           ├── test_cache.py
│           └── test_storage.py
│
├── data/                                     # Collected raw data (Parquet)
│   ├── cta_track_shape.json                 # Static reference data
│   ├── stations_weather.csv                 # Station-weather mapping
│   ├── date=2026-01-17/                     # Daily partitions
│   │   └── train_positions.parquet          # Time-series data
│   ├── weather_nws/                         # Weather data by provider
│   │   └── date=2026-01-17/
│   ├── weather_open_meteo/
│   │   └── date=2026-01-17/
│   └── weather_openweathermap/
│       └── date=2026-01-17/
│
├── devtools/                                 # Development utilities
├── .daemon_state/                           # Daemon state persistence
│   └── {DaemonClassName}.json              # Last known state per daemon
│
├── .planning/                               # GSD project planning
│   ├── codebase/                            # Codebase analysis documents
│   │   ├── STACK.md                         # Technology stack
│   │   ├── ARCHITECTURE.md                  # System design
│   │   ├── STRUCTURE.md                     # Directory layout (this file)
│   │   ├── CONVENTIONS.md                   # Code style
│   │   ├── TESTING.md                       # Test patterns
│   │   ├── INTEGRATIONS.md                  # External services
│   │   └── CONCERNS.md                      # Technical debt
│   └── phases/                              # Execution phase plans
│
├── config.toml                              # Operational configuration (versioned)
├── .env.template                            # Environment secrets template
├── pyproject.toml                           # Python project metadata, dependencies
├── uv.lock                                  # Dependency lockfile (269 KB)
├── .python-version                          # Python 3.13
├── .pre-commit-config.yaml                  # Ruff linting/formatting hooks
├── .gitignore                               # Git exclusions
├── README.md                                # Project documentation
└── CLAUDE.md                                # Instructions for Claude Code
```

## Directory Purposes

**src/cta_eta/**
- Purpose: Main source package
- Contains: `__init__.py`, `data_collection/` subsystem
- Key files: Package root
- Subdirectories: `data_collection/`

**src/cta_eta/data_collection/**
- Purpose: Data collection subsystem (Phase 1 of project)
- Contains: Configuration, logging, API clients, storage, orchestration
- Key files: `config.py`, `logging.py`
- Subdirectories: `apis/`, `storage_cache/`, `orchestration/`

**src/cta_eta/data_collection/apis/**
- Purpose: API clients for external data sources
- Contains: Stateless fetch + normalization functions
- Key files: `api_train_position.py` (CTA), `api_weather_nws.py` (NWS), `api_weather_open_meteo.py`, `api_weather_openweathermap.py`, `api_track_shape.py` (Chicago Data)
- Subdirectories: `archive/` (old code)

**src/cta_eta/data_collection/storage_cache/**
- Purpose: Storage abstraction and caching infrastructure
- Contains: Cloud-agnostic storage, TTL caches, grid discovery
- Key files: `storage.py` (Parquet writer), `cache.py` (generic cache), `weather_grid_cache.py` (lazy grid discovery)
- Subdirectories: None

**src/cta_eta/data_collection/orchestration/**
- Purpose: Daemon lifecycle management
- Contains: Abstract daemon base class
- Key files: `daemon.py` (BaseDaemon with signal handling)
- Subdirectories: None

**tests/**
- Purpose: Comprehensive test suite
- Contains: Unit tests mirroring src structure
- Key files: `conftest.py` (shared fixtures)
- Subdirectories: `data_collection/` (mirrors `src/cta_eta/data_collection/`)

**data/**
- Purpose: Collected raw data (Parquet files)
- Contains: Hive-partitioned Parquet files, static reference data
- Key files: `cta_track_shape.json`, `stations_weather.csv`
- Subdirectories: `date=YYYY-MM-DD/` partitions, `weather_nws/`, `weather_open_meteo/`, `weather_openweathermap/`

**.planning/**
- Purpose: GSD project planning documents
- Contains: Codebase analysis, phase plans
- Key files: `codebase/*.md`, `phases/*.md`
- Subdirectories: `codebase/`, `phases/`

## Key File Locations

**Entry Points:**
- None currently (CLI commented out in `pyproject.toml` lines 82-84)

**Configuration:**
- `config.toml` - Operational configuration (polling intervals, retry settings, storage backends)
- `.env.template` - Environment variable template (API keys, secrets)
- `pyproject.toml` - Python project metadata, dependencies, build configuration
- `.python-version` - Python 3.13

**Core Logic:**
- `src/cta_eta/data_collection/config.py` - Config loading (TOML + env merging)
- `src/cta_eta/data_collection/logging.py` - Structured logging setup
- `src/cta_eta/data_collection/apis/` - All API clients
- `src/cta_eta/data_collection/storage_cache/storage.py` - Parquet writer
- `src/cta_eta/data_collection/orchestration/daemon.py` - Daemon base class

**Testing:**
- `tests/conftest.py` - Shared pytest fixtures
- `tests/data_collection/` - All unit tests (mirror src structure)
- `pyproject.toml` (lines 197-213) - Pytest configuration

**Documentation:**
- `README.md` - User-facing project documentation
- `CLAUDE.md` - Instructions for Claude Code when working in this repo
- `.planning/codebase/` - Codebase analysis documents

## Naming Conventions

**Files:**
- `snake_case.py` - All Python modules (e.g., `api_train_position.py`, `weather_grid_cache.py`)
- `test_*.py` - Test files (e.g., `test_config.py`, `test_api_weather_nws.py`)
- `UPPER_CASE.md` - Important project files (e.g., `README.md`, `CLAUDE.md`)

**Directories:**
- `snake_case` - All directories (e.g., `data_collection`, `storage_cache`, `orchestration`)
- Plural for collections - Where applicable (e.g., `apis/`, `tests/`)

**Special Patterns:**
- `api_*.py` - API client modules
- `test_*.py` - Test modules
- `__init__.py` - Package initialization files
- `.*.yaml` - Hidden YAML configuration files (e.g., `.pre-commit-config.yaml`)

## Where to Add New Code

**New API Client:**
- Primary code: `src/cta_eta/data_collection/apis/api_{provider}.py`
- Tests: `tests/data_collection/apis/test_api_{provider}.py`
- Config: Add API key to `.env.template`, add settings to `config.toml`

**New Daemon:**
- Implementation: `src/cta_eta/data_collection/orchestration/{daemon_name}.py` (inherit from `BaseDaemon`)
- Tests: `tests/data_collection/orchestration/test_{daemon_name}.py`
- State: `.daemon_state/{DaemonClassName}.json` (auto-created)

**New Storage Backend:**
- Implementation: `src/cta_eta/data_collection/storage_cache/storage.py` (new class inheriting from `StorageBackend`)
- Tests: `tests/data_collection/storage_cache/test_storage.py`
- Config: Add backend settings to `config.toml`

**New Cache Type:**
- Implementation: `src/cta_eta/data_collection/storage_cache/cache.py` or new file
- Tests: `tests/data_collection/storage_cache/test_cache.py`

**Utilities:**
- Shared helpers: `src/cta_eta/data_collection/` (e.g., `config.py`, `logging.py`)
- Type definitions: In relevant module files (Python 3.13+ type hints)

## Special Directories

**.daemon_state/**
- Purpose: Daemon state persistence (graceful restart)
- Source: Auto-generated by `BaseDaemon` subclasses
- Committed: No (in `.gitignore`)

**data/**
- Purpose: Collected raw data (Parquet files)
- Source: Generated by storage backend during data collection
- Committed: No (in `.gitignore`)

**.planning/**
- Purpose: GSD project planning and codebase analysis
- Source: Manual and automated (codebase mapping)
- Committed: Yes (project documentation)

**.ruff_cache/, .pytest_cache/, __pycache__/**
- Purpose: Build artifacts and caches
- Source: Auto-generated by tools
- Committed: No (in `.gitignore`)

---

*Structure analysis: 2026-01-19*
*Update when directory structure changes*
