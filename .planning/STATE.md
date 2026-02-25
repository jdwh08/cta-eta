# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-16)

**Core value:** Never miss a data collection cycle when APIs are healthy - bulletproof scheduling, recovery, and gap detection ensure complete temporal coverage for model training.
**Current focus:** v0.2 Data Quality & Compaction — address small-file problem and enforce data integrity before data volume grows

## Current Position

Phase: 11 of 12 (Data Validation & Cleaning)
Plan: 1 of 3 in current phase
Status: In progress
Last activity: 2026-02-25 — Completed Phase 11 Plan 01 (IPC reader: schemas + discovery + repair)

Progress: ████░░░░░░ 38%

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table (all decisions with outcomes).

**11-01 IPC Reader decisions:**
- Catch OSError in addition to ArrowInvalid in read_ipc_with_repair: pyarrow 22.0.0 raises OSError for body read failures during truncation
- Crash files (no EOS marker) return was_clean=True: StopIteration from missing EOS is clean, not corruption
- poll_timestamp stored as pa.timestamp('us', tz='UTC') matching pyarrow inference from Python datetime

### Deferred Issues

None.

### Pending Todos

- Start production data collection (run `cta-train-daemon.service` and `cta-weather-daemon.service` via systemd)
- Monitor for several weeks to validate data quality before model training

### Blockers/Concerns

None.

### Roadmap Evolution

- Milestone v0.2 created: data quality & compaction, 2 phases (Phase 10-11)
- Roadmap restructured: Phase 10 → IPC Journal Writer, Phase 11 → Data Compaction, Phase 12 → Schema Enforcement (3 phases total)

## Session Continuity

Last session: 2026-02-25
Stopped at: Completed 11-01-PLAN.md (IPC reader: schemas.py + ipc_reader.py)
Resume file: None
