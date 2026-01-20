# CTA Train ETA Prediction - Data Collection Infrastructure

## What This Is

A production-grade data collection system that captures Chicago Transit Authority (CTA) train positions every ~15 seconds and weather conditions hourly to build training datasets for spatiotemporal ETA prediction models. The system prioritizes zero data loss within API rate limits, running continuously for months on local WSL initially with cloud migration capability.

## Core Value

Never miss a data collection cycle when APIs are healthy - bulletproof scheduling, recovery, and gap detection ensure complete temporal coverage for model training.

## Requirements

### Validated

- ✓ Basic API client modules for CTA, weather, and track geometry — existing
- ✓ Retry-based resilience using stamina decorator (10 attempts) — existing
- ✓ Environment-based configuration via .env files — existing
- ✓ Type-hinted modern Python 3.13+ codebase — existing
- ✓ Linting pipeline with ruff, basedpyright, codespell — existing

### Active

- [ ] Continuous polling daemon for train positions (~15 second intervals)
- [ ] Multi-source weather data collection (NWS primary, Open-Meteo supplementary, OpenWeatherMap fallback)
- [ ] TTL-based static data cache (stations list, track geometry, station-to-weather mappings)
- [ ] Intelligent retry with exponential backoff (retry transient errors, skip permanent errors, queue for later if possible)
- [ ] Rate limit management staying within free tiers (CTA, NWS unlimited; Open-Meteo 10k/day; OpenWeatherMap very limited)
- [ ] Storage abstraction supporting local Parquet files and cloud object storage (S3/GCS agnostic)
- [ ] Partitioned time-series storage (date-based partitions, compressed Parquet format)
- [ ] Zero deduplication at collection time (store all responses with request timestamps for stuck train detection)
- [ ] Structured logging with JSON format for parsing and analysis
- [ ] Metrics collection and dashboard (API success rates, latency, storage size, collection gaps)
- [ ] Email alerting for critical failures (missed cycles, persistent API errors, disk space issues)
- [ ] Graceful shutdown and restart capability with state preservation
- [ ] Gap detection and reporting (identify missing collection windows)
- [ ] Hybrid configuration (secrets in .env, operational config in TOML/YAML)
- [ ] Process management for 24/7 operation (systemd service or equivalent)
- [ ] Health check endpoint or heartbeat mechanism
- [ ] Local testing with easy cloud deployment (containerizable, infrastructure-agnostic design)

### Out of Scope

- Feature engineering and data transformations — defer to Phase 2 after data collection
- Real-time processing or streaming analytics — raw collection only, analysis happens later
- Data deduplication at collection time — preserve all temporal snapshots including duplicates
- Multi-city or multi-transit support — CTA Chicago only for v1
- Model training and inference — Phase 2 work after sufficient data collected
- Real-time ETA predictions — future product capability after model training
- Web dashboard or user-facing UI — internal data pipeline only
- Paid API tiers — stay within free tier constraints for all services

## Context

**Existing Infrastructure:**
- Modular API client layer with independent modules (no cross-dependencies)
- Script-based execution model transitioning to daemon-based continuous collection
- Early-stage ML pipeline at Phase 1: Data Collection → Phase 2: Model Training (planned)
- Development on WSL Debian, production deployment TBD (cloud VM likely)

**API Strategy:**
- **CTA Train Tracker:** ~15 second polling across all train lines, unlimited free tier
- **Weather (primary):** National Weather Service API for hourly forecasts, unlimited free tier
- **Weather (supplementary):** Open-Meteo for additional variables (snow depth, surface pressure, visibility, showers, snowfall, rain, apparent temperature, wind gusts), 10k calls/day limit
- **Weather (fallback):** OpenWeatherMap for redundancy, very limited free tier (use sparingly)
- **Static data:** CTA stations, track geometry from Chicago Data Portal, low update frequency

**Station-to-Weather Mapping:**
- Map ~300 CTA stations to ~39-50 unique weather grid points to preserve API rate limits
- Weather calls: 24hr × 50 locations × 2 APIs (NWS + Open-Meteo) = ~2,400 calls/day (well under 10k limit)
- TTL-based cache refresh for static mappings (daily/weekly)

**Data Volume Estimates:**
- Train positions: 8 lines × ~15s intervals × 60s/min × 60min/hr × 24hr = ~230k calls/day
- Storage: Multiple months of continuous collection, 1-10GB+ depending on duration
- Format: Parquet with date-based partitioning for efficient querying

**Operational Requirements:**
- Start local WSL for testing and initial collection
- Design for seamless cloud VM migration (AWS/GCP undecided)
- Run 24/7 for months with minimal manual intervention
- Email alerts for critical issues requiring human intervention
- Metrics dashboard for monitoring health and progress

## Constraints

- **API Rate Limits**: Must stay within free tiers - CTA/NWS unlimited, Open-Meteo 10k/day, OpenWeatherMap very limited usage
- **Local Resources**: WSL Debian environment has limited CPU/memory/disk - design for resource efficiency and graceful degradation
- **Tech Stack**: Python 3.13+ with UV package manager (per CLAUDE.md) - no deviations
- **Zero Data Loss**: Maximize uptime and recovery within rate limit constraints - missing data points reduces model quality
- **Cost Efficiency**: No paid API plans, optimize for free tier operation and low cloud storage costs
- **Cloud Agnostic**: AWS vs GCP undecided - abstract storage and infrastructure dependencies

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Store all API responses without deduplication | Need every timestamp to detect stuck trains and model temporal patterns | — Pending |
| Multi-source weather with primary/supplementary/fallback | NWS missing key variables (snow, pressure, visibility), Open-Meteo has 10k limit, OpenWeatherMap backup | — Pending |
| Parquet over CSV for long-term storage | Better compression (~10x), columnar format for analytics, cloud-native | — Pending |
| Station-to-weather mapping (~300 → ~50) | Preserve Open-Meteo 10k/day rate limit while getting full spatial coverage | — Pending |
| Hybrid env + config approach | Secrets in .env (git-ignored), operational config in TOML (version controlled) | — Pending |
| Email alerting over webhook/Slack | Simple, reliable, no external service dependencies for critical alerts | — Pending |
| Local-first with cloud abstraction | Start WSL for testing, design for easy cloud migration without code changes | — Pending |

---
*Last updated: 2026-01-17 after initialization*
