# External Integrations

**Analysis Date:** 2026-02-28

## APIs & External Services

**Transit Data:**
- CTA Train Tracker API (Chicago Transit Authority) - Train positions and real-time status
  - SDK/Client: Custom client in `src/cta_eta/data_collection/apis/api_train_position.py`
  - Auth: `CTA_API_KEY` environment variable
  - Rate limit: 50,000 calls/day; configured to 0.1 req/sec (6/min) = ~8,640/day
  - Docs: https://www.transitchicago.com/developers/ttdocs/

- CTA Stations API - Station metadata and geographic data
  - SDK/Client: Custom client in `src/cta_eta/data_collection/apis/api_cta_stations.py`
  - Auth: `CTA_API_KEY` environment variable
  - Rate limit: Shared with train position API (50k/day quota)

- CTA Track Shape API - Track geometry and route definitions
  - SDK/Client: Custom client in `src/cta_eta/data_collection/apis/api_track_shape.py`
  - Auth: `CTA_API_KEY` environment variable
  - Rate limit: Shared with train position API (50k/day quota)

**Weather Data (Primary):**
- National Weather Service (NWS) API - Hourly weather forecasts
  - SDK/Client: Custom client in `src/cta_eta/data_collection/apis/api_weather_nws.py`
  - Auth: User-Agent header with `NWS_APP_NAME` and `NWS_EMAIL` (no formal API key)
  - Rate limit: No official limit; configured to 5 req/sec with max 5 concurrent
  - Docs: https://www.weather.gov/documentation/services-web-api
  - Endpoint: https://api.weather.gov/

**Weather Data (Supplementary):**
- Open-Meteo API - Additional weather variables (visibility, snow depth, wind gusts, etc.)
  - SDK/Client: Custom client in `src/cta_eta/data_collection/apis/api_weather_open_meteo.py`
  - Auth: None (public API)
  - Rate limit: 10,000 calls/day free tier; configured to 0.1 req/sec (6/min) = ~8,640/day
  - Docs: https://open-meteo.com/en/docs
  - Endpoint: https://api.open-meteo.com/v1/forecast

**Weather Data (Fallback):**
- OpenWeatherMap API - Weather fallback when primary sources unavailable
  - SDK/Client: Custom client in `src/cta_eta/data_collection/apis/api_weather_openweathermap.py`
  - Auth: `OPENWEATHERMAP_API_KEY` environment variable
  - Rate limit: Free tier 60 calls/min, 1M calls/month; current tier 3000 calls/min, 100M calls/month; configured to 60 req/sec with max 60 concurrent
  - Docs: https://openweathermap.org/api
  - Endpoint: https://api.openweathermap.org/

**Municipal Data:**
- Chicago Data Portal API - Station and transit infrastructure metadata
  - SDK/Client: Custom client in `src/cta_eta/data_collection/apis/api_cta_stations.py`
  - Auth: `CHIDATA_APP_TOK` and `CHIDATA_APP_SECRET` (Socrata API credentials)
  - Docs: https://data.cityofchicago.org/

## Data Storage

**Databases:**
- Not used - Project uses flat file storage only (Parquet, Arrow IPC journals)

**File Storage:**
- Local filesystem (development):
  - IPC Journals: `data/journals/` (Arrow IPC format for immediate writes)
  - Compaction staging: `data/compaction/` (local staging before cloud upload)
  - Archive: `data/archive/` (archived journals after verified cloud upload)

- Cloud object storage (production):
  - AWS S3 via s3fs
    - Connection: `S3_BUCKET` environment variable (bucket name)
    - Optional: `S3_ENDPOINT_URL` for S3-compatible endpoints (MinIO, DigitalOcean Spaces)
    - Credentials: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (via AWS SDK chain or boto3 env vars)
    - Client: `src/cta_eta/data_collection/storage_cache/storage.py` uses fsspec with s3fs backend

  - Google Cloud Storage (GCS) via gcsfs
    - Connection: `GCS_BUCKET` environment variable (bucket name)
    - Credentials: `GOOGLE_APPLICATION_CREDENTIALS` (path to JSON service account key file)
    - Client: fsspec with gcsfs backend

  - Azure Blob Storage (ABFS) via fsspec
    - Connection: `AZURE_BUCKET` environment variable (container name)
    - Credentials: Azure Application Credentials
    - Client: fsspec with abfs backend

**Caching:**
- Local filesystem cache for operational state:
  - Location: `.cache/` directory
  - Purpose: Station list cache, track geometry, weather grid mappings
  - Cache TTLs: 7 days (stations), 30 days (track geometry), 30 days (weather mapping)
  - Implementation: `src/cta_eta/data_collection/storage_cache/cache.py`

## Authentication & Identity

**Auth Provider:**
- None - Stateless API authentication via API keys and headers

**Authentication Methods:**
- API Key in headers: CTA API, OpenWeatherMap
- Custom User-Agent headers: NWS API
- OAuth/Token-based: Chicago Data Portal (Socrata), Mailjet

## Monitoring & Observability

**Error Tracking:**
- Custom diagnostic system in `src/cta_eta/data_collection/orchestration/diagnostics.py`
- Structured event logging with bounded in-memory ring buffer
- Optional JSONL event log output (rotated by size)

**Logs:**
- Configuration: `src/cta_eta/data_collection/logging.py`
- Log file: `logs/cta_eta.log`
- Format: JSON structured logging (enabled by default in config.toml)
- Level: Configurable (default INFO)
- Output: Console + file simultaneously

**Alerting:**
- Email alerts via Mailjet API (optional, disabled by default)
  - Implementation: `src/cta_eta/monitoring/alerting.py`
  - API endpoint: https://api.mailjet.com/v3.1/send
  - Auth: `MAILJET_API_KEY`, `MAILJET_API_SECRET`
  - Cooldown: Configurable minimum hours between repeat alerts
  - State file: `.daemon_state/last_alert.json` tracks last alert timestamp

## CI/CD & Deployment

**Hosting:**
- Not applicable - This is a data collection service, not a web application
- Runs as daemons on schedule or continuously

**CI Pipeline:**
- Pre-commit hooks: Ruff linting and formatting (`.pre-commit-config.yaml`)
- GitHub integration: Configured for pull requests (`.github/` directory present)
- Manual testing via pytest

## Environment Configuration

**Required env vars (Feature-dependent):**
- `CTA_API_KEY` - CTA Train Tracker (required if train_positions enabled)
- `NWS_APP_NAME`, `NWS_EMAIL` - National Weather Service User-Agent (required if weather_collection enabled)
- `OPENWEATHERMAP_API_KEY` - OpenWeatherMap fallback (required if weather_collection_fallback enabled)
- `CHIDATA_APP_TOK`, `CHIDATA_APP_SECRET` - Chicago Data Portal (required if station_data enabled)
- `MAILJET_API_KEY`, `MAILJET_API_SECRET` - Email alerts (required if alerting enabled)
- `S3_BUCKET`, `GCS_BUCKET`, `AZURE_BUCKET` - Cloud storage buckets (required when storage.compaction.backend is cloud)
- `S3_ENDPOINT_URL` - Optional S3-compatible endpoint override

**Feature flags (config.toml):**
- `features.train_positions` - Enable/disable CTA train tracking
- `features.weather_collection` - Enable/disable NWS weather collection
- `features.weather_collection_fallback` - Enable/disable OpenWeatherMap fallback
- `features.station_data` - Enable/disable Chicago Data Portal station sync
- `alerting.enabled` - Enable/disable email alerts

**Secrets location:**
- `.env` file (git-ignored) - See `.env.template` for template
- Configuration uses dotenv to load `.env` into environment variables
- `config.py` redacts sensitive keys in logs

## Webhooks & Callbacks

**Incoming:**
- None detected

**Outgoing:**
- Mailjet email API calls (one-way notifications only, no callbacks)

---

*Integration audit: 2026-02-28*
