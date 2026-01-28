# Codebase Structure

**Analysis Date:** 2026-01-24

## Directory Layout

```
cta-eta/
├── src/
│   └── cta_eta/
│       └── data_collection/         # Main data collection pipeline (~5,157 lines, 26 modules)
│           ├── apis/                 # External API clients (stateless, async)
│           ├── storage_cache/        # Caching and Parquet storage
│           ├── merging/              # Multi-source data integration
│           ├── orchestration/        # Daemon coordination and lifecycle
│           ├── config.py             # TOML + .env configuration loader
│           ├── logging.py            # Structured JSON + human-readable logging
│           └── utils.py              # Validation and utility functions
├── tests/                            # Test files (mirrors src/ structure)
│   ├── conftest.py                   # Shared pytest fixtures
│   └── data_collection/              # Tests for data_collection modules
│       ├── apis/                     # API client tests
│       ├── storage_cache/            # Cache and storage tests
│       ├── orchestration/            # Daemon and workflow tests
│       └── merging/                  # Data processing tests
├── .planning/                        # Project planning documentation
│   ├── codebase/                     # Codebase analysis (THIS FILE)
│   ├── phases/                       # Phase-based planning
│   └── ROADMAP.md                    # Project roadmap
├── .daemon_state/                    # Runtime daemon state (JSON files)
├── config.toml                       # Operational configuration (version-controlled)
├── .env.template                     # Secrets template (use to create .env)
├── pyproject.toml                    # Python packaging and tool configuration
└── uv.lock                           # Dependency lockfile (UV package manager)
```

## Directory Purposes

**src/cta_eta/data_collection/**
- Purpose: Main data collection pipeline code (Phase 1 of project)
- Contains: APIs, storage, merging, orchestration, config, logging, utilities
- Key files:
  - `config.py` - Hybrid TOML + .env configuration loader with sanitization
  - `logging.py` - Structured JSON logging with decorators (`@log_api_call`, `log_context()`)
  - `utils.py` - `safe_get_nested()`, `validate_lat_lon()`, temperature conversion
- Subdirectories: apis/, storage_cache/, merging/, orchestration/

**src/cta_eta/data_collection/apis/**
- Purpose: External API client functions (stateless, async, with retry logic)
- Contains: HTTP client functions for CTA and weather APIs
- Key files:
  - `api_train_position.py` - CTA Train Tracker API (50k requests/day limit, 8 train lines)
  - `api_cta_stations.py` - Station metadata with GeoJSON (~300 stations)
  - `api_track_shape.py` - Track geometry (MultiLineString segments)
  - `api_weather_nws.py` - National Weather Service (no official rate limit, "reasonable" usage)
  - `api_weather_open_meteo.py` - Open-Meteo (10k calls/day, currently 2,400/day)
  - `api_weather_openweathermap.py` - OpenWeatherMap (fallback)
- Pattern: All functions use `@stamina.retry()` + `@log_api_call()` decorators
- Subdirectories: archive/ (deprecated/experimental APIs)

**src/cta_eta/data_collection/storage_cache/**
- Purpose: Data persistence and caching infrastructure
- Contains: Cache implementations, Parquet writer, cloud storage backends
- Key files:
  - `cache.py` - Generic TTL-based cache (`CachedData[T]` with JSON file backup)
  - `kv_cache.py` - Simple key-value cache
  - `weather_grid_cache.py` - Weather grid mapping caches (`NWSGridCache`, `OpenMeteoGridCache`)
    - Deduplicates ~145 CTA stations to ~50 unique grid points
  - `storage.py` - `ParquetWriter` with `StorageBackend` (local/S3/GCS)
    - Hive-style daily partitioning (3 AM Chicago timezone split)
    - Snappy compression
- Subdirectories: None

**src/cta_eta/data_collection/merging/**
- Purpose: Multi-source data integration and reconciliation
- Contains: Weather data merger with precedence rules
- Key files:
  - `weather_merger.py` - `merge_weather_sources()` (NWS > Open-Meteo > OpenWeatherMap)
    - Handles numpy/pandas type conversion for JSON serialization
- Subdirectories: None

**src/cta_eta/data_collection/orchestration/**
- Purpose: Daemon coordination and lifecycle management
- Contains: Base daemon classes, weather daemon implementation, diagnostics
- Key files:
  - `daemon_async.py` - `AsyncBaseDaemon` (363 lines, core abstraction)
    - Event loop management, interruptible sleep, signal handling
  - `daemon.py` - `BaseDaemon` (synchronous variant)
  - `weather_daemon.py` - `WeatherDaemon` (806 lines, main entry point)
    - 15-minute polling cycle, parallel API fetches
  - `daemon_utils.py` - `ErrorCategory` enum for error classification
  - `diagnostics.py` - `DaemonDiagnostics` with health monitoring
  - `weather_grid_discovery.py` - `OpenMeteoWeatherGridDiscoverer` class
- Subdirectories: None

**tests/**
- Purpose: Test files mirroring src/ structure
- Contains: pytest test files (19 test modules), fixtures, test utilities
- Key files:
  - `conftest.py` - Shared pytest fixtures
    - `httpx_json_response` factory for realistic HTTP response mocking
    - Environment setup for test credentials (fake keys for CI/CD)
- Subdirectories:
  - `tests/data_collection/apis/` - API client tests (AsyncMock patterns)
  - `tests/data_collection/storage_cache/` - Cache and storage tests
  - `tests/data_collection/orchestration/` - Daemon lifecycle tests
  - `tests/data_collection/merging/` - Data processing tests
- Pattern: One `test_*.py` file per source module

**.planning/**
- Purpose: Project planning and documentation
- Contains: Codebase analysis, phase planning, roadmaps
- Subdirectories:
  - `codebase/` - Codebase analysis documents (STACK, ARCHITECTURE, STRUCTURE, CONVENTIONS, etc.)
  - `phases/` - Phase-based planning documents
- Key files: ROADMAP.md

**.daemon_state/**
- Purpose: Runtime state persistence for daemons
- Contains: JSON state files (e.g., `weather_daemon.json`)
- Created: At runtime by daemon lifecycle management
- Format: JSON with last collection time, record counts
- Committed: No (git-ignored)
- Survives: Daemon restarts for monitoring

## Key File Locations

**Entry Points:**
- `src/cta_eta/data_collection/orchestration/weather_daemon.py` - Main daemon entry point
- No CLI entry points currently (commented out in `pyproject.toml`)

**Configuration:**
- `config.toml` - Operational configuration (version-controlled)
  - Sections: features, collection, cache, storage, retry, rate_limits
- `.env` - Secrets (git-ignored, use `.env.template` to create)
- `pyproject.toml` - Python packaging, dependencies, tool configuration
  - Ruff, basedpyright, pytest configuration

**Core Logic:**
- `src/cta_eta/data_collection/apis/` - API client functions (dependency injection pattern)
- `src/cta_eta/data_collection/orchestration/weather_daemon.py` - Main orchestration loop
- `src/cta_eta/data_collection/merging/weather_merger.py` - Multi-source data integration
- `src/cta_eta/data_collection/storage_cache/storage.py` - Parquet storage with cloud backends

**Testing:**
- `tests/` - All test files (parallel structure to src/)
- `tests/conftest.py` - Shared fixtures and test utilities
- Test coverage: 26 Python source files, 19 test files
- Coverage report: `.coverage` file (pytest-cov)

**Documentation:**
- `README.md` - User-facing project documentation
- `CLAUDE.md` - Instructions for Claude Code
- `.planning/codebase/` - Codebase analysis documents

## Naming Conventions

**Files:**
- snake_case for all Python modules: `api_train_position.py`, `weather_daemon.py`, `cache.py`
- API modules prefixed: `api_*.py` (e.g., `api_weather_nws.py`)
- Test files: `test_<module>.py` pattern (e.g., `test_api_weather_open_meteo.py`)
- Config files: lowercase with extension: `config.toml`, `.env.template`

**Directories:**
- snake_case for all directories: `data_collection`, `storage_cache`, `weather_grid_cache`
- Plural for collections: `apis/`, `tests/`
- Subdirectories mirror domain organization

**Special Patterns:**
- `__init__.py` for package initialization (present in all packages)
- `conftest.py` for pytest fixtures (shared test configuration)
- Daemon files: `daemon.py`, `daemon_async.py`, `*_daemon.py`
- Grid discovery/cache: `weather_grid_*.py`

## Where to Add New Code

**New API Client:**
- Primary code: `src/cta_eta/data_collection/apis/api_<name>.py`
- Tests: `tests/data_collection/apis/test_api_<name>.py`
- Pattern: Use `@stamina.retry()` + `@log_api_call()` decorators
- Dependency injection: Pass `httpx.AsyncClient` as parameter
- Add normalization function: `normalize_<name>()` for flat dict output

**New Cache Type:**
- Implementation: `src/cta_eta/data_collection/storage_cache/<name>_cache.py`
- Tests: `tests/data_collection/storage_cache/test_<name>_cache.py`
- Pattern: Use `CachedData[T]` generic or create custom cache class
- JSON file persistence for durability

**New Data Source:**
- API client: `src/cta_eta/data_collection/apis/api_<source>.py`
- Merger logic: Update `src/cta_eta/data_collection/merging/<type>_merger.py`
- Tests: `tests/data_collection/apis/test_api_<source>.py`
- Grid cache: Add to `weather_grid_cache.py` if needed

**New Daemon:**
- Implementation: `src/cta_eta/data_collection/orchestration/<name>_daemon.py`
- Tests: `tests/data_collection/orchestration/test_<name>_daemon.py`
- Pattern: Inherit from `AsyncBaseDaemon`, implement `async def run()`
- State persistence: Create JSON state file in `.daemon_state/`

**Utilities:**
- Shared helpers: `src/cta_eta/data_collection/utils.py` (or new utility module)
- Type definitions: Inline in modules (no separate types module)
- Validation functions: Add to `utils.py`

## Special Directories

**.daemon_state/**
- Purpose: Runtime daemon state persistence
- Source: Created at runtime by `AsyncBaseDaemon._save_state()`
- Committed: No (git-ignored)
- Format: JSON files with daemon-specific state
- Example: `weather_daemon.json` with last collection time, record counts

**.planning/**
- Purpose: Project planning and documentation
- Source: Human-written planning docs, codebase analysis
- Committed: Yes (version-controlled)
- Organization:
  - `codebase/` - Analysis documents (STACK, ARCHITECTURE, etc.)
  - `phases/` - Phase-based planning documents
- Updated: When structure or architecture changes

**tests/**
- Purpose: Test files for all source code
- Source: Human-written pytest test files
- Committed: Yes (version-controlled)
- Pattern: Exact mirror of `src/` directory structure
- Coverage: pytest-cov generates `.coverage` file

**src/cta_eta/data_collection/apis/archive/**
- Purpose: Deprecated or experimental API clients
- Source: Historical API implementations
- Committed: Yes (for reference)
- Status: Not actively maintained

---

*Structure analysis: 2026-01-24*
*Update when directory structure changes*
