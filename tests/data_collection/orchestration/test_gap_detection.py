"""Unit tests for gap detection logic with metadata generation.

Tests cover:
- No gap (within threshold)
- Retry exhaustion gap (exceeded threshold, <10 min)
- Downtime gap (exceeded threshold, >=10 min)
- Multiple interval gaps
- Edge cases (exactly at threshold, first poll ever)
- Metadata serialization
"""

from __future__ import annotations

import pytest

from cta_eta.data_collection.orchestration.gap_detection import detect_gap


class TestDetectGap:
    """Tests for detect_gap() function."""

    def test_no_gap_within_threshold(self) -> None:
        """Test that no gap is detected when time delta is within threshold."""
        result = detect_gap(
            last_poll_timestamp=1000.0,
            current_timestamp=1015.0,
            poll_interval=15.0,
            threshold_multiplier=2.0,
        )

        assert result["is_gap"] is False
        assert result["gap_start_timestamp"] is None
        assert result["gap_end_timestamp"] is None
        assert result["gap_duration_seconds"] is None
        assert result["gap_reason"] is None
        assert result["missed_poll_cycles"] is None

    def test_retry_exhaustion_gap(self) -> None:
        """Test gap detection for retry exhaustion (exceeded threshold, <10 min)."""
        result = detect_gap(
            last_poll_timestamp=1000.0,
            current_timestamp=1400.0,  # 400 seconds = 6.67 minutes
            poll_interval=15.0,
            threshold_multiplier=2.0,
        )

        assert result["is_gap"] is True
        assert result["gap_start_timestamp"] == 1000.0
        assert result["gap_end_timestamp"] == 1400.0
        assert result["gap_duration_seconds"] == 400.0
        assert result["gap_reason"] == "retry_exhausted"
        # missed_poll_cycles = int(400 // 15) = 26
        # Number of poll cycles that would have been scheduled in (1000, 1400]
        assert result["missed_poll_cycles"] == 26

    def test_downtime_gap(self) -> None:
        """Test gap detection for downtime (exceeded threshold, >=10 min)."""
        result = detect_gap(
            last_poll_timestamp=1000.0,
            current_timestamp=10000.0,  # 9000 seconds = 150 minutes
            poll_interval=15.0,
            threshold_multiplier=2.0,
        )

        assert result["is_gap"] is True
        assert result["gap_start_timestamp"] == 1000.0
        assert result["gap_end_timestamp"] == 10000.0
        assert result["gap_duration_seconds"] == 9000.0
        assert result["gap_reason"] == "downtime"
        # missed_poll_cycles = int(9000 // 15) = 600
        assert result["missed_poll_cycles"] == 600

    def test_multiple_interval_gap(self) -> None:
        """Test gap detection for multiple missed poll cycles."""
        result = detect_gap(
            last_poll_timestamp=1000.0,
            current_timestamp=1200.0,  # 200 seconds
            poll_interval=15.0,
            threshold_multiplier=2.0,
        )

        assert result["is_gap"] is True
        assert result["gap_start_timestamp"] == 1000.0
        assert result["gap_end_timestamp"] == 1200.0
        assert result["gap_duration_seconds"] == 200.0
        assert result["gap_reason"] == "retry_exhausted"  # <10 min
        # missed_poll_cycles = int(200 // 15) = 13
        assert result["missed_poll_cycles"] == 13

    def test_edge_case_exactly_at_threshold(self) -> None:
        """Test that exactly at threshold is NOT considered a gap."""
        result = detect_gap(
            last_poll_timestamp=1000.0,
            current_timestamp=1030.0,  # exactly 2x interval (30s = 2 * 15s)
            poll_interval=15.0,
            threshold_multiplier=2.0,
        )

        # Exactly at threshold should NOT be a gap (strict inequality: delta > threshold)
        assert result["is_gap"] is False
        assert result["gap_start_timestamp"] is None
        assert result["gap_end_timestamp"] is None
        assert result["gap_duration_seconds"] is None
        assert result["gap_reason"] is None
        assert result["missed_poll_cycles"] is None

    def test_edge_case_first_poll_ever(self) -> None:
        """Test that first poll ever (no previous poll) is not considered a gap."""
        result = detect_gap(
            last_poll_timestamp=None,
            current_timestamp=1000.0,
            poll_interval=15.0,
            threshold_multiplier=2.0,
        )

        assert result["is_gap"] is False
        assert result["gap_start_timestamp"] is None
        assert result["gap_end_timestamp"] is None
        assert result["gap_duration_seconds"] is None
        assert result["gap_reason"] is None
        assert result["missed_poll_cycles"] is None

    def test_edge_case_zero_last_poll_timestamp(self) -> None:
        """Test that zero last_poll_timestamp is treated as first poll."""
        result = detect_gap(
            last_poll_timestamp=0.0,
            current_timestamp=1000.0,
            poll_interval=15.0,
            threshold_multiplier=2.0,
        )

        assert result["is_gap"] is False

    def test_downtime_boundary_exactly_10_minutes(self) -> None:
        """Test that exactly 10 minutes gap is classified as downtime."""
        result = detect_gap(
            last_poll_timestamp=1000.0,
            current_timestamp=1600.0,  # exactly 600 seconds = 10 minutes
            poll_interval=15.0,
            threshold_multiplier=2.0,
        )

        assert result["is_gap"] is True
        assert result["gap_duration_seconds"] == 600.0
        assert result["gap_reason"] == "downtime"  # >= 10 min

    def test_retry_exhaustion_just_under_10_minutes(self) -> None:
        """Test that gap just under 10 minutes is classified as retry_exhausted."""
        result = detect_gap(
            last_poll_timestamp=1000.0,
            current_timestamp=1599.0,  # 599 seconds = 9.98 minutes
            poll_interval=15.0,
            threshold_multiplier=2.0,
        )

        assert result["is_gap"] is True
        assert result["gap_duration_seconds"] == 599.0
        assert result["gap_reason"] == "retry_exhausted"  # < 10 min

    def test_missed_poll_cycles_calculation(self) -> None:
        """Test that missed_poll_cycles is correctly calculated as floor division."""
        # Delta = 75s, interval = 15s
        # missed_poll_cycles = int(75 // 15) = 5
        result = detect_gap(
            last_poll_timestamp=1000.0,
            current_timestamp=1075.0,
            poll_interval=15.0,
            threshold_multiplier=2.0,
        )

        assert result["is_gap"] is True
        assert result["missed_poll_cycles"] == 5

    def test_missed_poll_cycles_partial_interval(self) -> None:
        """Test that missed_poll_cycles correctly floors partial intervals."""
        # Delta = 77s, interval = 15s
        # missed_poll_cycles = int(77 // 15) = 5 (not 6)
        result = detect_gap(
            last_poll_timestamp=1000.0,
            current_timestamp=1077.0,
            poll_interval=15.0,
            threshold_multiplier=2.0,
        )

        assert result["is_gap"] is True
        assert result["missed_poll_cycles"] == 5

    def test_custom_threshold_multiplier(self) -> None:
        """Test that custom threshold_multiplier works correctly."""
        # threshold = 15 * 3 = 45 seconds
        # delta = 40 seconds < 45 seconds -> no gap
        result = detect_gap(
            last_poll_timestamp=1000.0,
            current_timestamp=1040.0,
            poll_interval=15.0,
            threshold_multiplier=3.0,
        )

        assert result["is_gap"] is False

        # delta = 50 seconds > 45 seconds -> gap
        result = detect_gap(
            last_poll_timestamp=1000.0,
            current_timestamp=1050.0,
            poll_interval=15.0,
            threshold_multiplier=3.0,
        )

        assert result["is_gap"] is True
        assert result["gap_duration_seconds"] == 50.0

    def test_negative_delta_raises_error(self) -> None:
        """Test that negative time delta (current < last) raises ValueError."""
        with pytest.raises(ValueError, match="current_timestamp must be >= last_poll_timestamp"):
            detect_gap(
                last_poll_timestamp=1000.0,
                current_timestamp=900.0,  # Current before last!
                poll_interval=15.0,
                threshold_multiplier=2.0,
            )

    def test_zero_poll_interval_raises_error(self) -> None:
        """Test that zero poll_interval raises ValueError."""
        with pytest.raises(ValueError, match="poll_interval must be > 0"):
            detect_gap(
                last_poll_timestamp=1000.0,
                current_timestamp=1100.0,
                poll_interval=0.0,
                threshold_multiplier=2.0,
            )

    def test_negative_poll_interval_raises_error(self) -> None:
        """Test that negative poll_interval raises ValueError."""
        with pytest.raises(ValueError, match="poll_interval must be > 0"):
            detect_gap(
                last_poll_timestamp=1000.0,
                current_timestamp=1100.0,
                poll_interval=-15.0,
                threshold_multiplier=2.0,
            )

    def test_zero_threshold_multiplier_raises_error(self) -> None:
        """Test that zero threshold_multiplier raises ValueError."""
        with pytest.raises(ValueError, match="threshold_multiplier must be > 0"):
            detect_gap(
                last_poll_timestamp=1000.0,
                current_timestamp=1100.0,
                poll_interval=15.0,
                threshold_multiplier=0.0,
            )

    def test_negative_threshold_multiplier_raises_error(self) -> None:
        """Test that negative threshold_multiplier raises ValueError."""
        with pytest.raises(ValueError, match="threshold_multiplier must be > 0"):
            detect_gap(
                last_poll_timestamp=1000.0,
                current_timestamp=1100.0,
                poll_interval=15.0,
                threshold_multiplier=-2.0,
            )
