"""CLI monitoring tool for CTA data collection health.

Provides focused commands for progressive investigation:
- status: Quick health check showing daemon health and last collection times
- errors: Recent failures and API errors
- gaps: Data collection gaps from Parquet metadata
- metrics: Aggregated metrics for alerting automation
- compaction: Show compaction job status and metrics
"""

# ruff: noqa: T201  # print statements are expected in CLI
# ruff: noqa: PLR2004  # magic values are clear in time duration context
# ruff: noqa: PLR0915  # CLI commands naturally have many statements
# ruff: noqa: C901  # CLI commands naturally have high complexity
# ruff: noqa: PLR0912  # CLI commands naturally have many branches

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

try:
    import pyarrow.parquet as pq
except ImportError:
    pq = None  # type: ignore[assignment]

# Constants
_DAEMON_STATE_DIR = Path(".daemon_state")
_STALE_THRESHOLD_SECONDS = 300.0  # 5 minutes
_DEFAULT_DATA_DIR = Path("data")
_DEFAULT_DATASET = "train_positions"
_DEFAULT_DAYS_WINDOW = 7
_DEFAULT_COMPACTION_DIR = Path("data/compaction")


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
            return json.load(f)
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
            print(
                f"{daemon_name:<25} {'unknown':<10} {'N/A':<20} {'N/A':<12} {'N/A':<10}"
            )
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
        print(
            f"Overall: DEGRADED ({stale_count} stale daemon{'s' if stale_count > 1 else ''})"
        )
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


def _add_status_command(subparsers: argparse._SubParsersAction) -> None:
    """Add 'status' subcommand to parser.

    Args:
        subparsers: Subparsers object from ArgumentParser

    """
    parser = subparsers.add_parser(
        "status", help="Show daemon health, last collection times, and overall state"
    )
    parser.set_defaults(func=cmd_status)


def cmd_gaps(args: argparse.Namespace) -> None:
    """Handle 'gaps' command - show data collection gaps from Parquet metadata.

    Args:
        args: Parsed command-line arguments

    """
    dataset = args.dataset
    days = args.days
    json_output = args.json

    # Calculate time window
    cutoff_date = datetime.now(tz=UTC) - timedelta(days=days)

    # Find Parquet files in dataset directory
    dataset_dir = _DEFAULT_DATA_DIR / dataset
    if not dataset_dir.exists():
        print(f"Dataset directory not found: {dataset_dir}")
        print(f"No gaps found for dataset '{dataset}'")
        return

    # Collect gaps from Parquet metadata
    gaps = []
    if pq is None:
        print("Error: pyarrow not installed (required for gap analysis)")
        return

    for parquet_file in sorted(dataset_dir.rglob("*.parquet")):
        try:
            # Schema metadata (e.g. gap_metadata) is on the Arrow schema, not ParquetSchema
            with pq.ParquetFile(parquet_file) as pf:
                schema_metadata = pf.schema_arrow.metadata or {}

            if not schema_metadata or b"gap_metadata" not in schema_metadata:
                continue

            # Decode gap metadata
            gap_data = json.loads(schema_metadata[b"gap_metadata"].decode())

            # Skip if not a gap
            if not gap_data.get("is_gap"):
                continue

            # Check if within time window
            gap_end = gap_data.get("gap_end_timestamp")
            if gap_end and datetime.fromtimestamp(gap_end, tz=UTC) < cutoff_date:
                continue

            gaps.append(gap_data)

        except (OSError, json.JSONDecodeError, KeyError):
            # Skip files with missing/corrupted metadata
            continue

    if json_output:
        # JSON output for machine consumption
        print(json.dumps(gaps, indent=2))
        return

    # Human-readable table output
    print(f"Data Collection Gaps (last {days} days)")
    print("=" * 80)
    print()

    if not gaps:
        print(f"No gaps found for dataset '{dataset}'")
        return

    # Table header
    print(f"{'Date':<20} {'Duration':<15} {'Reason':<20} {'Missed Cycles':<15}")
    print("─" * 80)

    total_duration = 0.0
    total_cycles = 0

    for gap in gaps:
        gap_end = gap.get("gap_end_timestamp")
        if gap_end:
            gap_date = datetime.fromtimestamp(gap_end, tz=UTC).strftime(
                "%Y-%m-%d %H:%M"
            )
        else:
            gap_date = "Unknown"

        duration = gap.get("gap_duration_seconds", 0.0)
        duration_str = _format_duration(float(duration))
        reason = gap.get("gap_reason", "unknown")
        missed = gap.get("missed_poll_cycles", 0)

        total_duration += duration
        total_cycles += missed

        print(f"{gap_date:<20} {duration_str:<15} {reason:<20} {missed:<15}")

    print()
    print(
        f"Summary: {len(gaps)} gaps, {_format_duration(total_duration)} total, {total_cycles} cycles missed"
    )


def cmd_metrics(args: argparse.Namespace) -> None:
    """Handle 'metrics' command - show aggregated metrics for alerting.

    Args:
        args: Parsed command-line arguments

    """
    window_hours = args.window
    json_output = args.json

    daemons = _discover_daemons()

    # Collect metrics from all daemons
    daemon_metrics = {}
    overall_status = "healthy"

    for daemon_name in daemons:
        metrics_file = _DAEMON_STATE_DIR / f"{daemon_name}.metrics.jsonl"

        if not metrics_file.exists():
            continue

        try:
            # Read last line of metrics file
            with metrics_file.open("r", encoding="utf-8") as f:
                lines = f.readlines()
                if not lines:
                    continue

                # Parse last line
                last_line = lines[-1].strip()
                if not last_line:
                    continue

                metrics_snapshot = json.loads(last_line)
                metrics_data = metrics_snapshot.get("metrics", {})

                # Extract metrics for requested window
                window_key = "last_hour" if window_hours == 1 else "last_24h"
                window_metrics = metrics_data.get("time_window_metrics", {}).get(
                    window_key, {}
                )

                if not window_metrics:
                    continue

                # Calculate daemon-level metrics
                overall_success = window_metrics.get("overall_success_rate", 0.0)
                total_calls = window_metrics.get("total_calls", 0)

                # Get per-span metrics for latency info
                per_span = window_metrics.get("per_span_metrics", {})

                # Find highest p95 latency across all spans
                p95_latency = 0.0
                for span_metrics in per_span.values():
                    p95 = span_metrics.get("p95_ms", 0.0)
                    p95_latency = max(p95_latency, p95)

                daemon_metrics[daemon_name] = {
                    "success_rate": overall_success,
                    "error_rate": 1.0 - overall_success,
                    "total_calls": total_calls,
                    "p95_latency_ms": p95_latency,
                }

                # Update overall status
                if overall_success < 0.5:
                    overall_status = "critical"
                elif overall_success < 0.9 and overall_status != "critical":
                    overall_status = "degraded"

        except (OSError, json.JSONDecodeError):
            continue

    # Prepare alert context
    violations = []
    for daemon_name, metrics in daemon_metrics.items():
        if metrics["success_rate"] < 0.9:
            severity = "critical" if metrics["success_rate"] < 0.5 else "warning"
            violations.append(
                {
                    "daemon": daemon_name,
                    "severity": severity,
                    "message": f"{daemon_name} success rate: {metrics['success_rate']:.1%}",
                }
            )

    if json_output:
        # JSON output for Phase 9 consumption
        output = {
            "overall_status": overall_status,
            "timestamp": time.time(),
            "daemons": daemon_metrics,
            "alert_context": {
                "should_alert": bool(violations),
                "violations": violations,
            },
        }
        print(json.dumps(output, indent=2))
        return

    # Human-readable table output
    print(f"Metrics Summary ({window_hours}h window)")
    print("=" * 80)
    print()

    if not daemon_metrics:
        print("No metrics data available")
        return

    # Table header
    print(
        f"{'Daemon':<25} {'Success Rate':<15} {'Error Rate':<12} {'Calls':<8} {'P95 Latency':<12}"
    )
    print("─" * 80)

    for daemon_name, metrics in daemon_metrics.items():
        success_pct = f"{metrics['success_rate']:.1%}"
        error_pct = f"{metrics['error_rate']:.1%}"
        calls = str(metrics["total_calls"])
        latency = f"{metrics['p95_latency_ms']:.0f}ms"

        print(
            f"{daemon_name:<25} {success_pct:<15} {error_pct:<12} {calls:<8} {latency:<12}"
        )

    print()
    print(f"Overall Health: {overall_status.upper()}")


def _add_errors_command(subparsers: argparse._SubParsersAction) -> None:
    """Add 'errors' subcommand to parser.

    Args:
        subparsers: Subparsers object from ArgumentParser

    """
    parser = subparsers.add_parser("errors", help="Show recent failures and API errors")
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


def _add_gaps_command(subparsers: argparse._SubParsersAction) -> None:
    """Add 'gaps' subcommand to parser.

    Args:
        subparsers: Subparsers object from ArgumentParser

    """
    parser = subparsers.add_parser(
        "gaps", help="Show data collection gaps from Parquet metadata"
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default=_DEFAULT_DATASET,
        help=f"Dataset name (default: {_DEFAULT_DATASET})",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=_DEFAULT_DAYS_WINDOW,
        help=f"Time window in days (default: {_DEFAULT_DAYS_WINDOW})",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output as JSON for machine consumption"
    )
    parser.set_defaults(func=cmd_gaps)


def _add_metrics_command(subparsers: argparse._SubParsersAction) -> None:
    """Add 'metrics' subcommand to parser.

    Args:
        subparsers: Subparsers object from ArgumentParser

    """
    parser = subparsers.add_parser(
        "metrics", help="Show aggregated metrics for alerting automation"
    )
    parser.add_argument(
        "--window",
        type=int,
        default=1,
        choices=[1, 24],
        help="Time window in hours: 1 or 24 (default: 1)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output as JSON for machine consumption"
    )
    parser.set_defaults(func=cmd_metrics)


def _read_schema_drift(compaction_dir: Path, daemon: str, date_str: str) -> str:
    """Read schema_drift metadata from local staging Parquet. Returns 'DRIFT', 'OK', or '?'.

    Args:
        compaction_dir: Base compaction directory.
        daemon: Daemon name (e.g., "train_positions").
        date_str: ISO date string (e.g., "2026-02-17").

    Returns:
        "DRIFT" if schema_drift metadata is "true", "OK" if metadata is absent or not "true",
        "?" if the file does not exist or cannot be read.

    """
    parquet_path = compaction_dir / daemon / f"date={date_str}" / "data.parquet"
    if not parquet_path.exists():
        return "?"
    if pq is None:
        return "?"
    try:
        meta = pq.read_metadata(parquet_path)
        kv = meta.metadata or {}
        drift_val = kv.get(b"schema_drift", b"").decode()
        return "DRIFT" if drift_val == "true" else "OK"
    except Exception:  # noqa: BLE001
        return "?"


def cmd_compaction(args: argparse.Namespace) -> None:
    """Handle 'compaction' command - show compaction job status and metrics.

    Args:
        args: Parsed command-line arguments

    """
    days = args.days
    json_output = args.json

    compaction_dir = _DEFAULT_COMPACTION_DIR

    # Glob all sidecar JSON files, most recent first
    sidecars = sorted(
        compaction_dir.glob("compaction-*.json"),
        key=lambda p: p.name,
        reverse=True,
    )

    # Apply --days filter: include records on or after cutoff date (date-level comparison)
    cutoff_date = (datetime.now(tz=UTC) - timedelta(days=days)).date()
    filtered: list[dict[str, object]] = []
    for sidecar_path in sidecars:
        try:
            with sidecar_path.open("r", encoding="utf-8") as f:
                data: dict[str, object] = json.load(f)
            # Parse date from metrics (format: "2026-02-17")
            date_str = str(data.get("date", ""))
            if date_str:
                sidecar_date = datetime.fromisoformat(date_str).date()
                if sidecar_date < cutoff_date:
                    continue
            filtered.append(data)
        except (OSError, json.JSONDecodeError, ValueError):
            continue

    if json_output:
        print(json.dumps(filtered, indent=2))
        # Exit 1 if any run in window has status="failed"
        if any(str(r.get("status", "")) == "failed" for r in filtered):
            sys.exit(1)
        return

    # Human-readable output
    print(f"Compaction Status (last {days} days)")
    print("=" * 77)

    if not filtered:
        print(f"No compaction records found in {compaction_dir}")
        return

    print()
    print(
        f"{'Date':<12} {'Daemon':<20} {'Status':<10} {'Journals':<16} "
        f"{'Rows':<12} {'Upload':<10} {'Elapsed':<8} {'Schema':<8}"
    )
    print("─" * 98)

    has_failure = False
    days_seen: set[str] = set()
    runs_count = 0
    failures_count = 0

    for record in filtered:
        date_val = str(record.get("date", "N/A"))
        daemon_val = str(record.get("daemon", "N/A"))
        raw_status = str(record.get("status", "unknown"))
        journals_found = int(record.get("journals_found", 0))
        journals_repaired = int(record.get("journals_repaired", 0))
        rows_written = int(record.get("rows_written", 0))
        upload_bytes = int(record.get("upload_bytes", 0))
        elapsed = float(record.get("elapsed_seconds", 0.0))

        days_seen.add(date_val)
        runs_count += 1

        # Status formatting
        if raw_status == "partial":
            status_str = "PARTIAL"
        elif raw_status == "failed":
            status_str = "FAILED"
            has_failure = True
            failures_count += 1
        else:
            status_str = raw_status  # "success"

        # Journals column: "96" or "96 (1 repaired)" or "96 (2 skipped)"
        journals_skipped = int(record.get("journals_skipped", 0))
        if journals_repaired > 0:
            journals_str = f"{journals_found} ({journals_repaired} repaired)"
        elif journals_skipped > 0:
            journals_str = f"{journals_found} ({journals_skipped} skipped)"
        else:
            journals_str = str(journals_found)

        # Rows with commas
        rows_str = f"{rows_written:,}"

        # Upload in MB
        upload_mb = upload_bytes / (1024 * 1024)
        upload_str = f"{upload_mb:.1f} MB"

        # Elapsed
        elapsed_str = f"{elapsed:.1f}s"

        # Schema drift status from local Parquet metadata
        schema_str = _read_schema_drift(compaction_dir, daemon_val, date_val)

        print(
            f"{date_val:<12} {daemon_val:<20} {status_str:<10} {journals_str:<16} "
            f"{rows_str:<12} {upload_str:<10} {elapsed_str:<8} {schema_str:<8}"
        )

    print()
    print(
        f"Summary: {len(days_seen)} days, {runs_count} runs, {failures_count} failures"
    )

    if has_failure:
        sys.exit(1)


def _add_compaction_command(subparsers: argparse._SubParsersAction) -> None:
    """Add 'compaction' subcommand to parser.

    Args:
        subparsers: Subparsers object from ArgumentParser

    """
    parser = subparsers.add_parser(
        "compaction", help="Show compaction job status and metrics"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=_DEFAULT_DAYS_WINDOW,
        help=f"Time window in days (default: {_DEFAULT_DAYS_WINDOW})",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.set_defaults(func=cmd_compaction)


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
    _add_gaps_command(subparsers)
    _add_metrics_command(subparsers)
    _add_compaction_command(subparsers)

    args = parser.parse_args(argv)

    # Route to handler
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
