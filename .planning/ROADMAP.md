# Roadmap: CTA Train ETA Prediction - Data Collection Infrastructure

## Overview

Building a production-grade data collection system for CTA train positions and weather data to enable spatiotemporal ETA prediction models. The journey progresses from foundational infrastructure (configuration, storage, caching) through dynamic data collection (weather, train polling) to production readiness (resilience, monitoring, deployment). The system runs continuously for months, capturing ~230k train position snapshots daily with zero data loss within API rate limits.

## Domain Expertise

None

## Milestones

- ✅ **[v0.1 Data Collection](milestones/v0.1-data-collection.md)** — Phases 1-9 (shipped 2026-02-16)

## Phases

<details>
<summary>✅ v0.1 Data Collection (Phases 1-9) — SHIPPED 2026-02-16</summary>

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Foundation & Configuration** - Config system, structured logging, daemon framework (completed 2026-01-17)
- [x] **Phase 2: Storage Abstraction** - Cloud-agnostic Parquet storage with timezone-aware partitioning (completed 2026-01-17)
- [x] **Phase 3: Static Data Management** - Cache infrastructure and weather grid mapping (completed 2026-01-18)
- [x] **Phase 4: Static File Caching** - TTL-based caching for CTA stations and track geometry (completed 2026-01-24)
- [x] **Phase 5: Weather Data Collection** - Multi-source weather integration with rate limit management (completed 2026-01-24)
- [x] **Phase 6: Train Position Polling Daemon** - Continuous 15-second polling with retry logic (completed 2026-01-25)
- [x] **Phase 7: Resilience & Recovery** - Intelligent retry, gap detection, graceful shutdown (completed 2026-01-26)
- [x] **Phase 8: Monitoring & Metrics** - Metrics collection, CLI monitoring tool, progressive investigation (completed 2026-01-28)
- [x] **Phase 9: Alerting & Deployment** - Email alerts, health checks, systemd service (completed 2026-02-16)

</details>

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Foundation & Configuration | v0.1 | 3/3 | Complete | 2026-01-17 |
| 2. Storage Abstraction | v0.1 | 1/1 | Complete | 2026-01-17 |
| 3. Static Data Management | v0.1 | 2/2 | Complete | 2026-01-18 |
| 4. Static File Caching | v0.1 | 2/2 | Complete | 2026-01-24 |
| 5. Weather Data Collection | v0.1 | 3/3 | Complete | 2026-01-24 |
| 6. Train Position Polling Daemon | v0.1 | 2/2 | Complete | 2026-01-25 |
| 7. Resilience & Recovery | v0.1 | 3/3 | Complete | 2026-01-26 |
| 8. Monitoring & Metrics | v0.1 | 3/3 | Complete | 2026-01-28 |
| 9. Alerting & Deployment | v0.1 | 4/4 | Complete | 2026-02-16 |
