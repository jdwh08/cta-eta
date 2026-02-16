"""Health check CLI for CTA daemon liveness monitoring.

Checks daemon heartbeat files in .daemon_state/ and exits with a code
indicating overall health status:
- 0: All discovered daemons have fresh heartbeats (or no heartbeat files found)
- 1: One or more daemons have stale heartbeats (age > threshold)
- 2: Heartbeat directory missing (.daemon_state/ doesn't exist)
"""

# ruff: noqa: T201  # print statements are expected in CLI

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
_DEFAULT_STALE_THRESHOLD_SECONDS = 600  # 10 minutes = 2x diagnostics interval


def _read_heartbeat(heartbeat_file: Path) -> dict[str, object] | None:
    """Read a heartbeat JSON file.

    Args:
        heartbeat_file: Path to the heartbeat file

    Returns:
        Dictionary with heartbeat data, or None if file can't be read

    """
    try:
        with heartbeat_file.open("r", encoding="utf-8") as f:
            return json.load(f)  # type: ignore[no-any-return]
    except (OSError, json.JSONDecodeError):
        return None


def _check_daemons(
    threshold: int,
) -> tuple[list[dict[str, object]], str]:
    """Scan heartbeat files and check daemon liveness.

    Args:
        threshold: Maximum age in seconds before a heartbeat is considered stale

    Returns:
        Tuple of (daemon_results, overall_status) where overall_status is
        "healthy", "degraded", or "unknown"

    """
    now = time.time()
    daemon_results: list[dict[str, object]] = []
    has_stale = False

    for heartbeat_file in sorted(_DAEMON_STATE_DIR.glob("*.heartbeat.json")):
        heartbeat = _read_heartbeat(heartbeat_file)

        if heartbeat is None:
            # Skip corrupt/unreadable files with a warning to stderr
            print(
                f"Warning: Could not read heartbeat file {heartbeat_file}",
                file=sys.stderr,
            )
            continue

        ts = heartbeat.get("timestamp")
        daemon_name = heartbeat.get("daemon", heartbeat_file.stem)
        pid = heartbeat.get("pid")

        if not isinstance(ts, (int, float)):
            print(
                f"Warning: Invalid timestamp in {heartbeat_file}",
                file=sys.stderr,
            )
            continue

        age_seconds = now - float(ts)
        status = "stale" if age_seconds > threshold else "healthy"

        if status == "stale":
            has_stale = True

        daemon_results.append(
            {
                "name": daemon_name,
                "status": status,
                "age_seconds": round(age_seconds),
                "pid": pid,
            }
        )

    overall_status = "degraded" if has_stale else "healthy"
    return daemon_results, overall_status


def main(argv: Sequence[str] | None = None) -> None:
    """Execute health check CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv if None)

    """
    parser = argparse.ArgumentParser(
        prog="cta-health",
        description="Check CTA daemon liveness based on heartbeat freshness",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=_DEFAULT_STALE_THRESHOLD_SECONDS,
        metavar="SECONDS",
        help=f"Stale threshold in seconds (default: {_DEFAULT_STALE_THRESHOLD_SECONDS})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON for machine consumption",
    )

    args = parser.parse_args(argv)
    threshold: int = args.threshold
    json_output: bool = args.json

    # Exit 2 if state directory is missing
    if not _DAEMON_STATE_DIR.exists():
        if json_output:
            print(
                json.dumps(
                    {
                        "status": "unknown",
                        "threshold_seconds": threshold,
                        "daemons": [],
                        "error": ".daemon_state/ directory not found",
                    },
                    indent=2,
                )
            )
        else:
            print("Daemon Health Check")
            print("===================")
            print()
            print("Error: .daemon_state/ directory not found")
            print("No daemons have been started yet.")
        sys.exit(2)

    daemon_results, overall_status = _check_daemons(threshold)

    if json_output:
        output = {
            "status": overall_status,
            "threshold_seconds": threshold,
            "daemons": daemon_results,
        }
        print(json.dumps(output, indent=2))
        sys.exit(1 if overall_status == "degraded" else 0)

    # Human-readable output
    print(f"Daemon Health (threshold: {threshold}s)")
    print("=" * 40)

    if not daemon_results:
        print()
        print("No heartbeat files found in .daemon_state/")
        print("Daemons may not have run yet or heartbeat writing is not enabled.")
        sys.exit(0)

    print()
    # Table header
    print(f"{'Daemon':<30} {'Status':<10} {'Age':<15} {'PID':<10}")
    print("─" * 67)

    for daemon in daemon_results:
        name = str(daemon.get("name", "unknown"))
        status = str(daemon.get("status", "unknown")).upper()
        age = daemon.get("age_seconds")
        pid = daemon.get("pid")

        age_str = f"{age}s ago" if isinstance(age, int) else "N/A"
        pid_str = f"pid={pid}" if pid is not None else "N/A"

        print(f"{name:<30} {status:<10} {age_str:<15} {pid_str:<10}")

    print()
    stale_count = sum(1 for d in daemon_results if d.get("status") == "stale")
    if stale_count > 0:
        print(
            f"Overall: DEGRADED ({stale_count} stale daemon{'s' if stale_count > 1 else ''})"
        )
        sys.exit(1)

    print("Overall: HEALTHY")
    sys.exit(0)


if __name__ == "__main__":
    main()
