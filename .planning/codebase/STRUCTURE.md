# Codebase Structure

**Analysis Date:** 2026-01-22

## Directory Layout

```
cta-eta/
├── src/
│   └── cta_eta/
│       └── data_collection/         # Main data collection pipeline
│           ├── apis/                 # External API clients
│           ├── storage_cache/        # Caching and storage
│           ├── merging/              # Data integration
│           ├── orchestration/        # Daemon coordination
│           ├── config.py             # Configuration loader
│           └── logging.py            # Structured logging
├── tests/                            # Test files (mirrors src/)
│   ├── conftest.py                   # Shared pytest fixtures
│   └── data_collection/              # Tests for data_collection
├── .planning/                        # Project planning docs
│   ├── codebase/                     # Codebase analysis (THIS FILE)
│   └── phases/                       # Phase-based planning
├── .daemon_state/                    # Runtime daemon state persistence
├── config.toml                       # Operational configuration
├── .env.template                     # Secrets template
├── pyproject.toml                    # Python packaging and tool config
└── uv.lock                           # Dependency lockfile (341MB)
```

## Directory Purposes

**src/cta_eta/data_collection/**
- Purpose: Main data collection pipeline code
- Contains: APIs, storage, merging, orchestration, config, logging
- Key files:
  - `config.py` - Hybrid TOML + .env configuration loader
  - `logging.py` - Structured JSON logging with decorators
- Subdirectories: apis/, storage_cache/, merging/, orchestration/

**src/cta_eta/data_collection/apis/**
- Purpose: External API client functions (stateless, async)
- Contains: HTTP client functions with retry logic for all external APIs
- Key files:
  - `api_train_position.py` - CTA Train Tracker API client
  - `api_weather_nws.py` - National Weather Service API
  - `api_weather_open_meteo.py` - Open-Meteo API
  - `api_weather_openweathermap.py` - OpenWeatherMap (fallback)
  - `api_cta_stations.py` - Chicago Data Portal (station metadata)
  - `api_track_shape.py` - Chicago Data Portal (track geometry)
- Pattern: All functions use `@stamina.retry()` + `@log_api_call()` decorators
- Subdirectories: archive/ (experimental/deprecated APIs)

**src/cta_eta/data_collection/storage_cache/**
- Purpose: Data persistence and caching infrastructure
- Contains: Cache implementations, Parquet writer, cloud storage backends
- Key files:
  - `cache.py` - Generic TTL-based cache (CachedData[T])
  - `kv_cache.py` - Key-value cache with per-entry TTL
  - `weather_grid_cache.py` - Weather grid mapping caches (NWS, Open-Meteo, OWM)
  - `storage.py` - ParquetWriter with cloud-agnostic StorageBackend
- Subdirectories: None

**src/cta_eta/data_collection/merging/**
- Purpose: Multi-source data integration
- Contains: Weather data merger with precedence rules
- Key files:
  - `weather_merger.py` - Merge NWS + Open-Meteo + OWM with precedence
- Subdirectories: None

**src/cta_eta/data_collection/orchestration/**
- Purpose: Daemon coordination and lifecycle management
- Contains: Base daemon classes, weather daemon, diagnostics
- Key files:
  - `daemon_async.py` - AsyncBaseDaemon (primary base class)
  - `daemon.py` - BaseDaemon (sync variant)
  - `weather_daemon.py` - WeatherDaemon (main entry point, 863 lines)
  - `diagnostics.py` - DaemonDiagnostics with span timing
- Subdirectories: None

**tests/**
- Purpose: Test files mirroring src/ structure
- Contains: pytest test files, fixtures, test utilities
- Key files:
  - `conftest.py` - Shared pytest fixtures (httpx_json_response factory)
- Subdirectories: Mirrors `src/cta_eta/data_collection/` structure
  - `tests/data_collection/apis/` - API client tests
  - `tests/data_collection/storage_cache/` - Cache and storage tests
  - `tests/data_collection/orchestration/` - Daemon tests
  - `tests/data_collection/merging/` - Merger tests

**.planning/**
- Purpose: Project planning and documentation
- Contains: Codebase analysis, phase planning, roadmaps
- Subdirectories:
  - `codebase/` - Codebase analysis documents (STACK, ARCHITECTURE, etc.)
  - `phases/` - Phase-based planning documents

**.daemon_state/**
- Purpose: Runtime state persistence for daemons
- Contains: JSON state files for daemon resume on restart
- Files: `<daemon_name>.json` (created at runtime)
- Committed: No (git-ignored)

## Key File Locations

**Entry Points:**
- `src/cta_eta/data_collection/orchestration/weather_daemon.py` - Main daemon entry point

**Configuration:**
- `config.toml` - Operational configuration (version-controlled)
- `.env` - Secrets (git-ignored, use `.env.template`)
- `pyproject.toml` - Python packaging, dependencies, tool config

**Core Logic:**
- `src/cta_eta/data_collection/apis/` - API client functions
- `src/cta_eta/data_collection/orchestration/weather_daemon.py` - Main orchestration
- `src/cta_eta/data_collection/merging/weather_merger.py` - Data integration
- `src/cta_eta/data_collection/storage_cache/storage.py` - Parquet storage

**Testing:**
- `tests/` - All test files
- `tests/conftest.py` - Shared fixtures
- Test coverage: 23 Python source files, 19 test files

**Documentation:**
- `README.md` - User-facing project documentation
- `CLAUDE.md` - Instructions for Claude Code
- `.planning/codebase/` - Codebase analysis documents

## Naming Conventions

**Files:**
- snake_case for all Python modules: `api_train_position.py`, `weather_daemon.py`
- Test files: `test_<module>.py` pattern only (no `*_test.py`)
- Config files: lowercase with extension: `config.toml`, `.env.template`

**Directories:**
- snake_case for all directories: `data_collection`, `storage_cache`
- Plural for collections: `apis/`, `tests/`

**Special Patterns:**
- `__init__.py` for package initialization (present in all packages)
- `conftest.py` for pytest fixtures (shared test configuration)
- API modules prefixed: `api_*.py` (e.g., `api_train_position.py`)

## Where to Add New Code

**New API Client:**
- Primary code: `src/cta_eta/data_collection/apis/api_<name>.py`
- Tests: `tests/data_collection/apis/test_api_<name>.py`
- Pattern: Use `@stamina.retry()` + `@log_api_call()` decorators
- Dependency injection: Pass `httpx.AsyncClient` as parameter

**New Cache Type:**
- Implementation: `src/cta_eta/data_collection/storage_cache/<name>_cache.py`
- Tests: `tests/data_collection/storage_cache/test_<name>_cache.py`
- Pattern: Inherit from CachedData[T] or create custom cache class

**New Data Source:**
- API client: `src/cta_eta/data_collection/apis/api_<source>.py`
- Merger logic: Update `src/cta_eta/data_collection/merging/<type>_merger.py`
- Tests: `tests/data_collection/apis/`, `tests/data_collection/merging/`

**New Daemon:**
- Implementation: `src/cta_eta/data_collection/orchestration/<name>_daemon.py`
- Tests: `tests/data_collection/orchestration/test_<name>_daemon.py`
- Pattern: Inherit from AsyncBaseDaemon, implement `async def run()`

**Utilities:**
- Shared helpers: `src/cta_eta/data_collection/<utility>.py`
- Type definitions: Inline or in `__init__.py` (no separate types module)

## Special Directories

**.daemon_state/**
- Purpose: Runtime daemon state persistence
- Source: Created at runtime by daemon lifecycle management
- Committed: No (git-ignored)
- Format: JSON files with daemon state for resume after restart

**.planning/**
- Purpose: Project planning and documentation
- Source: Human-written (planning docs, roadmaps, phase plans)
- Committed: Yes (version-controlled)
- Organization: codebase/ (analysis), phases/ (planning)

**tests/**
- Purpose: Test files for all source code
- Source: Human-written pytest test files
- Committed: Yes (version-controlled)
- Pattern: Mirrors `src/` directory structure exactly

---

*Structure analysis: 2026-01-22*
*Update when directory structure changes*
