# External Integrations

**Analysis Date:** 2026-01-22

## APIs & External Services

**CTA Train Tracker API:**
- Endpoint: `http://lapi.transitchicago.com/api/1.0/ttpositions.aspx`
- Purpose: Real-time train positions for all 8 CTA rail lines
- Files: `src/cta_eta/data_collection/apis/api_train_position.py`
- SDK/Client: httpx with custom parsing
- Auth: API key in CTA_API_KEY env var
- Rate limit: 50,000 requests/day (~5K/hour)
- Polling: 15 second intervals (configured in config.toml)

**National Weather Service (NWS) API:**
- Endpoints:
  - Points API: `https://api.weather.gov/points/{lat},{lon}` (grid discovery)
  - Forecast API: `https://api.weather.gov/gridpoints/{OFFICE}/{X},{Y}/forecast/hourly`
- Purpose: Hourly weather forecasts (primary source)
- Files: `src/cta_eta/data_collection/apis/api_weather_nws.py`
- SDK/Client: httpx with async requests
- Auth: User-Agent header via NWS_APP_NAME and NWS_EMAIL env vars
- Rate limit: No documented limit (public US government API)
- Grid points: ~50 unique locations deduced from ~145 CTA stations

**Open-Meteo API:**
- Endpoint: `https://api.open-meteo.com/v1/forecast`
- Purpose: Supplementary weather data (visibility, snow depth, surface pressure)
- Files: `src/cta_eta/data_collection/apis/api_weather_open_meteo.py`
- SDK/Client: httpx with custom parsing
- Auth: No API key required
- Rate limit: 10,000 calls/day (~2,400/day usage with 50 grid points)
- Retry: 3 attempts for discovery, 5 for current data

**OpenWeatherMap API:**
- Endpoints:
  - Current: `https://api.openweathermap.org/data/2.5/weather`
  - Forecast: `https://api.openweathermap.org/data/2.5/forecast`
- Purpose: Fallback weather source when NWS/Open-Meteo fail
- Files: `src/cta_eta/data_collection/apis/api_weather_openweathermap.py`
- SDK/Client: httpx with custom parsing
- Auth: API key in OPENWEATHERMAP_API_KEY env var
- Rate limit: 60 calls/min, 1M calls/month (free tier)

**Chicago Data Portal (Socrata API):**
- Endpoints:
  - Stations: `https://data.cityofchicago.org/api/v3/views/3tzw-cg4m/query.json`
  - Track shapes: `https://data.cityofchicago.org/resource/xbyr-jnvx.json`
- Purpose: CTA station metadata and track geometry (reference data)
- Files: `src/cta_eta/data_collection/apis/api_cta_stations.py`, `api_track_shape.py`
- SDK/Client: httpx with custom parsing
- Auth: CHIDATA_APP_TOK and CHIDATA_APP_SECRET env vars
- Caching: 7 days (stations), 30 days (track geometry)
- Data: ~300 station locations, track segments with MultiLineString geometry

## Data Storage

**Cloud Storage Backends:**
- Local filesystem - Development and default mode
  - Path: Configurable directory with Hive-style partitioning
  - Files: `src/cta_eta/data_collection/storage_cache/storage.py`

- AWS S3 - Production cloud storage option
  - SDK/Client: s3fs>=2026.1.0 via fsspec abstraction
  - Auth: AWS credentials via standard AWS credential chain
  - Backend: config.toml backend="s3"

- Google Cloud Storage - Alternative cloud storage
  - SDK/Client: gcsfs>=2026.1.0 via fsspec abstraction
  - Auth: GCS credentials via standard GCP credential chain
  - Backend: config.toml backend="gcs"

**File Format:**
- Apache Parquet with Snappy compression
- Daily partitions split at 3:00 AM America/Chicago timezone
- Hive-style partitioning: `dataset=weather/date=2026-01-22/part-*.parquet`

**Caching:**
- TTL-based persistent caches for reference data
- Files: `src/cta_eta/data_collection/storage_cache/cache.py`, `kv_cache.py`
- Weather grid mappings: 7 day TTL (`weather_grid_cache.py`)
- Station metadata: 7 day TTL
- Track geometry: 30 day TTL

## Authentication & Identity

Not applicable - no authentication/authorization system (data collection pipeline)

## Monitoring & Observability

**Custom Diagnostics:**
- Lightweight span timing with context managers
- Files: `src/cta_eta/data_collection/orchestration/diagnostics.py`
- Event recording with bounded memory (deque)
- Optional JSONL event sink with rotation

**Logging:**
- Structured JSON logging via custom formatters
- Files: `src/cta_eta/data_collection/logging.py`
- Context variables for request correlation
- Log decorators: `@log_api_call()` for automatic timing

**Error Tracking:**
- None (logs only)

**Analytics:**
- None (internal data pipeline)

## CI/CD & Deployment

**Hosting:**
- Self-hosted daemon processes (24/7 operation)
- No cloud hosting platform (runs on user infrastructure)

**CI Pipeline:**
- `.pre-commit-config.yaml` - Local pre-commit hooks with ruff
- No CI/CD configuration detected

## Environment Configuration

**Development:**
- Required env vars: CTA_API_KEY, NWS_APP_NAME, NWS_EMAIL (for CTA + NWS APIs)
- Optional env vars: OPENWEATHERMAP_API_KEY (fallback), CHIDATA_APP_TOK/SECRET (metadata)
- Secrets location: `.env` file (git-ignored, template: `.env.template`)
- Config file: `config.toml` in project root

**Staging:**
- Not applicable (data collection pipeline, not a deployed application)

**Production:**
- Same as development with production API keys
- Cloud storage backend configured in `config.toml`

## Webhooks & Callbacks

**Incoming:**
- None

**Outgoing:**
- None

---

*Integration audit: 2026-01-22*
*Update when adding/removing external services*
