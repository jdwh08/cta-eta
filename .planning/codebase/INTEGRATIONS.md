# External Integrations

**Analysis Date:** 2026-01-19

## APIs & External Services

**CTA Train Tracker API:**
- Purpose: Real-time train positions for 8 CTA lines (red, blue, brn, g, org, p, pink, y)
- Endpoint: `http://lapi.transitchicago.com/api/1.0/ttpositions.aspx`
- File: `src/cta_eta/data_collection/apis/api_train_position.py`
- SDK/Client: httpx with stamina retry decorator
- Auth: API key from `CTA_API_KEY` environment variable
- Rate Limit: 50,000 requests/day
- Polling: ~15 seconds per `config.toml` (line 8)
- Response: JSON with train positions, destinations, arrival predictions

**National Weather Service (NWS) API:**
- Purpose: Hourly weather forecasts (temperature, humidity, wind, precipitation probability, dewpoint)
- Endpoints:
  - Points API: `https://api.weather.gov/points/{lat},{lon}`
  - Forecast: `https://api.weather.gov/gridpoints/{OFFICE}/{X},{Y}/forecast/hourly`
- File: `src/cta_eta/data_collection/apis/api_weather_nws.py`
- SDK/Client: httpx with stamina retry
- Auth: Custom User-Agent header (`NWS_APP_NAME`, `NWS_EMAIL` from environment)
- Rate Limit: None (government service)
- Grid Discovery: Lazy discovery pattern with caching in `src/cta_eta/data_collection/storage_cache/weather_grid_cache.py`

**Open-Meteo API:**
- Purpose: Supplementary weather variables (visibility, snow depth, wind gusts, apparent temperature, precipitation)
- Endpoint: `https://api.open-meteo.com/v1/forecast`
- File: `src/cta_eta/data_collection/apis/api_weather_open_meteo.py`
- SDK/Client: httpx with stamina retry
- Auth: None (open API, no key required)
- Rate Limit: 10,000 calls/day
- Polling: 30 minutes per `config.toml`
- Optimization: ~300 stations → ~50 weather grid points (deduplication)

**OpenWeatherMap API:**
- Purpose: Fallback weather data (current conditions, 3-hour forecasts)
- Endpoints:
  - Current: `https://api.openweathermap.org/data/2.5/weather`
  - Forecast: `https://api.openweathermap.org/data/2.5/forecast`
- File: `src/cta_eta/data_collection/apis/api_weather_openweathermap.py`
- SDK/Client: httpx with stamina retry
- Auth: API key from `OPENWEATHERMAP_API_KEY` environment variable
- Rate Limit: 1,000/day (free tier)
- Usage: Fallback when primary weather sources (NWS, Open-Meteo) fail

**Chicago Open Data Portal (Socrata):**
- Purpose: CTA track segment geometry (MultiLineString), station locations
- Base URL: `https://data.cityofchicago.org/resource`
- Dataset: CTA Track Segments (`xbyr-jnvx`)
- File: `src/cta_eta/data_collection/apis/api_cta_track_shape.py`
- SDK/Client: httpx with extended timeout (60s read)
- Auth: App Token & Secret (`CHIDATA_APP_TOK`, `CHIDATA_APP_SECRET` from environment)
- Caching: 30-day TTL for track geometry (stable reference data)
- Response: GeoJSON with track endpoints, type, shape length

## Data Storage

**Cloud Storage Backends:**
- AWS S3 - Via `s3fs>=2026.1.0` (configured via `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)
- Google Cloud Storage (GCS) - Via `gcsfs>=2026.1.0` (via `GOOGLE_APPLICATION_CREDENTIALS` JSON key)
- Local Filesystem - Default development backend
- Configuration: `config.toml` (lines 28-57) for backend selection and bucket names
- File: `src/cta_eta/data_collection/storage_cache/storage.py`

**Data Format:**
- Apache Parquet - Columnar format via PyArrow with Snappy compression
- Hive-style partitioning: `date=YYYY-MM-DD/` (partition hour: 3:00 AM Chicago time)
- Infinite retention by default

## Authentication & Identity

**API Authentication Methods:**
- CTA: API key in query parameter
- NWS: Custom User-Agent header (app name + email)
- Open-Meteo: None required
- OpenWeatherMap: API key in query parameter
- Chicago Data Portal: Socrata app token/secret headers

**Secrets Management:**
- Development: `.env` file (gitignored)
- Production: Environment variables on cloud VPS
- Template: `.env.template` with required variable names

## Monitoring & Observability

**Logging:**
- Structured JSON logging - `src/cta_eta/data_collection/logging.py`
- Dual formatters: JSON (production parsing), human-readable (development)
- Context-aware logging with request correlation
- API call decorator: `@log_api_call()` for automatic request/response logging

**Error Tracking:**
- Not implemented (logs only)

**Analytics:**
- Not implemented

## CI/CD & Deployment

**Hosting:**
- Target: Cloud VPS (Oracle Cloud, AWS EC2/Lightsail, GCP Compute Engine)
- Deployment: Manual (daemon processes)
- Uptime: 24/7 required for continuous polling

**CI Pipeline:**
- Pre-commit hooks: Ruff linting/formatting, YAML/TOML validation (`.pre-commit-config.yaml`)
- No automated CI/CD yet

## Environment Configuration

**Development:**
- Required env vars: `CTA_API_KEY`, `CHIDATA_APP_TOK`, `CHIDATA_APP_SECRET`, `NWS_APP_NAME`, `NWS_EMAIL`
- Optional: `OPENWEATHERMAP_API_KEY` (fallback)
- Secrets location: `.env.local` (gitignored)
- Storage backend: Local filesystem (`data/`)

**Staging:**
- Not configured

**Production:**
- Secrets: Environment variables on VPS
- Storage: S3 or GCS with daily Parquet partitions
- Backup: Weekly/daily file transmission to local (planned)

## Webhooks & Callbacks

**Incoming:**
- None

**Outgoing:**
- None

---

*Integration audit: 2026-01-19*
*Update when adding/removing external services*
