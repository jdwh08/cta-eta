# Roadmap: CTA ETA Data Collection Infrastructure

## Overview

Build a production-grade data collection system that captures CTA train positions every ~15 seconds and weather conditions every 30 minutes to create training datasets for spatiotemporal ETA prediction models. The journey progresses from foundational infrastructure through specialized collection systems, culminating in robust monitoring and deployment for months of continuous 24/7 operation.

## Domain Expertise

None

## Phases

- [x] **Phase 1: Foundation & Configuration** - Hybrid config system, structured logging, daemon framework
- [ ] **Phase 2: Storage Abstraction Layer** - Parquet storage with local/cloud abstraction, timezone-aware partitioning
- [ ] **Phase 3: Static Data Management** - TTL-based cache infrastructure for stations, geometry, weather mappings
- [ ] **Phase 4: Train Position Polling** - Continuous ~15s polling daemon across 8 CTA lines
- [ ] **Phase 5: Multi-Source Weather Collection** - 30min polling with NWS/Open-Meteo/OpenWeatherMap orchestration
- [ ] **Phase 6: Resilience & Recovery** - Intelligent retry, state preservation, graceful shutdown
- [ ] **Phase 7: Monitoring & Metrics** - Gap detection, health checks, API metrics dashboard
- [ ] **Phase 8: Alerting & Deployment** - SMTP email alerts, systemd service, 24/7 process management

## Phase Details

### Phase 1: Foundation & Configuration
**Goal**: Establish core infrastructure with hybrid configuration (secrets in .env, operational config in TOML), structured JSON logging, and base daemon framework for continuous operation.
**Depends on**: Nothing (first phase)
**Research**: Unlikely (Python config/logging are established patterns)
**Status**: ✅ Complete (2026-01-17)

Plans:
- [x] 01-01: Configuration System (config.toml + .env loader)
- [x] 01-02: Structured Logging (JSON logger with API decorator)
- [x] 01-03: Daemon Framework (lifecycle + signals + state preservation)

### Phase 2: Storage Abstraction Layer
**Goal**: Build cloud-agnostic Parquet storage layer supporting local files and object storage (S3/GCS), with date-based partitioning split at 3:00 AM America/Chicago time to minimize splitting active train runs.
**Depends on**: Phase 1
**Research**: Likely (cloud-agnostic abstraction design)
**Research topics**: Parquet with fsspec for S3/GCS abstraction, partitioning strategies, compression options, timezone-aware daily splits at 3 AM Chicago time
**Plans**: TBD

Plans:
- [ ] TBD during planning

### Phase 3: Static Data Management
**Goal**: Design and implement TTL-based cache infrastructure for CTA stations, track geometry, and station-to-weather mappings (~300 stations → ~50 weather grid points), with configurable refresh intervals.
**Depends on**: Phase 2
**Research**: Likely (TTL cache infrastructure design)
**Research topics**: TTL cache patterns in Python, refresh strategies (lazy vs eager), cache invalidation, storing mappings for station-to-weather grid reduction
**Plans**: TBD

Plans:
- [ ] TBD during planning

### Phase 4: Train Position Polling
**Goal**: Implement continuous polling daemon that queries CTA Train Tracker API every ~15 seconds across all 8 train lines, storing all responses without deduplication to detect stuck trains and temporal patterns.
**Depends on**: Phase 3
**Research**: Likely (precise timing patterns)
**Research topics**: Python daemon patterns for precise ~15s intervals, asyncio scheduling vs threading, rate limit tracking, preventing drift in polling intervals
**Plans**: TBD

Plans:
- [ ] TBD during planning

### Phase 5: Multi-Source Weather Collection
**Goal**: Build 30-minute weather polling system coordinating NWS (primary), Open-Meteo (supplementary variables), and OpenWeatherMap (fallback), staying within 10k/day rate limit (~4,800 calls/day actual).
**Depends on**: Phase 4
**Research**: Likely (multi-source coordination)
**Research topics**: Multi-source API orchestration, fallback strategies when primary fails, rate limit management across sources, scheduling 30min intervals efficiently, combining weather data from multiple APIs
**Plans**: TBD

Plans:
- [ ] TBD during planning

### Phase 6: Resilience & Recovery
**Goal**: Implement intelligent retry with exponential backoff (retry transient errors, skip permanent errors), graceful shutdown with state preservation, and restart capability to ensure zero data loss within rate limits.
**Depends on**: Phase 5
**Research**: Likely (advanced retry patterns)
**Research topics**: Distinguishing transient vs permanent API errors, exponential backoff strategies beyond stamina decorator, state preservation across restarts (where were we in polling cycle), graceful shutdown patterns
**Plans**: TBD

Plans:
- [ ] TBD during planning

### Phase 7: Monitoring & Metrics
**Goal**: Build monitoring infrastructure with gap detection (identify missing collection windows), API success rate/latency metrics, storage size tracking, and health check endpoint for external monitoring.
**Depends on**: Phase 6
**Research**: Likely (gap detection algorithms)
**Research topics**: Time-series gap detection algorithms, metrics collection patterns (Prometheus-style?), health check endpoint designs, identifying missed polling cycles vs API downtime
**Plans**: TBD

Plans:
- [ ] TBD during planning

### Phase 8: Alerting & Deployment
**Goal**: Configure SMTP email alerts for critical failures (missed cycles, persistent API errors, disk space), create systemd service for 24/7 operation, and establish process management for months of continuous collection.
**Depends on**: Phase 7
**Research**: Likely (SMTP and systemd setup)
**Research topics**: Python SMTP email configuration, systemd service file structure for Python daemons, restart policies, logging integration with journald, environment variable handling in systemd
**Plans**: TBD

Plans:
- [ ] TBD during planning

## Progress

**Execution Order:**
Phases execute sequentially: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation & Configuration | 3/3 | ✅ Complete | 2026-01-17 |
| 2. Storage Abstraction Layer | 0/TBD | Not started | - |
| 3. Static Data Management | 0/TBD | Not started | - |
| 4. Train Position Polling | 0/TBD | Not started | - |
| 5. Multi-Source Weather Collection | 0/TBD | Not started | - |
| 6. Resilience & Recovery | 0/TBD | Not started | - |
| 7. Monitoring & Metrics | 0/TBD | Not started | - |
| 8. Alerting & Deployment | 0/TBD | Not started | - |
