# Phase 9 Plan 4: systemd + Log Rotation Summary

**Created deploy/ directory with 6 production deployment files: 4 systemd unit files, logrotate config, and an operator README.**

## Accomplishments

- Created `cta-train-daemon.service` and `cta-weather-daemon.service` as long-running systemd services with SIGTERM + 60s graceful shutdown, matching Phase 7 daemon implementation
- Created `cta-alerts.service` (oneshot) and `cta-alerts.timer` (15-minute interval) for scheduled alert checks
- Created `logrotate.conf` using `copytruncate` for JSONL and log files, with documented rationale for why `create` would break daemon open file handles
- Created `deploy/README.md` covering all 8 required sections: prerequisites, installation, configuration, systemd setup, log rotation, monitoring CLI tools, troubleshooting, and updating

## Files Created/Modified

- `deploy/cta-train-daemon.service` - systemd service for train position daemon
- `deploy/cta-weather-daemon.service` - systemd service for weather collection daemon
- `deploy/cta-alerts.service` - oneshot alerting service
- `deploy/cta-alerts.timer` - 15-minute alert check timer
- `deploy/logrotate.conf` - Log rotation with copytruncate
- `deploy/README.md` - Deployment guide

## Decisions Made

- **Daemon ExecStart paths**: Used `-m cta_eta.data_collection.orchestration.train_position_daemon` and `-m cta_eta.data_collection.orchestration.weather_daemon` instead of the plan's suggested `run_train_daemon` / `run_weather_daemon` modules (which do not exist). Both daemon modules have `if __name__ == "__main__"` blocks that launch the daemon directly. This is more accurate than the plan template.
- **`cta-alerts` ExecStart**: Used `/opt/cta-eta/.venv/bin/cta-alerts` per the plan, noting that this entry point is installed by Phase 9-02 (parallel plan). Documented in README that `uv sync` installs all CLI entry points.
- **logrotate paths**: Used `/opt/cta-eta/.daemon_state/*.jsonl` and `/opt/cta-eta/logs/*.log` to match production installation path `/opt/cta-eta` and config.toml defaults (`diagnostics.event_log_path`, `logging.log_path`).

## Issues Encountered

None. The daemon module paths required inspection of the actual codebase since the plan suggested module paths (`run_train_daemon`, `run_weather_daemon`) that do not exist. Correct paths were determined from the `__main__` blocks in the existing daemon files.

## Next Step

Phase 9 complete — data collection infrastructure fully production-ready.
