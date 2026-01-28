# Architecture

**Analysis Date:** 2026-01-24

## Pattern Overview

**Overall:** Layered Services + Async Daemon Orchestration (Data Collection System)

**Key Characteristics:**
- Python-based async data collection pipeline for CTA train position and weather data
- Long-running daemon processes with 24/7 polling capability (~5,157 lines across 26 modules)
- Cloud-agnostic storage (Parquet files with S3/GCS/local support)
- Stateless API clients with retry resilience and rate limiting
- Event-driven architecture optimized for continuous external API polling

## Layers

**API Layer:**
- Purpose: Stateless async HTTP clients for external data sources
- Contains: CTA Train Tracker, weather APIs (NWS, Open-Meteo, OpenWeatherMap), station metadata
- Depends on: httpx for async HTTP, stamina for retry logic, config for rate limits
- Used by: Daemon orchestration layer
- Location: `src/cta_eta/data_collection/apis/`
- Key files:
  - `api_train_position.py` - CTA Train Tracker API (50k requests/day limit)
  - `api_cta_stations.py` - Station metadata with GeoJSON coordinates (~300 stations)
  - `api_track_shape.py` - Track geometry with MultiLineString segments
  - `api_weather_nws.py` - National Weather Service forecasts (no official rate limit)
  - `api_weather_open_meteo.py` - Open-Meteo weather (10k calls/day, currently 2,400/day)
  - `api_weather_openweathermap.py` - OpenWeatherMap fallback
- Pattern: All functions use `@stamina.retry()` + `@log_api_call()` decorators with dependency injection

**Storage/Cache Layer:**
- Purpose: Persistence and TTL-based caching with deduplication
- Contains: Parquet writers, cloud storage backends, cached data containers, grid mappings
- Depends on: pyarrow/parquet for columnar storage, fsspec/s3fs/gcsfs for cloud
- Used by: Orchestration layer for data persistence
- Location: `src/cta_eta/data_collection/storage_cache/`
- Key abstractions:
  - `CachedData[T]` (`cache.py`) - Generic TTL cache with JSON file backup, lazy refresh
  - `StorageBackend` (ABC) + `LocalStorage`/`CloudStorage` (`storage.py`)
  - `ParquetWriter` - Hive-style daily partitioning (3 AM Chicago timezone-aware split, Snappy compression)
  - `NWSGridCache`, `OpenMeteoGridCache` (`weather_grid_cache.py`) - Deduplicates ~145 stations to ~50 grid points

**Orchestration Layer:**
- Purpose: Stateful daemon lifecycle management with signal handling
- Contains: Base daemon classes, weather polling daemon, grid discovery, diagnostics
- Depends on: API layer, Storage layer, Config, Logging
- Used by: Application entry points (programmatic instantiation)
- Location: `src/cta_eta/data_collection/orchestration/`
- Key classes:
  - `BaseDaemon` (`daemon.py`) - Synchronous daemon base with signal handling (SIGTERM/SIGINT)
  - `AsyncBaseDaemon` (`daemon_async.py`) - Async daemon base (363 lines, core abstraction)
  - `WeatherDaemon` (`weather_daemon.py`) - Concrete weather polling (806 lines)
  - `DaemonDiagnostics` (`diagnostics.py`) - Health monitoring and metrics reporting
  - `OpenMeteoWeatherGridDiscoverer` (`weather_grid_discovery.py`) - Grid point discovery

**Data Processing Layer:**
- Purpose: Multi-source data reconciliation and normalization
- Contains: Weather merger, response normalization functions
- Depends on: pandas for data manipulation, numpy for type conversion
- Used by: Orchestration layer after API responses
- Location: `src/cta_eta/data_collection/merging/`
- Key functions:
  - `merge_weather_sources()` (`weather_merger.py`) - Applies precedence rules (NWS > Open-Meteo > OpenWeatherMap)
  - Handles numpy/pandas type conversion for JSON serialization

**Configuration & Utilities:**
- Purpose: Cross-cutting concerns (config, logging, validation)
- Contains: TOML config loader, structured logging, utility functions
- Depends on: tomllib (stdlib), python-dotenv for secrets
- Used by: All layers
- Location: Root of `src/cta_eta/data_collection/`
- Key modules:
  - `config.py` - Hybrid TOML + .env loader with automatic sanitization of sensitive values
  - `logging.py` - JSON + human-readable formatters, `@log_api_call()` decorator, context variables
  - `utils.py` - `safe_get_nested()`, `validate_lat_lon()`, temperature conversion

## Data Flow

**Weather Collection Pipeline (every 15-30 minutes):**

1. **Initialization**
   - Load stations cache (CachedData) via `get_stations_cache()` in `api_cta_stations.py`
   - Load NWS grid cache via `NWSGridCache` in `weather_grid_cache.py`
   - Load Open-Meteo grid cache via `OpenMeteoGridCache`
   - Initialize ParquetWriter for storage in `storage.py`

2. **Polling Loop**
   - WeatherDaemon enters continuous polling cycle
   - Deduplicate ~145 stations to ~50 unique grid points via grid caches
   - Parallel fetch: NWS + Open-Meteo via `asyncio.gather(return_exceptions=True)`
     - NWS: `get_nws_hourly_forecast()` in `api_weather_nws.py`
     - Open-Meteo: `get_open_meteo_current()` in `api_weather_open_meteo.py`
   - Rate limiting enforced via `aiometer` library
   - Retry resilience: `@stamina.retry` with exponential backoff

3. **Error Handling**
   - Classify errors: transient vs. fatal (`ErrorCategory` enum in `daemon_utils.py`)
   - Partial failure recovery: One source failing doesn't stop collection
   - Log warnings and continue

4. **Data Merging**
   - `merge_weather_sources()` in `weather_merger.py`
   - Apply precedence rules for conflicting fields
   - Convert numpy → Python native types

5. **Storage Persistence**
   - Write records to Parquet with daily partition via `ParquetWriter.append_batch()`
   - Hive-style partitioning: `dataset=weather/date=YYYY-MM-DD/`
   - Cloud/local storage via `fsspec` abstraction

6. **State Persistence**
   - Save last collection time and record count to JSON state file in `.daemon_state/`
   - Persist across daemon restarts for monitoring

**State Management:**
- File-based state persistence in `.daemon_state/<daemon_name>.json`
- Grid mappings cached with per-entry TTL in JSON files
- No in-memory state between restarts
- Each collection cycle is independent (stateless polling)

## Key Abstractions

**Dependency Injection Pattern:**
- Purpose: Enable testability and connection pooling
- Examples: All API functions require `httpx.AsyncClient` parameter
- Pattern: `async def get_train_positions(client: httpx.AsyncClient) -> dict[str, Any]`
- Benefits: Proper resource cleanup, pooled connections, mock injection for tests

**CachedData[T]:**
- Purpose: Generic TTL cache with file persistence
- Examples: `get_stations_cache()` (7-day TTL), `get_track_geometry_cache()` (30-day TTL)
- Pattern: In-memory cache dict + JSON file backup, lazy refresh when TTL expires
- Type-safe: Generic container preserves type information
- Location: `cache.py`

**AsyncBaseDaemon:**
- Purpose: Base class for long-running async daemons
- Examples: `WeatherDaemon` extends `AsyncBaseDaemon`
- Pattern: Event loop management, interruptible sleep, signal handling
- Features: Graceful shutdown, state persistence, health diagnostics
- Lifecycle: `start()` → `run()` → `stop()` with signal handlers
- Location: `daemon_async.py`

**Decorator-Based Cross-Cutting:**
- Purpose: Separation of concerns (retry, logging, rate limiting)
- Examples:
  ```python
  @stamina.retry(on=httpx.HTTPStatusError, attempts=MAX_RETRY_ATTEMPTS)
  @log_api_call(logger)
  async def get_open_meteo_current(client: httpx.AsyncClient) -> dict[str, Any]:
      ...
  ```
- Stacking order matters for proper instrumentation
- Benefits: Clean business logic, centralized error handling

**Abstract Base Classes:**
- Purpose: Extensibility for new implementations
- Examples:
  - `StorageBackend` (ABC) → `LocalStorage`, `CloudStorage`
  - `BaseDaemon` → `AsyncBaseDaemon` → `WeatherDaemon`
- Pattern: Template method pattern with concrete implementations

**TTL-Based Caching with File Persistence:**
- Purpose: Reduce API calls and survive restarts
- Pattern:
  ```python
  CachedData[T](
      cache_file: Path,
      ttl: int,
      fetch_fn: Callable[[], T]
  )
  ```
- In-memory cache + JSON file backup
- Automatic refresh on expiry
- Used by: Stations cache, weather grid caches

**Multi-Source Data Reconciliation:**
- Purpose: Combine multiple weather sources with precedence
- Pattern: API clients return normalized dicts → `merge_weather_sources()` applies rules
- Handles: Type conversions (numpy → Python native), missing field defaults
- Precedence: NWS > Open-Meteo > OpenWeatherMap

**State Machines for Daemon Lifecycle:**
- Purpose: Manage daemon state transitions
- Pattern: `running: bool` flag controls main loop, signal handlers trigger graceful shutdown
- State saved to JSON before exit, loaded from JSON on restart

## Entry Points

**Current Entry Points:**
- Location: No CLI entry points (commented out in `pyproject.toml`)
- Execution model: Daemons instantiated programmatically
- Example:
  ```python
  daemon = WeatherDaemon(config, logger)
  daemon.start()  # Blocks until SIGTERM/SIGINT
  ```

**Future Entry Point Candidates:**
- CLI for starting/stopping daemons
- Scheduled jobs for batch processing
- API server for data retrieval
- Model training orchestration (Phase 2)

## Error Handling

**Strategy:** Partial failure recovery with classified errors

**Patterns:**
- Services throw Error with descriptive messages
- Daemons catch at top level, classify as transient vs. fatal (`ErrorCategory` enum)
- Partial failures don't stop entire collection cycle
- Retry logic: `@stamina.retry` decorator with configurable attempts (default: 10)
- Logging: Errors logged with structured context before handling

**Error Classification:**
- Transient: HTTP 5xx, timeouts, rate limits → retry with exponential backoff
- Fatal: HTTP 4xx (except 429), validation errors → log and skip
- Partial: One API source fails → merge available data, log warning

**Retry Configuration:**
- Default: 10 attempts with exponential backoff via stamina
- Configurable via `config.toml` retry section
- Only retries on `httpx.HTTPStatusError` (4xx/5xx responses)

## Cross-Cutting Concerns

**Logging:**
- Structured JSON logging for production (ELK-ready) via `JSONFormatter`
- Human-readable formatter for development/console via `HumanReadableFormatter`
- Context variables for request correlation (thread-safe using `contextvars`)
- Decorators: `@log_api_call()` for API instrumentation
- Context managers: `log_context()` for scoped context
- Location: `logging.py`

**Validation:**
- Input validation at API boundaries: `validate_lat_lon()` for geographic coordinates
- Safe nested dict access: `safe_get_nested()` with descriptive errors
- Type checking: basedpyright strict mode with Python 3.13+ features

**Rate Limiting:**
- Per-API configuration in `config.toml` (`[rate_limits]` section)
- Enforcement: `aiometer` library for concurrent request limiting
- Configuration examples:
  - NWS: `max_per_second = 5` (no official limit, "reasonable" usage)
  - Open-Meteo: varies by grid point density

**Configuration:**
- Hybrid TOML (`config.toml`) + .env secrets model
- `load_config()` in `config.py` merges both sources
- Automatic sanitization of sensitive values in logs
- Sections: features, collection, cache, storage, retry, rate_limits

**Diagnostics:**
- `DaemonDiagnostics` class in `diagnostics.py`
- Health monitoring and metrics reporting
- Span timing for performance analysis

---

*Architecture analysis: 2026-01-24*
*Update when major patterns change*
