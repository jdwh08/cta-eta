"""CLI monitoring tool for CTA data collection health.

Provides focused commands for progressive investigation:
- status: Quick health check showing daemon health and last collection times
- errors: Recent failures and API errors
- gaps: Data collection gaps from Parquet metadata
- metrics: Aggregated metrics for alerting automation
"""

# ruff: noqa: T201  # print statements are expected in CLI
# ruff: noqa: PLR2004  # magic values are clear in time duration context
# ruff: noqa: PLR0915  # CLI commands naturally have many statements
# ruff: noqa: C901  # CLI commands naturally have high complexity

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Constants
_DAEMON_STATE_DIR = Path(".daemon_state")
_STALE_THRESHOLD_SECONDS = 300.0  # 5 minutes


def _discover_daemons() -> list[str]:
    """Discover daemon names from state files.

    Returns:
        List of daemon names (e.g., ["TrainPositionDaemon", "WeatherDaemon"])

    """
    if not _DAEMON_STATE_DIR.exists():
        return []

    daemon_names = set()
    for path in _DAEMON_STATE_DIR.glob("*.json"):
        # Skip diagnostics files
        if path.stem.endswith(".diagnostics"):
            continue
        # Extract daemon name (e.g., "TrainPositionDaemon.json" -> "TrainPositionDaemon")
        daemon_name = path.stem
        daemon_names.add(daemon_name)

    return sorted(daemon_names)


def _read_daemon_state(daemon_name: str) -> dict[str, object] | None:
    """Read daemon state JSON file.

    Args:
        daemon_name: Name of the daemon (e.g., "TrainPositionDaemon")

    Returns:
        Dictionary with state data, or None if file doesn't exist or can't be read

    """
    state_file = _DAEMON_STATE_DIR / f"{daemon_name}.json"
    if not state_file.exists():
        return None

    try:
        with state_file.open("r", encoding="utf-8") as f:
            return json.load(f)  # type: ignore[no-any-return]
    except (OSError, json.JSONDecodeError):
        return None


def _format_duration(seconds: float) -> str:
    """Format duration in human-readable form.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string (e.g., "2m 15s", "2h 31m", "3d 5h")

    """
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        minutes = int(seconds / 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s" if secs > 0 else f"{minutes}m"
    if seconds < 86400:
        hours = int(seconds / 3600)
        minutes = int((seconds % 3600) / 60)
        return f"{hours}h {minutes}m" if minutes > 0 else f"{hours}h"
    days = int(seconds / 86400)
    hours = int((seconds % 86400) / 3600)
    return f"{days}d {hours}h" if hours > 0 else f"{days}d"


def _classify_status(staleness: float | None) -> str:
    """Classify daemon status based on staleness.

    Args:
        staleness: Time since last poll in seconds, or None if unknown

    Returns:
        Status string: "active", "stale", or "unknown"

    """
    if staleness is None:
        return "unknown"
    if staleness > _STALE_THRESHOLD_SECONDS:
        return "stale"
    return "active"


def _read_diagnostic_events(daemon_name: str) -> list[dict[str, object]]:
    """Read diagnostic events from JSONL file.

    Args:
        daemon_name: Name of the daemon

    Returns:
        List of event dictionaries, sorted by timestamp descending

    """
    events_file = _DAEMON_STATE_DIR / f"{daemon_name}.events.jsonl"
    if not events_file.exists():
        return []

    events = []
    try:
        with events_file.open("r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    events.append(event)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []

    # Sort by timestamp descending (most recent first)
    events.sort(key=lambda e: e.get("ts", 0.0), reverse=True)
    return events


def cmd_status(args: argparse.Namespace) -> None:
    """Handle 'status' command - show daemon health overview.

    Args:
        args: Parsed command-line arguments (unused but required by argparse)

    """
    _ = args  # Unused but required by argparse interface
    daemons = _discover_daemons()

    if not daemons:
        print("CTA Data Collection Status")
        print("==========================")
        print()
        print("No daemons found (no state files in .daemon_state/)")
        sys.exit(2)

    print("CTA Data Collection Status")
    print("==========================")
    print()

    # Table header
    print(
        f"{'Daemon':<25} {'Status':<10} {'Last Poll':<20} {'Records':<12} {'Staleness':<10}"
    )
    print("─" * 85)

    now = time.time()
    stale_count = 0
    unknown_count = 0

    for daemon_name in daemons:
        state = _read_daemon_state(daemon_name)

        if state is None:
            print(f"{daemon_name:<25} {'unknown':<10} {'N/A':<20} {'N/A':<12} {'N/A':<10}")
            unknown_count += 1
            continue

        # Extract state data
        last_poll = state.get("last_poll_timestamp")
        if last_poll is None:
            # Try alternate field name
            last_poll = state.get("last_collection_timestamp")

        total_records = state.get("total_records_collected", "N/A")

        # Calculate staleness
        if last_poll is not None and isinstance(last_poll, (int, float)):
            staleness = now - float(last_poll)
            status = _classify_status(staleness)
            last_poll_str = f"{_format_duration(staleness)} ago"
            staleness_str = "STALE" if status == "stale" else "-"
        else:
            staleness = None
            status = "unknown"
            last_poll_str = "N/A"
            staleness_str = "N/A"

        if status == "stale":
            stale_count += 1
        elif status == "unknown":
            unknown_count += 1

        # Format records
        records_str = str(total_records) if total_records != "N/A" else "N/A"

        print(
            f"{daemon_name:<25} {status:<10} {last_poll_str:<20} {records_str:<12} {staleness_str:<10}"
        )

    print()

    # Overall status
    if stale_count > 0:
        print(f"Overall: DEGRADED ({stale_count} stale daemon{'s' if stale_count > 1 else ''})")
        sys.exit(1)
    if unknown_count > 0:
        print(
            f"Overall: UNKNOWN ({unknown_count} unknown daemon{'s' if unknown_count > 1 else ''})"
        )
        sys.exit(2)
    print("Overall: HEALTHY")
    sys.exit(0)


def cmd_errors(args: argparse.Namespace) -> None:
    """Handle 'errors' command - show recent failures and API errors.

    Args:
        args: Parsed command-line arguments

    """
    limit = args.limit
    json_output = args.json

    daemons = _discover_daemons()

    # Collect all error events from all daemons
    all_errors = []
    for daemon_name in daemons:
        events = _read_diagnostic_events(daemon_name)
        # Filter for error events
        for event in events:
            kind = event.get("kind")
            # Error events have kind="error" or span name ending in ".error"
            name = event.get("name", "")
            if kind == "error" or (isinstance(name, str) and name.endswith(".error")):
                all_errors.append(event)

    # Sort by timestamp descending
    all_errors.sort(key=lambda e: e.get("ts", 0.0), reverse=True)

    # Apply limit
    all_errors = all_errors[:limit]

    if json_output:
        # JSON output for machine consumption
        output = [
            {
                "timestamp": event.get("ts"),
                "daemon_name": event.get("daemon_class"),
                "span_name": event.get("name"),
                "error_type": event.get("error_type"),
                "error_message": event.get("error_message"),
                "http_status": event.get("http_status"),
            }
            for event in all_errors
        ]
        print(json.dumps(output, indent=2))
        return

    # Human-readable table output
    print(f"Recent Errors (last {limit})")
    print("=" * 80)
    print()

    if not all_errors:
        print("No errors found")
        return

    # Table header
    print(f"{'Time':<18} {'Daemon':<25} {'Error Type':<25} {'Message':<30}")
    print("─" * 100)

    now = time.time()
    for event in all_errors:
        ts = event.get("ts")
        if isinstance(ts, (int, float)):
            time_ago = _format_duration(now - float(ts))
            time_str = f"{time_ago} ago"
        else:
            time_str = "N/A"

        daemon_name = event.get("daemon_class", "Unknown")
        error_type = event.get("error_type", "Unknown")
        error_message = event.get("error_message", "")

        # Truncate long messages
        if isinstance(error_message, str) and len(error_message) > 50:
            error_message = error_message[:47] + "..."

        # Truncate long daemon names
        if isinstance(daemon_name, str) and len(daemon_name) > 23:
            daemon_name = daemon_name[:20] + "..."

        # Truncate long error types
        if isinstance(error_type, str) and len(error_type) > 23:
            error_type = error_type[:20] + "..."

        print(f"{time_str:<18} {daemon_name:<25} {error_type:<25} {error_message:<30}")


def _add_status_command(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Add 'status' subcommand to parser.

    Args:
        subparsers: Subparsers object from ArgumentParser

    """
    parser = subparsers.add_parser(
        "status", help="Show daemon health, last collection times, and overall state"
    )
    parser.set_defaults(func=cmd_status)


def _add_errors_command(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Add 'errors' subcommand to parser.

    Args:
        subparsers: Subparsers object from ArgumentParser

    """
    parser = subparsers.add_parser(
        "errors", help="Show recent failures and API errors"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of errors to display (default: 20)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output as JSON for machine consumption"
    )
    parser.set_defaults(func=cmd_errors)


def main(argv: Sequence[str] | None = None) -> None:
    """Execute CLI monitoring tool.

    Args:
        argv: Command-line arguments (defaults to sys.argv if None)

    """
    parser = argparse.ArgumentParser(
        prog="cta-monitor", description="CTA data collection monitoring CLI"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Add subcommands
    _add_status_command(subparsers)
    _add_errors_command(subparsers)

    args = parser.parse_args(argv)

    # Route to handler
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
