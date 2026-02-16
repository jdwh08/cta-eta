"""Alerting logic for CTA data collection monitoring.

Provides threshold checking, cooldown management, violation message formatting,
and SMTP email delivery for automated alerts based on metrics from the CLI monitoring tool.
"""

from __future__ import annotations

import json
import logging
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from pathlib import Path

_log = logging.getLogger(__name__)


class AlertConfig(TypedDict):
    """Configuration for email alerting.

    Fields:
        smtp_host: SMTP server hostname (e.g. "smtp.gmail.com").
        smtp_port: SMTP server port (465 for SSL, 587 for STARTTLS).
        smtp_username: SMTP login username.
        smtp_password: SMTP login password.
        from_addr: Sender email address.
        to_addrs: List of recipient email addresses.
        cooldown_hours: Minimum hours between successive alerts (default 4).
        last_alert_path: Path to the JSON file tracking the last alert timestamp.
    """

    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    from_addr: str
    to_addrs: list[str]
    cooldown_hours: int
    last_alert_path: Path


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
        return float(last_alert)  # type: ignore[arg-type]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        _log.debug("Could not load last alert time from %s", last_alert_path)
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
        _log.debug("Could not save alert timestamp to %s", last_alert_path)


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


def send_email_alert(
    smtp_config: dict[str, Any],
    subject: str,
    body: str,
) -> bool:
    """Send an email alert via SMTP.

    Uses SMTP_SSL for port 465, and SMTP with STARTTLS for port 587 (or any
    other port). Returns True on success, False on failure (error is logged,
    not raised).

    Args:
        smtp_config: Dictionary with SMTP connection parameters. Required keys:
            - host (str): SMTP server hostname.
            - port (int): SMTP server port (465 for SSL, 587 for STARTTLS).
            - username (str): SMTP login username.
            - password (str): SMTP login password.
            - from_addr (str): Sender email address.
            - to_addrs (list[str]): List of recipient email addresses.
        subject: Subject line (will be prefixed with "[CTA ETA Alert]").
        body: Plain-text email body.

    Returns:
        True if the email was sent successfully, False otherwise.

    """
    host: str = smtp_config["host"]
    port: int = smtp_config["port"]
    username: str = smtp_config["username"]
    password: str = smtp_config["password"]
    from_addr: str = smtp_config["from_addr"]
    to_addrs: list[str] = smtp_config["to_addrs"]

    full_subject = f"[CTA ETA Alert] {subject}"

    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = full_subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        if port == 465:  # noqa: PLR2004
            with smtplib.SMTP_SSL(host, port) as server:
                server.login(username, password)
                server.sendmail(from_addr, to_addrs, msg.as_string())
        else:
            with smtplib.SMTP(host, port) as server:
                server.starttls()
                server.login(username, password)
                server.sendmail(from_addr, to_addrs, msg.as_string())
    except smtplib.SMTPException:
        _log.error("Failed to send email alert to %s", to_addrs)
        return False

    _log.info("Email alert sent to %s: %s", to_addrs, full_subject)
    return True
