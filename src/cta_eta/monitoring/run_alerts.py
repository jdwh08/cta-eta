"""Alert runner for CTA data collection monitoring.

Fetches metrics from `cta-monitor metrics --json`, checks thresholds via
cooldown-guarded logic, and sends email alerts when daemon violations are detected.

Intended to be called from a cron job or systemd timer:
    cta-alerts          # via installed entry point
    uv run python src/cta_eta/monitoring/run_alerts.py  # direct

Exits 0 in all non-exceptional cases (config disabled, subprocess failure,
no alert needed, alert sent). Never crashes a cron job with an unhandled exception.
"""

# ruff: noqa: T201  # print statements are expected in CLI

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tomllib
from pathlib import Path

from dotenv import load_dotenv

from cta_eta.monitoring.alerting import (
    format_alert_message,
    save_alert_timestamp,
    send_email_alert,
    should_send_alert,
)

logger = logging.getLogger(__name__)

# Default config.toml path: project root is 4 levels above this file.
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config.toml"


def _load_alerting_config(config_path: Path) -> dict[str, object] | None:
    """Load the [alerting] section from config.toml.

    Returns None if the file is missing, the section is absent, or
    alerting is explicitly disabled.

    Args:
        config_path: Path to the TOML config file.

    Returns:
        The [alerting] config dict, or None if alerting should not run.

    """
    if not config_path.exists():
        print(f"Info: config file not found at {config_path}, alerting skipped")
        return None

    try:
        with config_path.open("rb") as f:
            config: dict[str, object] = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        print(f"Warning: failed to parse config.toml: {exc}")
        return None

    alerting_section = config.get("alerting")
    if not isinstance(alerting_section, dict):
        print("Info: [alerting] section absent from config.toml, alerting skipped")
        return None

    if not alerting_section.get("enabled", False):
        print("Info: alerting.enabled = false in config.toml, alerting skipped")
        return None

    return alerting_section


def _build_email_config(alerting_cfg: dict[str, object]) -> dict[str, object]:
    """Build email config from [alerting] section and environment variables.

    Uses ``email_provider`` (default ``"mailjet"``) to choose the backend.
    Only ``"mailjet"`` is implemented; add branches here and in
    ``alerting.send_email_alert`` for other API-based providers.
    From/to addresses come from ``smtp_from`` and ``smtp_to`` for all providers.

    Returns:
        Dict with ``provider`` and the keys required for that provider.

    """
    load_dotenv()
    provider = str(alerting_cfg.get("email_provider", "mailjet")).strip().lower()
    from_addr = str(os.getenv("SMTP_FROM"))
    to_addrs: list[str] = (
        os.getenv("SMTP_TO", "").split(",") if os.getenv("SMTP_TO") else []
    )
    to_addrs = [addr.strip() for addr in to_addrs]

    if provider == "mailjet":
        return {
            "provider": "mailjet",
            "api_key": (os.getenv("MAILJET_API_KEY") or "").strip(),
            "api_secret": (os.getenv("MAILJET_API_SECRET") or "").strip(),
            "from_addr": from_addr,
            "to_addrs": to_addrs,
        }
    return {
        "provider": provider,
        "from_addr": from_addr,
        "to_addrs": to_addrs,
    }


def _fetch_metrics() -> dict[str, object] | None:
    """Run `cta-monitor metrics --json` and parse JSON output.

    Returns None if the subprocess fails or produces no output — the caller
    should treat this as a soft failure (no crash, no alert).

    Returns:
        Parsed metrics dict, or None on failure.

    """
    try:
        result = subprocess.run(
            ["cta-monitor", "metrics", "--json"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("Could not run cta-monitor metrics: %s", exc)
        return None

    if result.returncode != 0 or not result.stdout.strip():
        logger.warning(
            "cta-monitor metrics --json failed (exit %d): %s",
            result.returncode,
            result.stderr.strip(),
        )
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.warning("Could not parse metrics JSON: %s", exc)
        return None


def main() -> None:
    """Run alert check: fetch metrics, check thresholds, send email if needed."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    alerting_cfg = _load_alerting_config(_DEFAULT_CONFIG_PATH)
    if alerting_cfg is None:
        sys.exit(0)

    # Build paths and config values
    cooldown_hours = int(alerting_cfg.get("cooldown_hours", 4))
    last_alert_state = str(
        alerting_cfg.get("last_alert_state", ".daemon_state/last_alert.json")
    )
    last_alert_path = Path(last_alert_state)

    # Fetch metrics via subprocess
    full_metrics = _fetch_metrics()
    if full_metrics is None:
        print("Warning: could not fetch metrics, skipping alert check")
        sys.exit(0)

    # Extract alert_context (nested in metrics --json output)
    alert_context = full_metrics.get("alert_context", {})
    if not isinstance(alert_context, dict):
        alert_context = {}

    # Check cooldown and threshold
    if not should_send_alert(alert_context, last_alert_path, cooldown_hours):
        print("No alert needed")
        sys.exit(0)

    # Extract violations for message formatting
    violations_raw = alert_context.get("violations", [])
    violations: list[dict[str, object]] = (
        violations_raw if isinstance(violations_raw, list) else []
    )

    n_violations = len(violations)
    subject = (
        f"CTA Daemon Alert: {n_violations} violation{'s' if n_violations != 1 else ''}"
    )

    violation_body = format_alert_message(violations)
    full_body = (
        violation_body + "\n\nFull metrics:\n" + json.dumps(full_metrics, indent=2)
    )

    email_config = _build_email_config(alerting_cfg)
    sent = send_email_alert(email_config, subject, full_body)

    if sent:
        save_alert_timestamp(last_alert_path)
        print(f"Alert sent: {n_violations} violation{'s' if n_violations != 1 else ''}")
    else:
        print("Warning: alert email failed to send")
        sys.exit(1)


if __name__ == "__main__":
    main()
