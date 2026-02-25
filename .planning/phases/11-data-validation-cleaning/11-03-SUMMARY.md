---
phase: 11-data-validation-cleaning
plan: "03"
subsystem: infra/monitoring
tags: [systemd, oneshot, timer, cta-monitor, compaction, cli, deployment]
dependency_graph:
  requires:
    - src/cta_eta/data_collection/compaction/compact.py
    - data/compaction/compaction-*.json
  provides:
    - deploy/cta-compaction.service
    - deploy/cta-compaction.timer
    - src/cta_eta/monitoring/cli.py (compaction subcommand)
    - cta-compact CLI entrypoint in pyproject.toml
  affects:
    - "Production deployment: enable cta-compaction.timer to run nightly at 3am"
tech_stack:
  added: []
  patterns:
    - Type=oneshot systemd service with no [Install] section (timer-activated)
    - Persistent=true timer with timezone-prefixed OnCalendar for DST-safe scheduling
    - CLI subcommand reading JSON sidecar files with --days window filter and --json output
key_files:
  created:
    - deploy/cta-compaction.service
    - deploy/cta-compaction.timer
  modified:
    - src/cta_eta/monitoring/cli.py
    - pyproject.toml
key_decisions:
  - "No [Install] section in cta-compaction.service — timer unit owns activation, not the service itself"
  - "OnCalendar=America/Chicago *-*-* 03:00:00 uses timezone prefix for DST safety (systemd >= 233)"
  - "Exit code 1 if any run in displayed window has status=failed; exit 0 for success/partial"
patterns_established:
  - "Oneshot job pattern: Type=oneshot service with no Restart= + Persistent=true timer"
  - "Sidecar-driven monitoring: CLI reads JSON sidecars rather than querying job state directly"
requirements_completed: []
duration: 3min
completed: "2026-02-25"
---

# Phase 11 Plan 03: Operational Integration (Systemd + cta-monitor compaction) Summary

**Systemd oneshot service + Persistent=true timer deploying compact.py at 3am Chicago time, plus `compaction` subcommand added to cta-monitor CLI reading JSON sidecar files written by compact.py**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-25T13:46:36Z
- **Completed:** 2026-02-25T13:49:30Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- Systemd oneshot service (`cta-compaction.service`) runs `compact.py` module with proper journal logging and no-restart policy (timer handles retry)
- Persistent calendar timer fires at 3am Chicago time daily with DST-safe timezone prefix; catches missed runs on next boot
- `cta-monitor compaction` subcommand shows per-daemon run history with date, status, journals, rows, upload size, elapsed; exit code 1 on any failure
- `cta-compact` registered as project script entrypoint in pyproject.toml (`uv sync` makes it available)

## Task Commits

Each task was committed atomically:

1. **Task 1: Systemd service + timer + cta-compact entrypoint** - `0076f54` (feat)
2. **Task 2: Add compaction subcommand to cta-monitor CLI** - `2a7ac9c` (feat)

**Plan metadata:** `(pending)` (docs: complete plan)

## Files Created/Modified

- `deploy/cta-compaction.service` - Oneshot systemd service running `cta_eta.data_collection.compaction.compact` module; no `[Install]` section (timer-activated)
- `deploy/cta-compaction.timer` - Persistent calendar timer at `America/Chicago *-*-* 03:00:00`; WantedBy=timers.target
- `src/cta_eta/monitoring/cli.py` - Added `cmd_compaction`, `_add_compaction_command`, `_DEFAULT_COMPACTION_DIR` constant; wired into `main()`
- `pyproject.toml` - Added `cta-compact = "cta_eta.data_collection.compaction.compact:main"` script entry

## Decisions Made

- No `[Install]` section in `cta-compaction.service`: timer unit manages activation; the service has no targets of its own
- `OnCalendar=America/Chicago *-*-* 03:00:00` uses timezone prefix syntax (systemd >= 233, standard Debian) for DST safety — chosen over UTC offset per RESEARCH.md Pitfall 1
- Exit code 1 if any run in the displayed window has `status=failed`; `partial` (empty journals) does not trigger failure exit

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required. To activate in production:
1. `systemctl enable --now cta-compaction.timer`
2. Verify: `systemctl status cta-compaction.timer`

## Next Phase Readiness

- Phase 11 complete: IPC repair reader (11-01), compaction pipeline (11-02), and operational integration (11-03) all done
- Phase 12 (Schema Enforcement) can begin; compact.py is ready to enforce schemas during daily runs
- Production deployment path complete: enable `cta-compaction.timer` and `cta-compact` is registered via `uv sync`

## Self-Check: PASSED

All files verified to exist on disk and all commits verified in git history.

---
*Phase: 11-data-validation-cleaning*
*Completed: 2026-02-25*
