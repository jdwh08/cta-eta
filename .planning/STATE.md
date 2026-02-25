# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-16)

**Core value:** Never miss a data collection cycle when APIs are healthy - bulletproof scheduling, recovery, and gap detection ensure complete temporal coverage for model training.
**Current focus:** v0.2 Data Quality & Compaction — address small-file problem and enforce data integrity before data volume grows

## Current Position

Phase: 11 of 12 (Data Validation & Cleaning)
Plan: 3 of 3 in current phase (phase complete)
Status: Phase 11 complete
Last activity: 2026-02-25 — Completed Phase 11 Plan 03 (operational integration: systemd service + timer + cta-monitor compaction subcommand)

Progress: ██████░░░░ 50%

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table (all decisions with outcomes).

**11-01 IPC Reader decisions:**
- Catch OSError in addition to ArrowInvalid in read_ipc_with_repair: pyarrow 22.0.0 raises OSError for body read failures during truncation
- Crash files (no EOS marker) return was_clean=True: StopIteration from missing EOS is clean, not corruption
- poll_timestamp stored as pa.timestamp('us', tz='UTC') matching pyarrow inference from Python datetime

**11-02 Compaction Pipeline decisions:**
- _compact_one_daemon catches upload exceptions and returns failed metrics rather than propagating — enables _write_sidecar finally block to always run and prevents journal archival on failure
- send_compaction_alert called from _compact_one_daemon on upload failure (not just from main) — ensures alert fires precisely when upload fails, not on unrelated exceptions

**11-03 Operational Integration decisions:**
- No [Install] section in cta-compaction.service — timer unit owns activation, not the service itself
- OnCalendar=America/Chicago *-*-* 03:00:00 uses timezone prefix for DST safety (systemd >= 233)
- Exit code 1 if any run in displayed window has status=failed; partial (empty journals) does not trigger failure exit

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
Stopped at: Completed 11-03-PLAN.md (operational integration: cta-compaction.service + cta-compaction.timer + cta-monitor compaction subcommand)
Resume file: None
