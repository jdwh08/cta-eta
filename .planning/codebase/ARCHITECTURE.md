# Architecture

**Analysis Date:** 2026-01-22

## Pattern Overview

**Overall:** Data Pipeline with Async Event-Driven Daemon Architecture

**Key Characteristics:**
- Production-grade data collection system for continuous 24/7 operation
- Async polling daemons for long-running collection processes
- Multi-source data integration with fallback mechanisms (NWS → Open-Meteo → OpenWeatherMap)
- Distributed caching to minimize API calls (~145 stations → ~50 grid points)
- Cloud-agnostic storage abstraction (local/S3/GCS)

## Layers

**Daemon Framework Layer:**
- Purpose: Lifecycle management for long-running processes
- Contains: Abstract base classes with signal handling, state persistence, cooperative shutdown
- Files:
  - `src/cta_eta/data_collection/orchestration/daemon_async.py` (AsyncBaseDaemon)
  - `src/cta_eta/data_collection/orchestration/daemon.py` (BaseDaemon - sync variant)
  - `src/cta_eta/data_collection/orchestration/weather_daemon.py` (WeatherDaemon - main entry point)
- Depends on: asyncio, signal handling, JSON state persistence
- Used by: Main daemon implementation (WeatherDaemon)

**API Client Layer:**
- Purpose: HTTP communication with external APIs
- Contains: Stateless async functions for API calls with retry logic
- Files:
  - `src/cta_eta/data_collection/apis/api_train_position.py` (CTA Train Tracker)
  - `src/cta_eta/data_collection/apis/api_weather_nws.py` (National Weather Service)
  - `src/cta_eta/data_collection/apis/api_weather_open_meteo.py` (Open-Meteo)
  - `src/cta_eta/data_collection/apis/api_weather_openweathermap.py` (OpenWeatherMap fallback)
  - `src/cta_eta/data_collection/apis/api_cta_stations.py` (Station metadata)
  - `src/cta_eta/data_collection/apis/api_track_shape.py` (Track geometry)
- Depends on: httpx (AsyncClient), stamina (retry decorator), logging
- Used by: Daemon orchestration layer
- Pattern: All functions use `@stamina.retry()` + `@log_api_call()` decorators

**Storage & Caching Layer:**
- Purpose: Data persistence and API call deduplication
- Contains: Cache implementations, Parquet writers, cloud storage abstraction
- Files:
  - `src/cta_eta/data_collection/storage_cache/cache.py` (TTL-based generic cache)
  - `src/cta_eta/data_collection/storage_cache/kv_cache.py` (Key-value cache)
  - `src/cta_eta/data_collection/storage_cache/weather_grid_cache.py` (Grid mappings)
  - `src/cta_eta/data_collection/storage_cache/storage.py` (Parquet writer, StorageBackend)
- Depends on: pyarrow, fsspec, s3fs, gcsfs, pathlib
- Used by: Daemon orchestration and API clients (for caching)

**Data Merging Layer:**
- Purpose: Multi-source weather data unification
- Contains: Weather data merger with precedence rules
- Files:
  - `src/cta_eta/data_collection/merging/weather_merger.py` (merge_weather_sources)
- Depends on: pandas, numpy
- Used by: Weather daemon after parallel API fetches
- Precedence: NWS > Open-Meteo > OpenWeatherMap

**Configuration Layer:**
- Purpose: Hybrid TOML + environment variable configuration
- Contains: Config loader merging operational settings with secrets
- Files:
  - `src/cta_eta/data_collection/config.py` (load_config)
- Depends on: tomllib (built-in), dotenv
- Used by: All modules requiring configuration
- Pattern: Single `load_config()` function returns nested dict

**Observability Layer:**
- Purpose: Structured logging and diagnostics
- Contains: JSON formatters, log decorators, span timing
- Files:
  - `src/cta_eta/data_collection/logging.py` (structured logging, `@log_api_call`)
  - `src/cta_eta/data_collection/orchestration/diagnostics.py` (DaemonDiagnostics)
- Depends on: logging (built-in), contextvars
- Used by: All layers for observability

## Data Flow

**Weather Collection Cycle (every 15-30 minutes):**

1. Load persisted state (daemon startup)
   - `AsyncBaseDaemon._load_state()` reads `.daemon_state/<daemon_name>.json`
   - Restores grid mappings from cache files

2. Resolve Station → Grid Mappings
   - Load cached mappings from NWSGridCache, OpenMeteoGridCache
   - Cache miss triggers discovery: `discover_nws_grid()`, `discover_open_meteo_grid()`
   - Result: ~145 CTA stations deduplicated to ~50 unique weather grid points

3. Parallel Multi-Source Weather Fetch
   - NWS: `aiometer.run_all(jobs, max_at_once=10, max_per_second=2.0)` with `get_nws_hourly_forecast`
   - Open-Meteo: `aiometer.run_all(jobs, max_at_once, max_per_second)` with `get_open_meteo_current`
   - Both sources fetched via `await asyncio.gather()` for parallel execution

4. Fallback for Failures (optional)
   - OpenWeatherMap called for any grid point where NWS or Open-Meteo failed
   - Deduplicated by grid point to avoid duplicate requests

5. Merge Station-Level Records
   - `merge_weather_sources(nws_data, om_data, owm_data)` with precedence
   - Attach station coordinates, collection timestamp, metadata

6. Storage
   - `ParquetWriter.append_batch()` with daily partition key
   - Hive-style partitioning: `dataset=weather/date=YYYY-MM-DD/`
   - Zero-copy buffering using PyArrow

**State Management:**
- File-based state persistence via `.daemon_state/<daemon_name>.json`
- Grid mappings cached in JSON files with per-entry TTL
- No in-memory state between daemon restarts
- Each collection cycle is independent (stateless polling)

## Key Abstractions

**Daemon:**
- Purpose: Abstract base for long-running processes
- Examples: `AsyncBaseDaemon`, `WeatherDaemon`
- Pattern: Template method with `start()` → `run()` → `stop()` lifecycle
- Lifecycle:
  ```python
  daemon.start()  # Registers signal handlers, loads state
  # Blocks on async run() until SIGTERM/SIGINT
  daemon.stop()   # Cooperative shutdown, saves state
  ```

**Cache:**
- Purpose: TTL-based persistent caching with lazy refresh
- Examples: `CachedData[T]`, `WeatherGridCache`, `NWSGridCache`
- Pattern: Generic cache with JSON file persistence
- Operations: `get()`, `set()`, `delete()`, `is_expired()`

**StorageBackend:**
- Purpose: Cloud-agnostic storage abstraction
- Examples: Local filesystem, S3, GCS via fsspec
- Pattern: Abstract interface with `put()`, `get()`, `list()`
- Implementation: fsspec handles protocol translation (file://, s3://, gcs://)

**API Client Function:**
- Purpose: Stateless HTTP request with retry and logging
- Pattern:
  ```python
  @stamina.retry(on=httpx.HTTPStatusError, attempts=10)
  @log_api_call(logger)
  async def get_xxx(client: httpx.AsyncClient) -> dict[str, Any]:
      # Dependency injection: caller provides client
  ```
- Examples: All functions in `apis/*.py`

**Rate Limiting:**
- Purpose: Enforce API rate limits with async concurrency control
- Examples: `aiometer.run_all()` for batched fetches, `aiometer.amap()` for discovery
- Pattern: max_per_second and max_at_once for provider-specific limits

## Entry Points

**Weather Daemon:**
- Location: `src/cta_eta/data_collection/orchestration/weather_daemon.py`
- Triggers: Manual execution via `python -m` or direct script
- Responsibilities:
  - Initialize HTTP clients and caches
  - Start polling loop with configured intervals
  - Handle SIGTERM/SIGINT for graceful shutdown
  - Persist state on exit

**CLI Entry (if present):**
- Not detected in current analysis

## Error Handling

**Strategy:** Exception bubbling with retry at API client level

**Patterns:**
- API clients: `@stamina.retry()` decorator with exponential backoff (10 attempts)
- Daemon main loop: `try/except Exception` catches all errors, logs, continues
- Partial failures: One API source failing doesn't stop collection (fallback mechanism)
- State persistence: Errors during save logged but don't crash daemon

**Retry Configuration:**
- Default: 10 attempts with exponential backoff
- Configurable via `config.toml` retry section
- Only retries on `httpx.HTTPStatusError` (4xx/5xx responses)

## Cross-Cutting Concerns

**Logging:**
- Structured JSON logging with context variables
- Log decorators: `@log_api_call(logger)` for automatic API timing
- Context manager: `log_context()` for correlation IDs
- Formatters: JSONFormatter (production) + HumanReadableFormatter (dev)

**Rate Limiting:**
- Per-provider limits enforced via aiometer library
- NWS: 2 req/sec soft limit (no official limit)
- Open-Meteo: 0.1 req/sec, max 3 concurrent (10K/day)
- OpenWeatherMap: 1 req/sec (60/min, 1M/month)

**Configuration:**
- Hybrid TOML (operational) + .env (secrets) approach
- Single `load_config()` merges both sources
- Defaults for optional credentials (enables partial pipelines)

**Diagnostics:**
- Lightweight span timing via async context managers
- Event recording with bounded memory (deque)
- Optional JSONL event sink for offline analysis

---

*Architecture analysis: 2026-01-22*
*Update when major patterns change*
