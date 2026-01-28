"""Gap detection logic for train position data collection.

Detects missed collection windows and generates metadata for Parquet datasets to ensure
complete temporal coverage transparency for downstream ML pipelines.

Gap types:
- retry_exhausted: Poll took too long due to retry attempts (<10 minutes)
- downtime: Daemon was stopped or system downtime (>=10 minutes)
"""

from __future__ import annotations


def detect_gap(
    last_poll_timestamp: float | None,
    current_timestamp: float,
    poll_interval: float,
    threshold_multiplier: float = 2.0,
) -> dict[str, bool | float | str | int | None]:
    """Detect if a gap occurred between the last poll and current timestamp.

    A gap occurs when the time between polls exceeds the configured threshold
    (poll_interval * threshold_multiplier).

    Args:
        last_poll_timestamp: Timestamp of last successful poll (None for first poll)
        current_timestamp: Current timestamp
        poll_interval: Configured poll interval in seconds
        threshold_multiplier: Multiplier for gap threshold (default 2.0)

    Returns:
        Dictionary with gap metadata:
        - is_gap: bool - Whether a gap was detected
        - gap_start_timestamp: float | None - Start of gap (same as last_poll_timestamp)
        - gap_end_timestamp: float | None - End of gap (same as current_timestamp)
        - gap_duration_seconds: float | None - Duration of gap in seconds
        - gap_reason: str | None - Reason for gap ("retry_exhausted" or "downtime")
        - missed_poll_cycles: int | None - Estimated number of missed poll cycles

    Raises:
        ValueError: If inputs are invalid (negative delta, zero/negative intervals)

    """
    # Input validation
    if poll_interval <= 0:
        msg = "poll_interval must be > 0"
        raise ValueError(msg)

    if threshold_multiplier <= 0:
        msg = "threshold_multiplier must be > 0"
        raise ValueError(msg)

    # First poll ever - no gap
    if last_poll_timestamp is None or last_poll_timestamp == 0.0:
        return {
            "is_gap": False,
            "gap_start_timestamp": None,
            "gap_end_timestamp": None,
            "gap_duration_seconds": None,
            "gap_reason": None,
            "missed_poll_cycles": None,
        }

    # Validate timestamps
    if current_timestamp < last_poll_timestamp:
        msg = "current_timestamp must be >= last_poll_timestamp"
        raise ValueError(msg)

    # Calculate time delta
    delta = current_timestamp - last_poll_timestamp

    # Calculate threshold
    threshold = poll_interval * threshold_multiplier

    # Check if gap occurred (strict inequality: delta > threshold)
    if delta <= threshold:
        return {
            "is_gap": False,
            "gap_start_timestamp": None,
            "gap_end_timestamp": None,
            "gap_duration_seconds": None,
            "gap_reason": None,
            "missed_poll_cycles": None,
        }

    # Gap detected - determine reason
    # Heuristic: <10 minutes = retry exhaustion, >=10 minutes = downtime
    downtime_threshold_seconds = 600.0  # 10 minutes

    gap_reason = "retry_exhausted" if delta < downtime_threshold_seconds else "downtime"

    # Calculate missed poll cycles
    # Number of poll cycles that would have been scheduled in (last_poll, current]
    # Use floor division to get integer count
    missed_poll_cycles = int(delta // poll_interval)

    return {
        "is_gap": True,
        "gap_start_timestamp": last_poll_timestamp,
        "gap_end_timestamp": current_timestamp,
        "gap_duration_seconds": delta,
        "gap_reason": gap_reason,
        "missed_poll_cycles": missed_poll_cycles,
    }
