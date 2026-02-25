# Roadmap: CTA Train ETA Prediction - Data Collection Infrastructure

## Overview

Building a production-grade data collection system for CTA train positions and weather data to enable spatiotemporal ETA prediction models. The journey progresses from foundational infrastructure (configuration, storage, caching) through dynamic data collection (weather, train polling) to production readiness (resilience, monitoring, deployment). The system runs continuously for months, capturing ~230k train position snapshots daily with zero data loss within API rate limits.

## Domain Expertise

None

## Milestones

- ✅ **[v0.1 Data Collection](milestones/v0.1-data-collection.md)** — Phases 1-9 (shipped 2026-02-16)
- 🚧 **v0.2 Data Quality & Compaction** — Phases 10-12 (in progress)

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

### 🚧 v0.2 Data Quality & Compaction (In Progress)

**Milestone Goal:** Address the small-file problem (~5,760 files/day) and enforce data integrity before data volume grows, making the dataset ready for efficient cloud storage and model training.

#### ✅ Phase 10: IPC Journal Writer (Complete — 2026-02-17)

**Goal**: Replace per-poll Parquet writes with Arrow IPC journal files — daemons append each poll to a local journal, rotating to a new file every 15 minutes (configurable), using hive-style directory structure
**Depends on**: Phase 9 (v0.1 complete)
**Context**: [10-CONTEXT.md](phases/10-journal-writer/10-CONTEXT.md)

Plans:
- [x] 10-01: JournalWriter TDD implementation (Arrow IPC stream + rotation)
- [x] 10-02: TrainPositionDaemon refactor (ParquetWriter → JournalWriter)
- [x] 10-03: WeatherDaemon refactor (ParquetWriter → JournalWriter)

#### Phase 11: Data Compaction

**Goal**: Daily batch job (3am Chicago time) that merges yesterday's IPC journal files into a single validated, Snappy-compressed Parquet file per daemon per day, then uploads to cloud storage as immutable raw data
**Depends on**: Phase 10
**Research**: Likely (pyarrow IPC → Parquet conversion patterns, cloud upload integration)
**Plans**: 3 plans

Plans:
- [ ] 11-01-PLAN.md — TDD implementation of IPC reader (schemas.py + ipc_reader.py: file discovery, partial repair, schema validation)
- [ ] 11-02-PLAN.md — Compaction pipeline (uploader.py + archiver.py + compact.py CLI + config.toml [compaction] section)
- [ ] 11-03-PLAN.md — Operational integration (systemd service + timer, cta-monitor compaction subcommand, cta-compact script)

#### Phase 12: Schema Enforcement

**Goal**: Parquet schema registry/validation with drift detection and alerting on schema changes from CTA or weather API updates
**Depends on**: Phase 11
**Research**: Likely (pyarrow schema comparison patterns; best approach for schema registry/drift detection unclear)
**Research topics**: pyarrow schema validation APIs, schema registry patterns for file-based storage, integration with existing cta-monitor CLI
**Plans**: TBD

Plans:
- [ ] 12-01: TBD (run /gsd:plan-phase 12 to break down)

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
| 10. IPC Journal Writer | v0.2 | 3/3 | Complete | 2026-02-17 |
| 11. Data Compaction | 2/3 | In Progress|  | - |
| 12. Schema Enforcement | v0.2 | 0/? | Not started | - |
