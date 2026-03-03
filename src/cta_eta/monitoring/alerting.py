"""Alerting logic for CTA data collection monitoring.

Provides threshold checking, cooldown management, violation message formatting,
and email delivery via API-based providers (e.g. Mailjet) for automated alerts
based on metrics from the CLI monitoring tool.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

MAILJET_SEND_URL = "https://api.mailjet.com/v3.1/send"


def load_last_alert_time(last_alert_path: Path) -> float | None:
    """Load the timestamp of the last alert from the state file.

    Args:
        last_alert_path: Path to the JSON file storing the last alert timestamp.

    Returns:
        Unix timestamp of the last alert as a float, or None if missing or invalid.

    """
    if not last_alert_path.exists():
        return None

    try:
        with last_alert_path.open("r", encoding="utf-8") as f:
            data: dict[str, object] = json.load(f)
        last_alert = data["last_alert"]
        return float(last_alert)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        logger.debug("Could not load last alert time from %s", last_alert_path)
        return None


def save_alert_timestamp(last_alert_path: Path) -> None:
    """Save the current time as the last alert timestamp.

    Uses best-effort I/O: OSError is suppressed and logged at debug level.

    Args:
        last_alert_path: Path to write the JSON alert state file.

    """
    try:
        last_alert_path.parent.mkdir(parents=True, exist_ok=True)
        with last_alert_path.open("w", encoding="utf-8") as f:
            json.dump({"last_alert": time.time()}, f)
    except OSError:
        logger.debug("Could not save alert timestamp to %s", last_alert_path)


def should_send_alert(
    metrics_data: dict[str, object],
    last_alert_path: Path,
    cooldown_hours: int,
) -> bool:
    """Determine whether an alert should be sent based on metrics and cooldown.

    Args:
        metrics_data: Dictionary from ``cta-monitor metrics --json`` output.
            Must contain a ``should_alert`` boolean key.
        last_alert_path: Path to the JSON file tracking the last alert time.
        cooldown_hours: Minimum hours between successive alerts.

    Returns:
        True if an alert should be sent, False otherwise.

    Decision logic:
        - If ``should_alert`` is missing or False → return False immediately.
        - If no previous alert exists (file missing or invalid) → return True.
        - If time since last alert exceeds cooldown → return True.
        - Otherwise (still within cooldown) → return False.

    """
    if not metrics_data.get("should_alert", False):
        return False

    last_alert_time = load_last_alert_time(last_alert_path)
    if last_alert_time is None:
        return True

    cooldown_seconds = cooldown_hours * 3600
    elapsed = time.time() - last_alert_time
    return elapsed > cooldown_seconds


def format_alert_message(violations: list[dict[str, object]]) -> str:
    """Format a list of metric violations into a human-readable alert message.

    Args:
        violations: List of violation dictionaries, each containing keys such as
            ``metric``, ``threshold``, and ``actual``.

    Returns:
        Multi-line string with one violation per line, or a default message if
        the list is empty.

    Each line follows the format::

        - {metric}: actual={actual} exceeds threshold={threshold}

    Missing keys are replaced with sensible defaults to avoid KeyError.

    """
    if not violations:
        return "No specific violations reported"

    lines: list[str] = []
    for violation in violations:
        metric = violation.get("metric", "unknown")
        actual = violation.get("actual", "N/A")
        threshold = violation.get("threshold", "N/A")
        lines.append(f"- {metric}: actual={actual} exceeds threshold={threshold}")

    return "\n".join(lines)


def _send_via_mailjet(config: dict[str, Any], subject: str, body: str) -> bool:
    """Send one message via Mailjet Send API v3.1. Returns True on success."""
    api_key = str(config["api_key"]).strip()
    api_secret = str(config["api_secret"]).strip()
    from_addr = str(config["from_addr"])
    to_addrs = list(config["to_addrs"])
    payload = {
        "Messages": [
            {
                "From": {"Email": from_addr},
                "To": [{"Email": addr} for addr in to_addrs],
                "Subject": subject,
                "TextPart": body,
            }
        ]
    }
    try:
        with httpx.Client() as client:
            resp = client.post(
                MAILJET_SEND_URL,
                auth=(api_key, api_secret),
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError:
        logger.exception(
            "Mailjet send failed: %s", resp.text[:500] if resp.text else ""
        )
        return False
    logger.info("Mailjet alert sent to %s: %s", to_addrs, subject)
    return True


def send_email_alert(
    email_config: dict[str, Any],
    subject: str,
    body: str,
) -> bool:
    """Send an email alert via the configured API-based provider.

    Dispatches on ``email_config["provider"]``. Currently only ``"mailjet"``
    is implemented; add a ``_send_via_<provider>`` helper and a branch here
    to support other services (e.g. SendGrid, Postmark). Returns True on
    success, False on failure (error is logged, not raised).

    Mailjet config keys: api_key, api_secret, from_addr, to_addrs.

    Subject is prefixed with "[CTA ETA Alert]".
    """
    full_subject = f"[CTA ETA Alert] {subject}"
    provider = (str(email_config.get("provider") or "mailjet")).strip().lower()

    if provider == "mailjet":
        return _send_via_mailjet(email_config, full_subject, body)

    logger.error("Unsupported email provider: %s", provider)
    return False
