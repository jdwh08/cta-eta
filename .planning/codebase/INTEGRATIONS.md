# External Integrations

**Analysis Date:** 2026-01-17

## APIs & External Services

**CTA Train Tracker API:**
- Endpoint: `http://lapi.transitchicago.com/api/1.0/ttpositions.aspx`
- Used in: `src/cta_eta/api_train_position.py`
- Auth: API key in `CTA_API_KEY` environment variable
- Rate limit: 50,000 requests/day
- Update frequency: ~15-20 seconds (with ~10 second lag)
- Provides: Real-time train positions, arrival times, line data for 8 CTA lines (red, blue, brn, g, org, p, pink, y)
- Returns: JSON with train position, destination, heading, next station info
- Integration method: httpx GET with retry logic (stamina decorator, 10 attempts)

**Chicago Open Data Portal - Stations API:**
- Endpoint: `https://data.cityofchicago.org/api/v3/views/3tzw-cg4m/query.json`
- Used in: `src/cta_eta/api_stations_weather.py`
- Auth: Optional `CHIDATA_APP_TOK` and `CHIDATA_APP_SECRET` headers
- Rate limit: Not specified (public API)
- Provides: CTA station names, IDs, served lines, addresses, geographic coordinates
- Returns: JSON with 146+ station records including latitude/longitude
- Integration method: httpx GET with retry logic (stamina decorator, 10 attempts)

**Chicago Open Data Portal - Track Shape API:**
- Endpoint: `https://data.cityofchicago.org/api/v3/views/xbyr-jnvx/query.json`
- Used in: `src/cta_eta/api_track_shape.py`
- HTTP Method: POST
- Auth: `CHIDATA_APP_TOK` and `CHIDATA_APP_SECRET` environment variables
- Provides: CTA track segment geometry (MultiLineString coordinates), track type (Elevated, At Grade, Subway), lines served, segment endpoints, segment length
- Data cached: `data/cta_track_shape.json` (155 lines)
- Update frequency: Long cache acceptable (track shape rarely changes)

**Open-Meteo Weather API:**
- Endpoint: `https://api.open-meteo.com/v1/forecast`
- Used in: `src/cta_eta/api_stations_weather.py`
- Auth: None required (free tier)
- Rate limit: 10,000 calls/day
- Update interval: 15 minutes (900 seconds)
- Provides: Current weather (temperature, humidity, apparent temperature, rain, snowfall, pressure, wind data, weather code) at station coordinates
- Returns: JSON with current weather conditions in Fahrenheit, mph, inches
- Supports: Batch queries (comma-separated lat/lon lists)
- Integration method: httpx GET with retry logic (stamina decorator, 10 attempts)
- Optimization: Maps 146 CTA stations to 39 unique weather stations (~4,500 daily calls vs 14,000 if querying per station)

**National Weather Service API (Planned):**
- Endpoints:
  - Points: `https://api.weather.gov/points/{latitude},{longitude}`
  - Hourly Forecast: Retrieved from points response
- Auth: Requires User-Agent header: `(cta-eta-weather, jdwh08s@gmail.com)`
- Planned use: `src/cta_eta/api_stations_weather.py` (commented in code)
- Provides: Hourly weather forecasts including probability of precipitation, dewpoint, relative humidity, wind speed/direction
- Rate limit: Free, no documented limit
- Integration rationale: Avoid overloading Open-Meteo; provides detailed hourly forecasts

## Data Storage

**Local File Storage:**
- CSV export: `stations_weather.csv` - Generated output from station/weather data collection
- JSON cache: `data/cta_track_shape.json` - Cached track geometry data (155 lines, ~140KB)

**Cloud Storage (Planned):**
- S3 or equivalent object storage for parquet files (`PLAN.md`)
- Weekly/daily parquet file transmission from VPS to local backup
- Cloud compute storage for continuous data collection

## Authentication & Identity

Not applicable (no user authentication system)

## Monitoring & Observability

Not detected (early development stage)

## CI/CD & Deployment

**CI Pipeline:**
- GitHub Actions (`.github/workflows/ci.yml`)
- Workflows: Linting (`uv run python devtools/lint.py`), testing (`uv run pytest`)
- Platform: ubuntu-latest with Python 3.13
- Secrets: None needed (public repo tests only)

**Hosting:**
- Planned: VPS (Oracle Cloud Infrastructure Free Tier, AWS EC2/Lightsail, or GCP Compute Engine)
- Deployment: Continuous polling daemon for data collection
- Environment vars: Configured in VPS environment

## Environment Configuration

**Development:**
- Required env vars: `CTA_API_KEY`, `CHIDATA_APP_TOK`, `CHIDATA_APP_SECRET`
- Secrets location: `.env` file (gitignored)
- Mock/stub services: Direct API calls (no mocking currently)

**Production:**
- Secrets management: Environment variables on VPS
- Data: Continuous collection to local storage + S3 backup

## Webhooks & Callbacks

**Incoming:**
- Not applicable

**Outgoing:**
- Not applicable

---

*Integration audit: 2026-01-17*
*Update when adding/removing external services*
