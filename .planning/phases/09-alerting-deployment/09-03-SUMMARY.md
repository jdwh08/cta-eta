# Phase 9 Plan 3: Heartbeat + Health Check Summary

**Added daemon heartbeat writing to both base classes and created a `cta-health` CLI that checks heartbeat freshness with machine-readable exit codes.**

## Accomplishments

- Added `_write_heartbeat()` to `AsyncBaseDaemon` (called every ~5 minutes in `_run_diagnostics_loop()`) and `BaseDaemon` (called in `stop()` on clean shutdown)
- Created `src/cta_eta/monitoring/health_check.py` with `cta-health` entry point: scans `.daemon_state/*.heartbeat.json`, checks age against configurable threshold (default 600s), and exits 0/1/2 matching cta-monitor convention
- Added `--threshold SECONDS` and `--json` CLI flags for machine-readable output compatible with systemd/automation
- All 710 existing tests pass with no regressions

## Files Created/Modified

- `src/cta_eta/data_collection/orchestration/daemon_async.py` - Added `_write_heartbeat()`, added `import os` and `import time`, call in `_run_diagnostics_loop()`
- `src/cta_eta/data_collection/orchestration/daemon.py` - Added `_write_heartbeat()`, added `import os` and `import time`, call in `stop()`
- `src/cta_eta/monitoring/health_check.py` - New health check CLI (entry point: `cta-health`)
- `pyproject.toml` - Added `cta-health = "cta_eta.monitoring.health_check:main"` entry point

## Decisions Made

- Dropped the `PLR2004` (magic value) noqa comment from `health_check.py` after ruff reported it as unused — the file's threshold comparisons use named variables, not bare literals
- `BaseDaemon` has no periodic run loop in the base class (subclasses define their own), so heartbeat is written on `stop()` only as specified in the plan
- Exit code for "no heartbeat files found" is 0 (not an error) since no daemons may have started yet — consistent with plan spec

## Issues Encountered

None — implementation matched the plan spec cleanly.

## Next Step

Ready for 09-04-PLAN.md (systemd service files + log rotation)
