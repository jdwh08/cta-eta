# Roadmap: CTA Train ETA Prediction - Data Collection Infrastructure

## Overview

Building a production-grade data collection system for CTA train positions and weather data to enable spatiotemporal ETA prediction models. The journey progresses from foundational infrastructure (configuration, storage, caching) through dynamic data collection (weather, train polling) to production readiness (resilience, monitoring, deployment). The system runs continuously for months, capturing ~230k train position snapshots daily with zero data loss within API rate limits.

## Domain Expertise

None

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Foundation & Configuration** - Config system, structured logging, daemon framework
- [x] **Phase 2: Storage Abstraction** - Cloud-agnostic Parquet storage with timezone-aware partitioning
- [x] **Phase 3: Static Data Management** - Cache infrastructure and weather grid mapping
- [x] **Phase 4: Static File Caching** - TTL-based caching for CTA stations and track geometry
- [x] **Phase 5: Weather Data Collection** - Multi-source weather integration with rate limit management (Completed 2026-01-24)
- [ ] **Phase 6: Train Position Polling Daemon** - Continuous 15-second polling with retry logic (2/3 plans complete)
- [ ] **Phase 7: Resilience & Recovery** - Intelligent retry, gap detection, graceful shutdown
- [ ] **Phase 8: Monitoring & Metrics** - Metrics collection, dashboard, performance tracking
- [ ] **Phase 9: Alerting & Deployment** - Email alerts, health checks, systemd service

## Phase Details

### Phase 1: Foundation & Configuration
**Goal**: Establish core infrastructure for config management, logging, and daemon lifecycle
**Depends on**: Nothing (first phase)
**Research**: Unlikely (established patterns)
**Plans**: 3 plans

Plans:
- [x] 01-01: Hybrid configuration system (config.toml + .env)
- [x] 01-02: Structured JSON logging with API call decorator
- [x] 01-03: Base daemon class with lifecycle and signal handling

### Phase 2: Storage Abstraction
**Goal**: Cloud-agnostic storage layer supporting local Parquet files and S3/GCS backends
**Depends on**: Phase 1
**Research**: Unlikely (fsspec, pyarrow are well-documented)
**Plans**: 1 plan

Plans:
- [x] 02-01: Storage backends with timezone-aware Parquet partitioning

### Phase 3: Static Data Management
**Goal**: TTL-based cache infrastructure and lazy-discovery weather grid mapping
**Depends on**: Phase 2
**Research**: Unlikely (extending established cache patterns)
**Plans**: 2 plans

Plans:
- [x] 03-01: Generic TTL cache infrastructure with file persistence
- [x] 03-02: Lazy discovery weather grid cache (NWS, Open-Meteo)

### Phase 4: Static File Caching
**Goal**: TTL-based file caching for CTA stations list and track geometry data
**Depends on**: Phase 3
**Research**: Unlikely (reusing cache infrastructure from Phase 3)
**Research topics**: Chicago Data Portal API endpoints for stations and track geometry
**Plans**: 2 plans

Plans:
- [x] 04-01: CTA stations list cache with TTL refresh
- [x] 04-02: Track geometry cache with spatial data handling

### Phase 5: Weather Data Collection
**Goal**: Multi-source weather data collection with NWS primary, Open-Meteo supplementary, OpenWeatherMap fallback
**Depends on**: Phase 4
**Research**: Likely (multi-source integration patterns)
**Research topics**: NWS API missing variables (snow depth, visibility), Open-Meteo supplementary fields, OpenWeatherMap fallback strategy, rate limit coordination across sources
**Plans**: 3 plans

Plans:
- [ ] 05-01: NWS hourly forecast collection (primary source)
- [ ] 05-02: Open-Meteo supplementary data collection (snow, pressure, visibility)
- [ ] 05-03: OpenWeatherMap fallback integration with rate limiting

### Phase 6: Train Position Polling Daemon
**Goal**: Continuous 15-second polling daemon for all CTA train lines with rate limit management
**Depends on**: Phase 5
**Research**: Likely (CTA Train Tracker API polling patterns)
**Research topics**: CTA Train Tracker API response format, optimal polling intervals per line, handling stuck trains (duplicate positions), batch vs per-line polling strategy
**Plans**: 3 plans

Plans:
- [x] 06-01: Train position API integration with polling loop
- [x] 06-02: Rate limiting and integration validation
- [ ] 06-03: Error handling and recovery mechanisms

### Phase 7: Resilience & Recovery
**Goal**: Intelligent retry logic, gap detection, and graceful shutdown with state preservation
**Depends on**: Phase 6
**Research**: Unlikely (stamina retry already integrated, extending with gap detection)
**Plans**: 3 plans

Plans:
- [ ] 07-01: Enhanced retry logic (transient vs permanent error handling)
- [ ] 07-02: Gap detection and reporting for missed collection windows
- [ ] 07-03: Graceful shutdown with state preservation and restart recovery

### Phase 8: Monitoring & Metrics
**Goal**: Metrics collection, performance dashboard, and API health tracking
**Depends on**: Phase 7
**Research**: Likely (metrics collection library choice)
**Research topics**: Metrics backend (Prometheus vs local JSON), dashboard tooling (Grafana vs simple web UI), storage efficiency tracking
**Plans**: 3 plans

Plans:
- [ ] 08-01: Metrics collection framework (API success rates, latency, storage)
- [ ] 08-02: Performance dashboard for monitoring collection health
- [ ] 08-03: API health tracking and success rate reporting

### Phase 9: Alerting & Deployment
**Goal**: Email alerting for critical failures, health checks, and systemd service setup
**Depends on**: Phase 8
**Research**: Likely (systemd service configuration, email SMTP setup)
**Research topics**: systemd service configuration for Python daemons, email alerting via SMTP (Gmail, SendGrid options), health check endpoint patterns, log rotation
**Plans**: 3 plans

Plans:
- [ ] 09-01: Email alerting for critical failures (missed cycles, persistent errors)
- [ ] 09-02: Health check endpoint and heartbeat mechanism
- [ ] 09-03: systemd service configuration and log rotation

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation & Configuration | 3/3 | Complete | 2026-01-17 |
| 2. Storage Abstraction | 1/1 | Complete | 2026-01-17 |
| 3. Static Data Management | 2/2 | Complete | 2026-01-18 |
| 4. Static File Caching | 2/2 | Complete | 2026-01-24 |
| 5. Weather Data Collection | 3/3 | Complete | 2026-01-19 |
| 6. Train Position Polling Daemon | 2/3 | In progress | - |
| 7. Resilience & Recovery | 0/3 | Not started | - |
| 8. Monitoring & Metrics | 0/3 | Not started | - |
| 9. Alerting & Deployment | 0/3 | Not started | - |
