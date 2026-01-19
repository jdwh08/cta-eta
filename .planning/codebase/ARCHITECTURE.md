# Architecture

**Analysis Date:** 2026-01-19

## Pattern Overview

**Overall:** Layered Data Collection Pipeline - Modular system for continuous 24/7 API polling with cloud-native storage

**Key Characteristics:**
- Stateless API clients with dependency injection
- Lazy grid discovery with TTL-based caching
- Cloud-agnostic storage abstraction (local/S3/GCS)
- Daemon lifecycle management with graceful shutdown
- Structured observability through JSON logging

## Layers

**API Client Layer:**
- Purpose: Fetch raw data from external APIs, normalize responses
- Location: `src/cta_eta/data_collection/apis/`
- Contains: `api_train_position.py`, `api_weather_nws.py`, `api_weather_open_meteo.py`, `api_weather_openweathermap.py`, `api_cta_track_shape.py`
- Pattern: Pure functions with `@stamina.retry` decorator
- Depends on: httpx.Client (dependency injection), config, logging
- Used by: Caching layer, orchestration layer (future daemon pollers)

**Caching & Grid Discovery Layer:**
- Purpose: Smart lazy loading - discover API-specific grid identifiers on-demand
- Location: `src/cta_eta/data_collection/storage_cache/`
- Contains: `CachedData[T]` (generic TTL cache), `WeatherGridCache` (lazy grid discovery)
- Pattern: Lazy loading with file persistence across restarts
- Optimization: ~300 CTA stations → ~50 weather grid points (deduplication)
- Depends on: API clients, logging
- Used by: Data collection daemons (future)

**Storage Backend Layer:**
- Purpose: Cloud-agnostic Parquet file writing
- Location: `src/cta_eta/data_collection/storage_cache/storage.py`
- Contains: `StorageBackend` (ABC), `LocalStorage`, `CloudStorage`
- Pattern: Strategy pattern supporting local filesystem, S3, GCS
- Features: Hive-style daily partitioning (`date=YYYY-MM-DD/`), timezone-aware splits at 3:00 AM Chicago time
- Depends on: fsspec, pyarrow
- Used by: Data collection daemons (future)

**Configuration Layer:**
- Purpose: Load operational settings and API keys
- Location: `src/cta_eta/data_collection/config.py`
- Pattern: TOML config file + environment variable secrets merging
- Functions: `load_config()`, `_load_config_from_path()`
- Depends on: tomllib, python-dotenv
- Used by: All layers

**Logging Layer:**
- Purpose: Structured logging with dual formatters
- Location: `src/cta_eta/data_collection/logging.py`
- Contains: `JSONFormatter`, `HumanReadableFormatter`, `log_api_call` decorator
- Pattern: Dual output (JSON for production, human-readable for development)
- Features: Context-aware logging with request correlation
- Depends on: Python logging module
- Used by: All layers

**Orchestration Layer:**
- Purpose: Daemon lifecycle management with signal handling
- Location: `src/cta_eta/data_collection/orchestration/daemon.py`
- Contains: `BaseDaemon` (ABC)
- Pattern: Abstract base class with `run()` and `_get_state()` hooks
- Features: SIGTERM/SIGINT graceful shutdown, state persistence to `.daemon_state/{ClassName}.json`
- Depends on: config, logging
- Used by: Future train/weather poller daemons

## Data Flow

**API Request Lifecycle:**

1. **External APIs** (CTA, NWS, Open-Meteo, Chicago Data, OpenWeatherMap)
   ↓
2. **API Clients** (`api_*.py`)
   - Stateless fetch functions with `@stamina.retry` decorators
   - HTTP client dependency injection for connection pooling
   - Response normalization to flat dictionaries
   ↓
3. **Grid Cache Layer** (for weather APIs)
   - Lazy discovery of API-specific grid identifiers
   - TTL-based refresh (24 hours for weather grids, 30 days for track shapes)
   - File persistence across daemon restarts
   ↓
4. **Normalized Data Records** (Flat, JSON-serializable dicts)
   ↓
5. **Storage Backend** (`StorageBackend` ABC)
   - LocalStorage (dev) or CloudStorage (S3/GCS prod)
   - Append to daily Parquet partitions
   ↓
6. **Parquet Files** (Hive-partitioned)
   ```
   data/
   ├── date=2026-01-17/
   │   ├── train_positions.parquet
   │   ├── weather_nws.parquet
   │   └── weather_open_meteo.parquet
   ├── weather_nws/
   ├── weather_open_meteo/
   └── weather_openweathermap/
   ```

## Key Abstractions

**Dependency Injection:**
- Purpose: HTTP clients passed to API functions for reusable connection pooling
- Examples: All functions in `api_*.py` accept `client: httpx.Client` parameter
- Pattern: Caller manages client lifecycle, function uses client

**Decorator Pattern:**
- Purpose: Cross-cutting concerns (retry logic, logging)
- Examples: `@stamina.retry()`, `@log_api_call()`
- Pattern: Wrap functions with decorators for automatic behavior

**Strategy Pattern:**
- Purpose: Pluggable storage backends
- Examples: `StorageBackend` (ABC) → `LocalStorage`, `CloudStorage`
- Pattern: Common interface, different implementations

**Generic Caching:**
- Purpose: Type-safe caching for flexible data types
- Examples: `CachedData[T]` parameterized type
- Pattern: Generic class with TTL-based expiration

**Template Method:**
- Purpose: Daemon lifecycle with customizable behavior
- Examples: `BaseDaemon` (ABC) with `run()` and `_get_state()` hooks
- Pattern: Abstract base class defines structure, subclasses implement specifics

**Lazy Loading:**
- Purpose: Discover API grids on-demand, not upfront
- Examples: `WeatherGridCache` discovers grid points as stations are encountered
- Pattern: Check cache → fetch if missing → cache result

## Entry Points

**Current:**
- No CLI entry points yet (commented out in `pyproject.toml` lines 82-84)

**Planned:**
- Train position poller daemon (inherits from `BaseDaemon`)
- Weather data poller daemon (inherits from `BaseDaemon`)
- Orchestration daemon (coordinates multiple pollers)

## Error Handling

**Strategy:** Retry at API client level, fail fast at config level

**Patterns:**
- API clients: `@stamina.retry` with exponential backoff (configurable attempts)
- Config loading: Raise exceptions immediately on missing required values
- Cache save: Log errors but don't propagate (daemon continues)
- Daemon: Catch exceptions in `run()`, log, and gracefully shut down

## Cross-Cutting Concerns

**Logging:**
- JSONFormatter for production (machine-parseable)
- HumanReadableFormatter for development (colorized, readable)
- `@log_api_call()` decorator for automatic API request/response logging

**Validation:**
- Environment variables validated at config load time
- API responses normalized and validated in `api_*.py` modules
- Type hints enforced with basedpyright static analysis

**Retry Logic:**
- Stamina decorator with exponential backoff
- Configurable max retry attempts in `config.toml`
- Retries on `httpx.HTTPStatusError`

---

*Architecture analysis: 2026-01-19*
*Update when major patterns change*
