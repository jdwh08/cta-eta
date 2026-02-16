# Phase 9 Plan 2: SMTP Email Sender + Alert Runner Summary

**Completed end-to-end alerting pipeline: stdlib SMTP delivery via `send_email_alert()`, `AlertConfig` TypedDict, and `run_alerts.py` module with `cta-alerts` entry point that fetches metrics, checks cooldown-guarded thresholds, and sends email on daemon violations.**

## Accomplishments

- Added `send_email_alert(smtp_config, subject, body) -> bool` to `alerting.py` using stdlib `smtplib` — SMTP_SSL for port 465, STARTTLS for port 587; subject prefixed with "[CTA ETA Alert]"; SMTPException caught and logged, returns False on failure
- Added `AlertConfig` TypedDict with fields: `smtp_host`, `smtp_port`, `smtp_username`, `smtp_password`, `from_addr`, `to_addrs`, `cooldown_hours`, `last_alert_path`
- Created `src/cta_eta/monitoring/run_alerts.py` with `main()` entry point: loads `[alerting]` config from `config.toml`, builds SMTP config from `.env` SMTP_USERNAME/SMTP_PASSWORD, invokes `cta-monitor metrics --json` via subprocess, extracts `alert_context`, calls `should_send_alert()` → `send_email_alert()` → `save_alert_timestamp()` pipeline
- Added `[alerting]` section to `config.toml` (disabled by default with example SMTP settings)
- Added `SMTP_USERNAME` and `SMTP_PASSWORD` variables to `.env.template`
- Added `cta-alerts = "cta_eta.monitoring.run_alerts:main"` entry point to `pyproject.toml`
- 710 existing tests pass, 0 regressions

## Performance

- Duration: ~15 minutes
- Tasks completed: 2/2
- Files modified: 5 (alerting.py, run_alerts.py created, config.toml, .env.template, pyproject.toml)

## Task Commits

| Task | Hash | Description |
|------|------|-------------|
| Task 1 | `0d39867` | feat(09-02): add send_email_alert and AlertConfig to alerting.py |
| Task 2 | `867dc23` | feat(09-02): create alert runner module with config, entry point, and SMTP integration |

## Files Created/Modified

- `src/cta_eta/monitoring/alerting.py` - Added `send_email_alert()` function and `AlertConfig` TypedDict; all existing functions preserved unchanged
- `src/cta_eta/monitoring/run_alerts.py` - New module: alert runner with `main()` entry point; loads config, fetches metrics via subprocess, runs threshold+cooldown check, sends email
- `config.toml` - Added `[alerting]` section with `enabled = false`, SMTP defaults, cooldown settings
- `.env.template` - Added `SMTP_USERNAME` and `SMTP_PASSWORD` variable templates
- `pyproject.toml` - Added `cta-alerts` script entry point

## Decisions Made

**`alert_context` extraction in runner**: The `cta-monitor metrics --json` output nests `should_alert` and `violations` inside an `alert_context` key (not at the top level). The runner extracts `alert_context` and passes it to `should_send_alert()`, which checks the top-level `should_alert` key within that slice. This matches the 09-01 SUMMARY note about plan 02 handling the extraction.

**`dotenv` import at module level**: Moved `from dotenv import load_dotenv` to module-level imports to satisfy ruff PLC0415 (import-not-at-top-level rule). The `dotenv` package is already a project dependency used in `data_collection/config.py`.

**S607 suppression on subprocess list arg**: Ruff S607 (partial executable path) is suppressed on the `["cta-monitor", "metrics", "--json"]` list argument with `# noqa: S607` since `cta-monitor` is an installed package entry point, not an arbitrary system binary.

**No noqa for PLR2004 on port 465 check**: Port 465 comparison uses `# noqa: PLR2004` inline since the numeric constant is a well-known SMTP port with clear context in the docstring.

## Issues Encountered

**Ruff RUF100 (unused noqa) on TRY400**: Initial `# noqa: TRY400` on the `_log.error()` call in `send_email_alert` was unnecessary because `TRY400` only triggers when re-raising within an except block. Removed.

**Ruff S607 on subprocess.run**: The noqa comment must go on the list argument line (where the violation is detected), not the `subprocess.run()` call line itself.

**Ruff PLC0415 (import-not-at-top-level)**: `from dotenv import load_dotenv` was initially inside `_build_smtp_config()`. Moved to module-level imports.

## Next Step

Ready for 09-03-PLAN.md (heartbeat mechanism + health check) and 09-04-PLAN.md (systemd + log rotation)
