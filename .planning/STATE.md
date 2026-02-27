# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-16)

**Core value:** Never miss a data collection cycle when APIs are healthy - bulletproof scheduling, recovery, and gap detection ensure complete temporal coverage for model training.
**Current focus:** v0.2 Data Quality & Compaction — address small-file problem and enforce data integrity before data volume grows

## Current Position

Phase: 12 of 12 (Schema Enforcement)
Plan: 2 of 3 in current phase
Status: Phase 12 in progress
Last activity: 2026-02-27 — Completed Phase 12 Plan 02 (schema registry integration into compaction: drift detection loop, alert guard, Parquet annotation, bootstrap)

Progress: ████████░░ 70%

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table (all decisions with outcomes).

**12-02 Schema Registry Integration decisions:**
- continue-on-drift: breaking drift journals merged (not skipped) — continue-on-drift policy with column cast + annotation
- cast breaking columns to registry type before concat; ArrowInvalid cast failures log warning and keep as-is
- promote_options="default" in concat_tables handles additive drift (new fields filled with null)
- IPC test files use ipc.new_stream (not ipc.new_file) to match read_ipc_with_repair's ipc.open_stream reader

**12-01 Schema Registry decisions:**
- WIDENING_PAIRS stored as frozenset of string tuples (str(pa.DataType)) for O(1) lookup — avoids pyarrow type object equality complexity
- registry_dict_to_schema() ignores human-readable fields list entirely, uses schema_ipc_b64 exclusively — pa.lib.ensure_type fails on timestamp[us, tz=UTC]
- load_registry() raises ValueError on corrupt JSON (not silent None) — corruption and absence require different operator responses
- bootstrap_registry() checks existence before save — idempotent, safe to call on every compaction run

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

Last session: 2026-02-27
Stopped at: Completed 12-02-PLAN.md (schema registry integration into compaction: drift detection loop, Parquet annotation, bootstrap)
Resume file: None
