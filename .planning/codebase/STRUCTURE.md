# Codebase Structure

**Analysis Date:** 2026-02-28

## Directory Layout

```
cta-eta/
├── src/cta_eta/                          # Main package (Python 3.13+)
│   ├── __init__.py                       # Package marker
│   ├── data_collection/                  # Core data collection pipeline
│   │   ├── __init__.py                   # Module docstring
│   │   ├── config.py                     # TOML+.env loader, validation
│   │   ├── exceptions.py                 # Shared exception hierarchy
│   │   ├── logging.py                    # Structured JSON/human logging
│   │   ├── utils.py                      # Utilities (lat/lon validation, etc.)
│   │   ├── apis/                         # External API clients (stateless)
│   │   │   ├── __init__.py
│   │   │   ├── api_train_position.py     # CTA Train Tracker API
│   │   │   ├── api_weather_nws.py        # National Weather Service
│   │   │   ├── api_weather_open_meteo.py # Open-Meteo (supplementary)
│   │   │   ├── api_weather_openweathermap.py # OpenWeatherMap (fallback)
│   │   │   ├── api_cta_stations.py       # CTA stations metadata
│   │   │   └── api_track_shape.py        # CTA track geometry
│   │   ├── orchestration/                # Daemon framework & impls
│   │   │   ├── __init__.py
│   │   │   ├── daemon.py                 # BaseDaemon (sync polling)
│   │   │   ├── daemon_async.py           # AsyncBaseDaemon (async polling)
│   │   │   ├── daemon_utils.py           # Error classification, helpers
│   │   │   ├── train_position_daemon.py  # TrainPositionDaemon (15s polls)
│   │   │   ├── weather_daemon.py         # WeatherDaemon (30m polls)
│   │   │   ├── gap_detection.py          # Detect collection gaps
│   │   │   ├── weather_grid_discovery.py # Map stations to NWS grids
│   │   │   └── diagnostics.py            # Debug/monitoring helpers
│   │   ├── storage_cache/                # Journaling & storage abstraction
│   │   │   ├── __init__.py
│   │   │   ├── storage.py                # StorageBackend (local/cloud)
│   │   │   ├── journal_writer.py         # IPC stream writer w/ rotation
│   │   │   ├── parquet_writer.py         # Direct Parquet appending
│   │   │   ├── writer_protocol.py        # Protocol definitions
│   │   │   ├── kv_cache.py               # Simple key-value persistence
│   │   │   ├── cache.py                  # State caching layer
│   │   │   └── weather_grid_cache.py     # Station→grid mapping cache
│   │   ├── compaction/                   # Daily merge & upload
│   │   │   ├── __init__.py
│   │   │   ├── compact.py                # CLI entry point, orchestrator
│   │   │   ├── ipc_reader.py             # Read+repair IPC journals
│   │   │   ├── schema_registry.py        # Schema versioning & drift
│   │   │   ├── schemas.py                # PyArrow schema definitions
│   │   │   ├── uploader.py               # Cloud upload handler
│   │   │   └── archiver.py               # Journal cleanup & retention
│   │   ├── merging/                      # Multi-source data merging
│   │   │   ├── __init__.py
│   │   │   └── weather_merger.py         # Merge NWS/OM/OWM with precedence
│   │   └── schemas/                      # Schema registry (git-tracked)
│   │       ├── train_positions.json      # Current train schema
│   │       └── weather.json              # Current weather schema
│   └── monitoring/                       # Health checks & alerting
│       ├── __init__.py
│       ├── cli.py                        # cta-monitor CLI
│       ├── health_check.py               # cta-health readiness probe
│       ├── alerting.py                   # Email alert sender
│       └── run_alerts.py                 # cta-alerts orchestrator
│
├── tests/                                # Pytest suite (mirrors src/)
│   ├── data_collection/
│   │   ├── __init__.py
│   │   ├── test_config.py                # Config loader + validation
│   │   ├── test_logging.py               # Logging/JSON formatting
│   │   ├── test_utils.py                 # Utility function tests
│   │   ├── apis/
│   │   │   ├── __init__.py
│   │   │   └── test_*.py                 # API client tests
│   │   ├── orchestration/
│   │   │   ├── __init__.py
│   │   │   └── test_*.py                 # Daemon tests
│   │   ├── compaction/
│   │   │   ├── __init__.py
│   │   │   └── test_*.py                 # Compaction tests
│   │   └── merging/
│   │       └── test_*.py                 # Merger tests
│   └── monitoring/
│       └── test_*.py                     # Monitor/alert tests
│
├── data/                                 # Data storage (git-ignored)
│   ├── journals/                         # IPC streams from daemons
│   │   ├── train_positions/date=YYYY-MM-DD/
│   │   │   └── journal_HHMMSS.ipc        # 15-minute rotated files
│   │   └── weather/date=YYYY-MM-DD/
│   ├── train_positions/date=YYYY-MM-DD/ # Compacted daily Parquet
│   ├── weather/date=YYYY-MM-DD/          # Compacted daily Parquet
│   └── compaction/                       # Compaction metrics & state
│
├── .daemon_state/                        # Daemon persistence (git-ignored)
│   ├── TrainPositionDaemon.json          # State from last run
│   ├── WeatherDaemon.json
│   ├── TrainPositionDaemon.diagnostics.json
│   └── WeatherDaemon.diagnostics.json
│
├── .cache/                               # Operational caches (git-ignored)
│   ├── stations.json                     # CTA station metadata
│   ├── track_geometry.json               # Track shapes
│   └── weather_grid_mapping.json         # Station→NWS grid map
│
├── config.toml                           # Operational config (version-controlled)
├── .env                                  # Secrets (git-ignored)
├── pyproject.toml                        # Python package config (uv, pytest, ruff)
├── README.md
└── CLAUDE.md                             # Project guidelines
```

## Directory Purposes

**`src/cta_eta/data_collection/`**

- Purpose: Core data collection engine with pluggable APIs, daemons, and storage
- Contains: Async polling, API clients, IPC journaling, compaction pipeline
- Key files: `config.py` (TOML+.env), `exceptions.py` (shared errors), `logging.py` (structured logging)

**`src/cta_eta/data_collection/apis/`**

- Purpose: Stateless API clients for external data sources
- Contains: Async HTTP functions with retry logic, response parsers, normalization
- Pattern: Each file is a module with functions (no classes), one per API
- Convention: `get_*()` functions for fetching, `parse_*()` for parsing, `normalize_*()` for flattening

**`src/cta_eta/data_collection/orchestration/`**

- Purpose: Long-running daemons with lifecycle, signal handling, and state persistence
- Contains: BaseDaemon/AsyncBaseDaemon base classes, concrete daemons (Train/Weather), utilities
- Key files: `daemon.py` (base), `train_position_daemon.py`, `weather_daemon.py`, `daemon_utils.py` (error classification)

**`src/cta_eta/data_collection/storage_cache/`**

- Purpose: Cloud-agnostic storage abstraction and append-friendly IPC journaling
- Contains: StorageBackend (local/S3/GCS/Azure), JournalWriter (Arrow IPC), KVCache (state)
- Key files: `storage.py` (backend abstraction), `journal_writer.py` (hive-partitioned IPC)

**`src/cta_eta/data_collection/compaction/`**

- Purpose: Daily merge of IPC journals into Parquet, schema validation, cloud upload
- Contains: CLI orchestrator, IPC reader with repair, schema registry, uploader, archiver
- Key files: `compact.py` (entry point), `ipc_reader.py` (batch processing), `schema_registry.py` (drift detection)

**`src/cta_eta/monitoring/`**

- Purpose: Health checks, metrics, and alert automation
- Contains: CLI for investigating health, email alerter, readiness probes
- Key files: `cli.py` (cta-monitor), `alerting.py` (email), `health_check.py` (probe)

**`tests/`**

- Purpose: Pytest suite mirroring source structure
- Contains: Unit tests, mocked API responses, fixture data
- Convention: `test_<module>.py` for module, `Test<Class>` for classes, `test_<function>` for functions

**`data/`** (git-ignored)

- Purpose: Collected data and compaction outputs
- Structure: Hive-partitioned by date and dataset (train_positions, weather)
- Lifecycle: IPC journals → daily Parquet → cloud upload → archive → cleanup

## Key File Locations

**Entry Points:**

- `src/cta_eta/data_collection/orchestration/train_position_daemon.py`: TrainPositionDaemon class
- `src/cta_eta/data_collection/orchestration/weather_daemon.py`: WeatherDaemon class
- `src/cta_eta/data_collection/compaction/compact.py`: cta-compact CLI
- `src/cta_eta/monitoring/cli.py`: cta-monitor CLI
- `src/cta_eta/monitoring/health_check.py`: cta-health readiness probe
- `src/cta_eta/monitoring/run_alerts.py`: cta-alerts automation

**Configuration:**

- `config.toml`: Operational settings (feature flags, polling intervals, rate limits, storage paths)
- `.env`: Secrets (API keys, tokens) — NEVER committed
- `src/cta_eta/data_collection/config.py`: TOML+.env loader with validation

**Core Logic:**

- `src/cta_eta/data_collection/apis/api_train_position.py`: Train position fetch + normalize
- `src/cta_eta/data_collection/apis/api_weather_nws.py`: NWS weather fetch + parse
- `src/cta_eta/data_collection/orchestration/daemon_async.py`: AsyncBaseDaemon polling loop
- `src/cta_eta/data_collection/storage_cache/journal_writer.py`: IPC append + rotation
- `src/cta_eta/data_collection/compaction/ipc_reader.py`: Read+repair journals
- `src/cta_eta/data_collection/merging/weather_merger.py`: Multi-source merge with precedence

**Testing:**

- `tests/data_collection/test_config.py`: Configuration validation
- `tests/data_collection/apis/`: API client mocks and response parsing
- `tests/data_collection/orchestration/`: Daemon lifecycle and error handling
- `tests/data_collection/compaction/`: IPC reading, merging, schema drift

## Naming Conventions

**Files:**

- `api_<service>.py`: API client modules (e.g., `api_train_position.py`, `api_weather_nws.py`)
- `<daemon_name>_daemon.py`: Daemon implementations (e.g., `train_position_daemon.py`)
- `test_<module>.py`: Test files matching source module name
- `_<internal>.py`: Private/internal modules (underscore prefix, not exported)

**Directories:**

- Lowercase with underscores (snake_case): `data_collection`, `storage_cache`, `weather_daemon`
- Functional grouping: `apis/`, `orchestration/`, `storage_cache/`, `compaction/`, `monitoring/`

**Python Classes:**

- PascalCase: `BaseDaemon`, `TrainPositionDaemon`, `JournalWriter`, `StorageBackend`
- Abstract bases prefixed: `Base*` or `Abstract*`
- Test classes: `Test<ClassName>` (e.g., `TestBaseDaemon`)

**Python Functions/Variables:**

- snake_case: `get_train_positions()`, `normalize_train_positions()`, `create_storage_backend()`
- Private: leading underscore (e.g., `_sanitize_config_for_logging()`)
- Constants: SCREAMING_SNAKE_CASE (e.g., `MAX_RETRY_ATTEMPTS`, `CTA_LINES`)

## Where to Add New Code

**New API Client:**

- File: `src/cta_eta/data_collection/apis/api_<service_name>.py`
- Structure: Functions for fetch, parse, normalize
- Tests: `tests/data_collection/apis/test_<service_name>.py`
- Pattern: Use `@stamina.retry` for retries, `@log_api_call` for logging, async functions with `httpx.AsyncClient`

**New Daemon:**

- File: `src/cta_eta/data_collection/orchestration/<dataset>_daemon.py`
- Inherit: `AsyncBaseDaemon` for async polling, override `run()` and `_get_state()`
- Storage: Use `JournalWriter.append_batch()` to store normalized records
- Tests: `tests/data_collection/orchestration/test_<dataset>_daemon.py`
- Register: Add to CLI scripts in `pyproject.toml` if public entry point needed

**New Storage Backend:**

- File: `src/cta_eta/data_collection/storage_cache/storage.py` (add to existing)
- Inherit: `StorageBackend` abstract class
- Implement: `put()`, `get()`, `list()` methods
- Tests: `tests/data_collection/storage_cache/test_storage.py`
- Usage: Pass to `create_storage_backend()` factory, configured via `config.toml`

**Utilities & Helpers:**

- Shared logic: `src/cta_eta/data_collection/utils.py`
- Shared exceptions: `src/cta_eta/data_collection/exceptions.py`
- New exception class: Add to `exceptions.py` with docstring explaining when to use

**Monitoring Probes:**

- File: `src/cta_eta/monitoring/<probe_name>.py`
- Pattern: Functions that return JSON-serializable metrics or status
- Tests: `tests/monitoring/test_<probe_name>.py`
- CLI integration: Add subcommand to `monitoring/cli.py` argparse

## Special Directories

**`.daemon_state/`**

- Purpose: Persistent daemon state across restarts
- Generated: Yes (created by daemons on startup)
- Committed: No (git-ignored)
- Contents: JSON files with last poll times, error counts, diagnostic info

**`.cache/`**

- Purpose: Cached metadata (stations, track geometry, weather grid mapping)
- Generated: Yes (populated on first run of each daemon)
- Committed: No (git-ignored)
- TTL: Configurable per cache (config.toml cache section)

**`data/journals/`**

- Purpose: IPC stream files produced by daemons
- Generated: Yes (created by JournalWriter)
- Committed: No (git-ignored)
- Lifecycle: Rotated every 15 minutes, compacted daily, archived, deleted per retention

**`src/cta_eta/data_collection/schemas/`**

- Purpose: Schema registry tracking observed PyArrow schemas from daemons
- Generated: Partially (observed schemas added by compaction)
- Committed: Yes (registry is version-controlled)
- Files: `train_positions.json`, `weather.json` (JSON serializations of pa.Schema)

---

*Structure analysis: 2026-02-28*
