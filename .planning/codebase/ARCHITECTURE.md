# Architecture

**Analysis Date:** 2026-02-28

## Pattern Overview

**Overall:** Layered data pipeline with async daemons, cloud-agnostic storage, and daily compaction/archival.

**Key Characteristics:**
- **Daemon-based collection**: Long-running async processes continuously poll APIs with lifecycle management
- **IPC streaming**: Apache Arrow IPC streams for append-friendly journaling without per-poll file creation
- **Pluggable storage**: Cloud-agnostic abstraction (local filesystem, S3, GCS, Azure)
- **Schema-driven compaction**: Validates and merges daily journals into Parquet with schema registry
- **Graceful degradation**: Independent feature flags allow partial operations (e.g., weather-only without train APIs)

## Layers

**API Layer:**
- Purpose: Stateless fetch and response normalization from external services
- Location: `src/cta_eta/data_collection/apis/`
- Contains: Async HTTP clients with retry logic (stamina), API-specific response parsers, normalization functions
- Depends on: httpx, stamina (retry), config for credentials and rate limits
- Used by: Daemon layer (train_position_daemon, weather_daemon)

**Daemon Layer (Orchestration):**
- Purpose: Long-running async polling with lifecycle management, state persistence, and signal handling
- Location: `src/cta_eta/data_collection/orchestration/`
- Contains: BaseDaemon (sync base), AsyncBaseDaemon (async base), concrete daemons (TrainPositionDaemon, WeatherDaemon), gap detection, diagnostics
- Depends on: API layer, storage_cache layer, config, logging
- Used by: CLI scripts (cta-monitor, cta-health, etc.), deployment infrastructure

**Storage & Caching Layer:**
- Purpose: Append-friendly journaling with cloud-agnostic abstraction and local state management
- Location: `src/cta_eta/data_collection/storage_cache/`
- Contains: JournalWriter (hive-partitioned IPC streams), StorageBackend (pluggable local/cloud), KVCache (state), parquet_writer, cache modules
- Depends on: pyarrow, fsspec (S3/GCS), pathlib for file operations
- Used by: Daemon layer (appends records), compaction layer (reads journals)

**Compaction & Processing Layer:**
- Purpose: Daily journal merging into Parquet, schema validation, cloud upload, and archival
- Location: `src/cta_eta/data_collection/compaction/`
- Contains: compact.py (CLI orchestrator), ipc_reader (batch processing), schema_registry (drift detection), uploader (cloud), archiver (cleanup)
- Depends on: storage_cache, pyarrow, schema definitions
- Used by: Scheduled jobs (cron/Airflow), manual CLI

**Monitoring & Alerting Layer:**
- Purpose: Health checks, error tracking, metrics collection, and alert notifications
- Location: `src/cta_eta/monitoring/`
- Contains: cli.py (status/metrics), alerting.py (email), health_check.py (probes), run_alerts.py (automation)
- Depends on: config, storage backend (for reading Parquet metadata)
- Used by: Operators, alerting systems

**Configuration & Utilities:**
- Purpose: Centralized configuration management, logging, validation
- Location: `src/cta_eta/data_collection/config.py`, `logging.py`, `exceptions.py`, `utils.py`
- Contains: TOML+.env loader, schema validation, structured JSON logging, shared exceptions
- Depends on: dotenv, tomllib
- Used by: All layers

## Data Flow

**Collection Pipeline:**

1. **Polling (Daemon)** → Async loop in TrainPositionDaemon/WeatherDaemon
   - Daemon starts, loads config, initializes storage writer
   - Polls API every N seconds (15s trains, 30m weather) within rate limits
   - Calls API layer (e.g., `get_train_positions()`) with retry

2. **Normalization** → API layer converts nested responses to flat records
   - Raw CTA JSON with nested routes/trains → flat train_position records
   - Multiple weather sources (NWS, Open-Meteo, OpenWeatherMap) → merged weather record

3. **Storage (IPC)** → JournalWriter appends to Arrow IPC stream
   - Hive-partitioned paths: `data/{dataset_name}/date=YYYY-MM-DD/journal_HHMMSS.ipc`
   - Schema inferred from first batch, reused across all rotations
   - Rotates every 15 minutes (900s default), splits at 3 AM CST for daily boundary
   - ~230k train records/day, weather every 30m

4. **Compaction (Scheduled)** → compact.py CLI runs daily or on-demand
   - Discovers all IPC journals for a date and daemon
   - Reads batches with repair (handles partial writes)
   - Merges into single Snappy-compressed Parquet per daemon per day
   - Validates schema against registry, detects drift, raises alerts if needed

5. **Cloud Upload** → Uploader sends Parquet to S3/GCS/Azure
   - Uses configured backend (fsspec-based)
   - Staging directory → upload prefix (cloud), then archive locally

6. **Cleanup** → Archiver prunes old journals and archived Parquet
   - Retains N days (configurable) in archive before deletion
   - Preserves Parquet in cloud indefinitely

**State Management:**

- **Daemon state** persisted to `.daemon_state/{DaemonClassName}.json`
- **Observed schemas** stored in `src/cta_eta/data_collection/schemas/` registry
- **Config cached** in `.cache/` for TTLs (stations, track geometry, weather grids)
- **Compaction metrics** logged to `data/compaction/` for auditing

## Key Abstractions

**Daemon Base Classes:**

- **`BaseDaemon`** (`daemon.py`): Sync polling with state, signal handlers, lifecycle hooks
  - Subclass implements `run()` (main loop) and `_get_state()` (persistence)
  - Graceful shutdown on SIGTERM/SIGINT, persists state before exit

- **`AsyncBaseDaemon`** (`daemon_async.py`): Async variant for I/O-bound work
  - Uses asyncio with semaphores for rate limiting
  - Stores state in memory, saves on shutdown

**Storage Backend Protocol:**

- **`StorageBackend`** abstract class with `put()`, `get()`, `list()` operations
- **`LocalStorage`**: Direct filesystem using pathlib
- **`CloudStorage`**: fsspec-based for S3/GCS/Azure with single endpoint URL

**Writer Protocol:**

- **`WritableFile`**: Protocol for append-capable handles (used by IPC writer)
- **`RotatableWriter`**: Protocol for time-based rotation (base for JournalWriter)

**Configuration:**

- Hybrid TOML (`config.toml` version-controlled) + .env (secrets)
- Feature flags enable partial pipelines
- Section-based access via `get_config_section()` with validation and defaults

## Entry Points

**CLI Commands:**

- **`cta-monitor`** (`monitoring/cli.py`)
  - Inspect health: `cta-monitor status`, `cta-monitor errors`, `cta-monitor gaps`
  - Aggregated metrics: `cta-monitor metrics` (for alerting systems)

- **`cta-health`** (`monitoring/health_check.py`)
  - Structured health probe for readiness checks

- **`cta-alerts`** (`monitoring/run_alerts.py`)
  - Send email alerts based on metrics (e.g., missing data, schema drift)

- **`cta-compact`** (`compaction/compact.py`)
  - Default: `cta-compact` (run daily compaction)
  - Reprocess: `cta-compact run --reprocess 2026-02-17`
  - Schema: `cta-compact schema update` (promote observed to registry)

**Daemon Scripts (via Python -m):**

- `python -m cta_eta.data_collection.orchestration.train_position_daemon` → TrainPositionDaemon
- `python -m cta_eta.data_collection.orchestration.weather_daemon` → WeatherDaemon

## Error Handling

**Strategy:** Classified error handling with different recovery paths

**Exception Hierarchy:**

- **`ConfigurationError`** (`exceptions.py`): Raised for missing/invalid config → daemon exits
  - Missing API keys, malformed TOML, invalid credentials
  - Daemon catches, logs, raises (no retry)

- **`APIResponseError`**: Malformed API responses → may retry or skip batch
  - Missing required JSON fields, type mismatches
  - Daemon logs, may skip record batch depending on severity

- **`CTATrackerAPIError`**: CTA API returns error in body (not HTTP error)
  - Includes error code and message from API
  - Daemon-level error classification distinguishes from network errors

- **HTTP/Network errors**: retried via `@stamina.retry` decorator
  - Uses exponential backoff, max attempts from config
  - Exhaust retries → bubble to daemon error handler

**Daemon Error Classification** (`daemon_utils.py`):

- Distinguishes: `ConfigurationError`, `TransientError`, `PermanentError`, `StorageError`
- Routes to appropriate recovery (exit, retry, backoff, etc.)

## Cross-Cutting Concerns

**Logging:**

- Structured JSON output (or human-readable in dev)
- Context vars for request correlation (async-safe)
- log_api_call decorator traces API requests/responses
- Extra fields for structured enrichment

**Validation:**

- Config validation at startup via `validate_config()` checks credentials + file settings
- Schema validation in compaction: observed schema vs registry
- Drift detection alerts if schema changes detected
- Latitude/longitude validation in API responses

**Authentication:**

- CTA API: key-based (env `CTA_API_KEY`)
- NWS: app name + email (env `NWS_APP_NAME`, `NWS_EMAIL`)
- Chi-data API: token + secret (env `CHIDATA_APP_TOK`, `CHIDATA_APP_SECRET`)
- OpenWeatherMap: key-based (env `OPENWEATHERMAP_API_KEY`)
- Cloud storage: handled by fsspec (AWS_* env vars for S3, GOOGLE_* for GCS, etc.)

**Rate Limiting:**

- Per-API limits defined in config: `rate_limits.{cta,nws,openweathermap}`
- Enforced at daemon level using `aiometer` (async) with semaphores
- Allows graceful handling of 402 quota exhausted (CTA error code 102)

---

*Architecture analysis: 2026-02-28*
