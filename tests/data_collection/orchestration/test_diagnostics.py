"""Unit tests for diagnostics module."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import httpx
import pytest

from cta_eta.data_collection.orchestration.diagnostics import (
    DaemonDiagnostics,
    DaemonDiagnosticsConfig,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    from pytest_mock import MockerFixture


@pytest.fixture
def mock_logger() -> MagicMock:
    """Create a mock logger for testing."""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.debug = MagicMock()
    logger.exception = MagicMock()
    return logger


@pytest.fixture
def cleanup_state_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Generator[None]:
    """Change to temporary directory for daemon state files."""
    original_cwd = Path.cwd()
    monkeypatch.chdir(tmp_path)
    yield
    monkeypatch.chdir(original_cwd)


@pytest.fixture
def enabled_config() -> DaemonDiagnosticsConfig:
    """Create an enabled diagnostics config."""
    return DaemonDiagnosticsConfig(
        enabled=True,
        summary_interval_seconds=60.0,
        max_recent_events=100,
        event_log_path="test.events.jsonl",
        event_log_max_bytes=1024,
        event_log_backups=2,
    )


@pytest.fixture
def disabled_config() -> DaemonDiagnosticsConfig:
    """Create a disabled diagnostics config."""
    return DaemonDiagnosticsConfig(enabled=False)


@pytest.fixture
def diagnostics(
    mock_logger: MagicMock, enabled_config: DaemonDiagnosticsConfig
) -> DaemonDiagnostics:
    """Create a DaemonDiagnostics instance with enabled config."""
    return DaemonDiagnostics(
        logger=mock_logger, daemon_name="TestDaemon", config=enabled_config
    )


@pytest.fixture
def disabled_diagnostics(
    mock_logger: MagicMock, disabled_config: DaemonDiagnosticsConfig
) -> DaemonDiagnostics:
    """Create a DaemonDiagnostics instance with disabled config."""
    return DaemonDiagnostics(
        logger=mock_logger, daemon_name="TestDaemon", config=disabled_config
    )


@pytest.fixture
def cleanup_test_files() -> Generator[None]:
    """Clean up test.events.jsonl and backup files after tests."""
    yield
    # Clean up test.events.jsonl and any backup files
    test_file = Path("test.events.jsonl")
    if test_file.exists():
        test_file.unlink()
    # Clean up backup files (.1, .2, etc.)
    for backup_num in range(1, 10):  # Check up to .9
        backup_file = Path(f"test.events.jsonl.{backup_num}")
        if backup_file.exists():
            backup_file.unlink()
    # Clean up subdir/nested/test.events.jsonl if it exists
    nested_file = Path("subdir/nested/test.events.jsonl")
    if nested_file.exists():
        nested_file.unlink()
        # Clean up parent directories if empty
        try:
            nested_file.parent.rmdir()
            nested_file.parent.parent.rmdir()
        except OSError:
            pass  # Directory not empty or doesn't exist


class TestDaemonDiagnosticsConfig:
    """Tests for DaemonDiagnosticsConfig."""

    def test_default_config(self) -> None:
        """Default config has expected values."""
        # Arrange & Act
        config = DaemonDiagnosticsConfig()

        # Assert
        assert config.enabled is False
        assert config.summary_interval_seconds == 300.0
        assert config.max_recent_events == 250
        assert config.event_log_path is None
        assert config.event_log_max_bytes == 5 * 1024 * 1024
        assert config.event_log_backups == 3

    def test_from_config_with_none(self) -> None:
        """from_config returns default config when raw is None and config has no diagnostics."""
        config = DaemonDiagnosticsConfig.from_config(
            None, daemon_name="TestDaemon", config=None
        )
        # NOTE(jdwh08): these fields come from the config.toml file
        assert config.enabled is True
        assert config.summary_interval_seconds == 30.0
        assert config.max_recent_events == 64

    def test_from_config_resolves_from_config_section(self) -> None:
        """from_config(None, config=...) uses the [diagnostics] section from the given config."""
        full_config = {
            "diagnostics": {
                "enabled": True,
                "summary_interval_seconds": 45,
                "max_recent_events": 64,
            }
        }
        config = DaemonDiagnosticsConfig.from_config(
            None, daemon_name="TestDaemon", config=full_config
        )
        assert config.enabled is True
        assert config.summary_interval_seconds == 45
        assert config.max_recent_events == 64

    def test_from_config_with_empty_dict(self) -> None:
        """from_config returns default config when raw is empty."""
        # Arrange & Act
        config = DaemonDiagnosticsConfig.from_config({}, daemon_name="TestDaemon")

        # Assert
        assert config.enabled is False
        # event_log_path is None when enabled=False and not explicitly set
        assert config.event_log_path is None

    def test_from_config_enabled(self) -> None:
        """from_config parses enabled flag correctly."""
        # Arrange
        raw = {"enabled": True}

        # Act
        config = DaemonDiagnosticsConfig.from_config(raw, daemon_name="TestDaemon")

        # Assert
        assert config.enabled is True

    def test_from_config_enabled_with_truthy_value(self) -> None:
        """from_config converts truthy values to True."""
        # Arrange
        raw = {"enabled": 1}

        # Act
        config = DaemonDiagnosticsConfig.from_config(raw, daemon_name="TestDaemon")

        # Assert
        assert config.enabled is True

    def test_from_config_summary_interval_seconds(self) -> None:
        """from_config parses summary_interval_seconds correctly."""
        # Arrange
        raw = {"summary_interval_seconds": 120.0}

        # Act
        config = DaemonDiagnosticsConfig.from_config(raw, daemon_name="TestDaemon")

        # Assert
        assert config.summary_interval_seconds == 120.0

    def test_from_config_summary_interval_seconds_enforces_minimum(self) -> None:
        """from_config enforces minimum summary_interval_seconds of 1.0."""
        # Arrange
        raw = {"summary_interval_seconds": 0.5}

        # Act
        config = DaemonDiagnosticsConfig.from_config(raw, daemon_name="TestDaemon")

        # Assert
        assert config.summary_interval_seconds == 1.0

    def test_from_config_summary_interval_seconds_invalid_float(self) -> None:
        """from_config handles invalid float values gracefully."""
        # Arrange
        raw = {"summary_interval_seconds": "not_a_float"}

        # Act
        config = DaemonDiagnosticsConfig.from_config(raw, daemon_name="TestDaemon")

        # Assert
        assert config.summary_interval_seconds == 300.0

    def test_from_config_max_recent_events(self) -> None:
        """from_config parses max_recent_events correctly."""
        # Arrange
        raw = {"max_recent_events": 500}

        # Act
        config = DaemonDiagnosticsConfig.from_config(raw, daemon_name="TestDaemon")

        # Assert
        assert config.max_recent_events == 500

    def test_from_config_max_recent_events_enforces_minimum(self) -> None:
        """from_config enforces minimum max_recent_events of 10."""
        # Arrange
        raw = {"max_recent_events": 5}

        # Act
        config = DaemonDiagnosticsConfig.from_config(raw, daemon_name="TestDaemon")

        # Assert
        assert config.max_recent_events == 10

    def test_from_config_max_recent_events_invalid_int(self) -> None:
        """from_config handles invalid int values gracefully."""
        # Arrange
        raw = {"max_recent_events": "not_an_int"}

        # Act
        config = DaemonDiagnosticsConfig.from_config(raw, daemon_name="TestDaemon")

        # Assert
        assert config.max_recent_events == 250

    def test_from_config_event_log_path(self) -> None:
        """from_config uses provided event_log_path."""
        # Arrange
        raw = {"event_log_path": "/custom/path.jsonl"}

        # Act
        config = DaemonDiagnosticsConfig.from_config(raw, daemon_name="TestDaemon")

        # Assert
        assert config.event_log_path == "/custom/path.jsonl"

    def test_from_config_event_log_path_default_when_none(self) -> None:
        """from_config uses default path when event_log_path is None."""
        # Arrange
        raw = {"enabled": True}

        # Act
        config = DaemonDiagnosticsConfig.from_config(raw, daemon_name="TestDaemon")

        # Assert
        assert config.event_log_path == ".daemon_state/TestDaemon.events.jsonl"

    def test_from_config_event_log_max_bytes(self) -> None:
        """from_config parses event_log_max_bytes correctly."""
        # Arrange
        raw = {"event_log_max_bytes": 2048}

        # Act
        config = DaemonDiagnosticsConfig.from_config(raw, daemon_name="TestDaemon")

        # Assert
        assert config.event_log_max_bytes == 2048

    def test_from_config_event_log_max_bytes_enforces_minimum(self) -> None:
        """from_config enforces minimum event_log_max_bytes of 1024."""
        # Arrange
        raw = {"event_log_max_bytes": 512}

        # Act
        config = DaemonDiagnosticsConfig.from_config(raw, daemon_name="TestDaemon")

        # Assert
        assert config.event_log_max_bytes == 1024

    def test_from_config_event_log_backups(self) -> None:
        """from_config parses event_log_backups correctly."""
        # Arrange
        raw = {"event_log_backups": 5}

        # Act
        config = DaemonDiagnosticsConfig.from_config(raw, daemon_name="TestDaemon")

        # Assert
        assert config.event_log_backups == 5

    def test_from_config_event_log_backups_enforces_minimum(self) -> None:
        """from_config enforces minimum event_log_backups of 0."""
        # Arrange
        raw = {"event_log_backups": -1}

        # Act
        config = DaemonDiagnosticsConfig.from_config(raw, daemon_name="TestDaemon")

        # Assert
        assert config.event_log_backups == 0

    def test_from_config_all_fields(self) -> None:
        """from_config handles all fields together."""
        # Arrange
        raw = {
            "enabled": True,
            "summary_interval_seconds": 180.0,
            "max_recent_events": 200,
            "event_log_path": "/tmp/test.jsonl",  # noqa: S108
            "event_log_max_bytes": 4096,
            "event_log_backups": 4,
        }

        # Act
        config = DaemonDiagnosticsConfig.from_config(raw, daemon_name="TestDaemon")

        # Assert
        assert config.enabled is True
        assert config.summary_interval_seconds == 180.0
        assert config.max_recent_events == 200
        assert config.event_log_path == "/tmp/test.jsonl"  # noqa: S108
        assert config.event_log_max_bytes == 4096
        assert config.event_log_backups == 4

        # Clean up
        if Path("/tmp/test.jsonl").exists():  # noqa: S108
            Path("/tmp/test.jsonl").unlink()  # noqa: S108


class TestDaemonDiagnosticsInit:
    """Tests for DaemonDiagnostics initialization."""

    def test_init_creates_run_id(
        self, mock_logger: MagicMock, enabled_config: DaemonDiagnosticsConfig
    ) -> None:
        """__init__ creates a unique run_id."""
        # Arrange & Act
        diag = DaemonDiagnostics(
            logger=mock_logger, daemon_name="TestDaemon", config=enabled_config
        )

        # Assert
        assert diag.run_id is not None
        assert len(diag.run_id) == 32  # hex string length

    def test_init_creates_different_run_ids(
        self, mock_logger: MagicMock, enabled_config: DaemonDiagnosticsConfig
    ) -> None:
        """__init__ creates different run_ids for different instances."""
        # Arrange & Act
        diag1 = DaemonDiagnostics(
            logger=mock_logger, daemon_name="TestDaemon", config=enabled_config
        )
        diag2 = DaemonDiagnostics(
            logger=mock_logger, daemon_name="TestDaemon", config=enabled_config
        )

        # Assert
        assert diag1.run_id != diag2.run_id

    def test_init_initializes_counters(
        self, mock_logger: MagicMock, enabled_config: DaemonDiagnosticsConfig
    ) -> None:
        """__init__ initializes all counters and dequeues."""
        # Arrange & Act
        diag = DaemonDiagnostics(
            logger=mock_logger, daemon_name="TestDaemon", config=enabled_config
        )

        # Assert
        assert len(diag._recent_events) == 0
        assert len(diag._span_counts) == 0
        assert len(diag._span_errors) == 0
        assert len(diag._error_types) == 0
        assert len(diag._durations_ms) == 0

    def test_enabled_property(
        self, diagnostics: DaemonDiagnostics, disabled_diagnostics: DaemonDiagnostics
    ) -> None:
        """Enabled property returns config.enabled."""
        # Assert
        assert diagnostics.enabled is True
        assert disabled_diagnostics.enabled is False


class TestDaemonDiagnosticsNewCycleId:
    """Tests for DaemonDiagnostics.new_cycle_id()."""

    def test_new_cycle_id_returns_short_id(
        self, diagnostics: DaemonDiagnostics
    ) -> None:
        """new_cycle_id returns a 10-character hex string."""
        # Arrange & Act
        cycle_id = diagnostics.new_cycle_id()

        # Assert
        assert len(cycle_id) == 10
        assert all(c in "0123456789abcdef" for c in cycle_id)

    def test_new_cycle_id_returns_different_ids(
        self, diagnostics: DaemonDiagnostics
    ) -> None:
        """new_cycle_id returns different IDs on each call."""
        # Arrange & Act
        id1 = diagnostics.new_cycle_id()
        id2 = diagnostics.new_cycle_id()

        # Assert
        assert id1 != id2


class TestDaemonDiagnosticsSpan:
    """Tests for DaemonDiagnostics.span() async context manager."""

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("cleanup_test_files")
    async def test_span_records_successful_operation(
        self, diagnostics: DaemonDiagnostics
    ) -> None:
        """Span records successful operation with timing."""
        # Arrange
        span_name = "test_span"

        # Act
        async with diagnostics.span(span_name, extra_field="value"):
            await asyncio.sleep(0.01)

        # Assert
        assert diagnostics._span_counts[span_name] == 1
        assert diagnostics._span_errors[span_name] == 0
        assert len(diagnostics._durations_ms[span_name]) == 1
        assert diagnostics._durations_ms[span_name][0] > 0

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("cleanup_test_files")
    async def test_span_records_failed_operation(
        self, diagnostics: DaemonDiagnostics
    ) -> None:
        """Span records failed operation and re-raises exception."""
        # Arrange
        span_name = "test_span"
        test_error = ValueError("test error")

        # Act & Assert
        with pytest.raises(ValueError, match="test error"):
            async with diagnostics.span(span_name):
                raise test_error

        # Assert
        assert diagnostics._span_counts[span_name] == 1
        assert diagnostics._span_errors[span_name] == 1
        assert len(diagnostics._durations_ms[span_name]) == 1

    @pytest.mark.asyncio
    async def test_span_noop_when_disabled(
        self, disabled_diagnostics: DaemonDiagnostics
    ) -> None:
        """Span does nothing when diagnostics are disabled."""
        # Arrange
        span_name = "test_span"

        # Act
        async with disabled_diagnostics.span(span_name):
            await asyncio.sleep(0.01)

        # Assert
        assert disabled_diagnostics._span_counts[span_name] == 0
        assert len(disabled_diagnostics._durations_ms[span_name]) == 0

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("cleanup_test_files")
    async def test_span_captures_fields(self, diagnostics: DaemonDiagnostics) -> None:
        """Span captures additional fields in event."""
        # Arrange
        span_name = "test_span"

        # Act
        async with diagnostics.span(span_name, station_id="s1", cycle_id="c1"):
            await asyncio.sleep(0.01)

        # Assert
        assert len(diagnostics._recent_events) == 1
        event = diagnostics._recent_events[0]
        assert event["kind"] == "span"
        assert event["name"] == span_name
        assert event["station_id"] == "s1"
        assert event["cycle_id"] == "c1"
        assert event["ok"] is True


class TestDaemonDiagnosticsRecordSpan:
    """Tests for DaemonDiagnostics.record_span()."""

    @pytest.mark.usefixtures("cleanup_test_files")
    def test_record_span_increments_count(self, diagnostics: DaemonDiagnostics) -> None:
        """record_span increments span count."""
        # Arrange
        span_name = "test_span"

        # Act
        diagnostics.record_span(span_name, 10.5, ok=True)

        # Assert
        assert diagnostics._span_counts[span_name] == 1
        assert diagnostics._span_errors[span_name] == 0

    @pytest.mark.usefixtures("cleanup_test_files")
    def test_record_span_increments_error_count(
        self, diagnostics: DaemonDiagnostics
    ) -> None:
        """record_span increments error count when ok=False."""
        # Arrange
        span_name = "test_span"

        # Act
        diagnostics.record_span(span_name, 10.5, ok=False)

        # Assert
        assert diagnostics._span_counts[span_name] == 1
        assert diagnostics._span_errors[span_name] == 1

    @pytest.mark.usefixtures("cleanup_test_files")
    def test_record_span_stores_duration(self, diagnostics: DaemonDiagnostics) -> None:
        """record_span stores duration in deque."""
        # Arrange
        span_name = "test_span"

        # Act
        diagnostics.record_span(span_name, 25.7, ok=True)

        # Assert
        assert len(diagnostics._durations_ms[span_name]) == 1
        assert diagnostics._durations_ms[span_name][0] == 25.7

    @pytest.mark.usefixtures("cleanup_test_files")
    def test_record_span_creates_event(self, diagnostics: DaemonDiagnostics) -> None:
        """record_span creates event in recent_events."""
        # Arrange
        span_name = "test_span"

        # Act
        diagnostics.record_span(span_name, 15.3, ok=True, extra="value")

        # Assert
        assert len(diagnostics._recent_events) == 1
        event = diagnostics._recent_events[0]
        assert event["kind"] == "span"
        assert event["name"] == span_name
        assert event["elapsed_ms"] == 15.3
        assert event["ok"] is True
        assert event["extra"] == "value"

    def test_record_span_noop_when_disabled(
        self, disabled_diagnostics: DaemonDiagnostics
    ) -> None:
        """record_span does nothing when disabled."""
        # Arrange
        span_name = "test_span"

        # Act
        disabled_diagnostics.record_span(span_name, 10.0, ok=True)

        # Assert
        assert disabled_diagnostics._span_counts[span_name] == 0
        assert len(disabled_diagnostics._recent_events) == 0

    @pytest.mark.usefixtures("cleanup_test_files")
    def test_record_span_rounds_elapsed_ms(
        self, diagnostics: DaemonDiagnostics
    ) -> None:
        """record_span rounds elapsed_ms to 2 decimal places."""
        # Arrange
        span_name = "test_span"

        # Act
        diagnostics.record_span(span_name, 10.123456, ok=True)

        # Assert
        event = diagnostics._recent_events[0]
        assert event["elapsed_ms"] == 10.12


class TestDaemonDiagnosticsRecordError:
    """Tests for DaemonDiagnostics.record_error()."""

    @pytest.mark.usefixtures("cleanup_test_files")
    def test_record_error_increments_error_type(
        self, diagnostics: DaemonDiagnostics
    ) -> None:
        """record_error increments error type counter."""
        # Arrange
        error_name = "test_error"
        test_error = ValueError("test message")

        # Act
        diagnostics.record_error(error_name, test_error)

        # Assert
        assert diagnostics._error_types["ValueError"] == 1

    @pytest.mark.usefixtures("cleanup_test_files")
    def test_record_error_creates_event(self, diagnostics: DaemonDiagnostics) -> None:
        """record_error creates error event."""
        # Arrange
        error_name = "test_error"
        test_error = ValueError("test message")

        # Act
        diagnostics.record_error(error_name, test_error, station_id="s1")

        # Assert
        assert len(diagnostics._recent_events) == 1
        event = diagnostics._recent_events[0]
        assert event["kind"] == "error"
        assert event["name"] == error_name
        assert event["error_type"] == "ValueError"
        assert event["error_message"] == "test message"
        assert event["station_id"] == "s1"

    @pytest.mark.usefixtures("cleanup_test_files")
    def test_record_error_with_httpx_status_error(
        self, diagnostics: DaemonDiagnostics
    ) -> None:
        """record_error enriches httpx.HTTPStatusError with HTTP fields."""
        # Arrange
        error_name = "http_error"
        mock_request = MagicMock()
        mock_request.url = "https://api.example.com/endpoint"
        mock_request.method = "GET"
        mock_response = MagicMock()
        mock_response.status_code = 429
        status_error = httpx.HTTPStatusError(
            "Rate limited", request=mock_request, response=mock_response
        )

        # Act
        diagnostics.record_error(error_name, status_error)

        # Assert
        event = diagnostics._recent_events[0]
        assert event["http_status"] == 429
        assert event["http_url"] == "https://api.example.com/endpoint"
        assert event["http_method"] == "GET"

    @pytest.mark.usefixtures("cleanup_test_files")
    def test_record_error_with_httpx_request_error(
        self, diagnostics: DaemonDiagnostics
    ) -> None:
        """record_error enriches httpx.RequestError with HTTP fields."""
        # Arrange
        error_name = "request_error"
        mock_request = MagicMock()
        mock_request.url = "https://api.example.com/timeout"
        mock_request.method = "POST"
        request_error = httpx.RequestError("Connection timeout", request=mock_request)

        # Act
        diagnostics.record_error(error_name, request_error)

        # Assert
        event = diagnostics._recent_events[0]
        assert event["http_url"] == "https://api.example.com/timeout"
        assert event["http_method"] == "POST"
        assert "http_status" not in event

    def test_record_error_noop_when_disabled(
        self, disabled_diagnostics: DaemonDiagnostics
    ) -> None:
        """record_error does nothing when disabled."""
        # Arrange
        error_name = "test_error"
        test_error = ValueError("test message")

        # Act
        disabled_diagnostics.record_error(error_name, test_error)

        # Assert
        assert len(disabled_diagnostics._error_types) == 0
        assert len(disabled_diagnostics._recent_events) == 0

    @pytest.mark.usefixtures("cleanup_test_files")
    def test_record_error_handles_httpx_enrichment_failure(
        self,
        diagnostics: DaemonDiagnostics,
        mocker: MockerFixture,
    ) -> None:
        """record_error handles failures during httpx enrichment gracefully."""
        # Arrange
        error_name = "test_error"
        # Create an httpx error but make accessing response.status_code fail
        mock_request = MagicMock()
        mock_request.url = "https://api.example.com/endpoint"
        mock_request.method = "GET"
        mock_response = MagicMock()
        # Make status_code property raise an exception
        type(mock_response).status_code = mocker.PropertyMock(
            side_effect=Exception("access failed")
        )
        status_error = httpx.HTTPStatusError(
            "Rate limited", request=mock_request, response=mock_response
        )

        # Act & Assert - should not raise, enrichment is in suppress block
        diagnostics.record_error(error_name, status_error)

        # The error should still be recorded in error_types
        assert diagnostics._error_types["HTTPStatusError"] == 1
        # Event should still be created (without http fields due to suppression)
        assert len(diagnostics._recent_events) == 1
        event = diagnostics._recent_events[0]
        # Should not have http_status field due to suppression
        assert "http_status" not in event


class TestDaemonDiagnosticsRecordEvent:
    """Tests for DaemonDiagnostics.record_event()."""

    @pytest.mark.usefixtures("cleanup_test_files")
    def test_record_event_creates_event(self, diagnostics: DaemonDiagnostics) -> None:
        """record_event creates custom event."""
        # Arrange
        event_kind = "custom_event"

        # Act
        diagnostics.record_event(event_kind, field1="value1", field2=42)

        # Assert
        assert len(diagnostics._recent_events) == 1
        event = diagnostics._recent_events[0]
        assert event["kind"] == event_kind
        assert event["field1"] == "value1"
        assert event["field2"] == 42

    def test_record_event_noop_when_disabled(
        self, disabled_diagnostics: DaemonDiagnostics
    ) -> None:
        """record_event does nothing when disabled."""
        # Arrange
        event_kind = "custom_event"

        # Act
        disabled_diagnostics.record_event(event_kind, field="value")

        # Assert
        assert len(disabled_diagnostics._recent_events) == 0


class TestDaemonDiagnosticsMaybeLogSummary:
    """Tests for DaemonDiagnostics.maybe_log_summary()."""

    @pytest.mark.usefixtures("cleanup_test_files")
    def test_maybe_log_summary_logs_when_forced(
        self,
        diagnostics: DaemonDiagnostics,
        mock_logger: MagicMock,
    ) -> None:
        """maybe_log_summary logs when force=True."""
        # Arrange
        diagnostics.record_span("test_span", 10.0, ok=True)

        # Act
        diagnostics.maybe_log_summary(force=True)

        # Assert
        mock_logger.info.assert_called_once()
        call_kwargs = mock_logger.info.call_args[1]
        assert "Daemon diagnostics summary" in str(mock_logger.info.call_args[0])
        extra_fields = call_kwargs["extra"]["extra_fields"]
        assert extra_fields["daemon_class"] == "TestDaemon"
        assert "span_counts" in extra_fields

    @pytest.mark.usefixtures("cleanup_test_files")
    def test_maybe_log_summary_respects_interval(
        self,
        diagnostics: DaemonDiagnostics,
        mock_logger: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """maybe_log_summary respects summary_interval_seconds."""
        # Arrange
        diagnostics.record_span("test_span", 10.0, ok=True)
        # Use a list to track calls and return values
        time_values = [0.0, 30.0, 91.0]  # 91.0 is > 60 seconds after 30.0
        call_index = [0]

        def mock_monotonic() -> float:
            idx = call_index[0]
            call_index[0] += 1
            return time_values[idx] if idx < len(time_values) else time_values[-1]

        mocker.patch(
            "cta_eta.data_collection.orchestration.diagnostics.time.monotonic",
            side_effect=mock_monotonic,
        )

        # Act
        diagnostics.maybe_log_summary(force=False)  # Logs, sets _last_summary_at to 0.0
        mock_logger.info.reset_mock()
        # Manually set _last_summary_at to simulate first call
        diagnostics._last_summary_at = 0.0
        diagnostics.maybe_log_summary(
            force=False
        )  # Should not log (30.0 - 0.0 = 30.0 < 60.0)
        diagnostics._last_summary_at = 30.0
        diagnostics.maybe_log_summary(
            force=False
        )  # Should log (91.0 - 30.0 = 61.0 > 60.0)

        # Assert
        assert mock_logger.info.call_count == 1

    @pytest.mark.usefixtures("cleanup_test_files")
    def test_maybe_log_summary_includes_duration_percentiles(
        self,
        diagnostics: DaemonDiagnostics,
        mock_logger: MagicMock,
    ) -> None:
        """maybe_log_summary includes duration percentiles."""
        # Arrange
        span_name = "test_span"
        # Add multiple durations for percentile calculation
        for duration in [10.0, 20.0, 30.0, 40.0, 50.0]:
            diagnostics.record_span(span_name, duration, ok=True)

        # Act
        diagnostics.maybe_log_summary(force=True)

        # Assert
        call_kwargs = mock_logger.info.call_args[1]
        extra_fields = call_kwargs["extra"]["extra_fields"]
        assert "duration_ms" in extra_fields
        duration_summary = extra_fields["duration_ms"]
        assert span_name in duration_summary
        assert "p50_ms" in duration_summary[span_name]
        assert "p95_ms" in duration_summary[span_name]

    def test_maybe_log_summary_noop_when_disabled(
        self, disabled_diagnostics: DaemonDiagnostics, mock_logger: MagicMock
    ) -> None:
        """maybe_log_summary does nothing when disabled."""
        # Act
        disabled_diagnostics.maybe_log_summary(force=True)

        # Assert
        mock_logger.info.assert_not_called()

    @pytest.mark.usefixtures("cleanup_test_files")
    def test_maybe_log_summary_includes_all_counters(
        self,
        diagnostics: DaemonDiagnostics,
        mock_logger: MagicMock,
    ) -> None:
        """maybe_log_summary includes all counter types."""
        # Arrange
        diagnostics.record_span("span1", 10.0, ok=True)
        diagnostics.record_span("span2", 20.0, ok=False)
        diagnostics.record_error("error1", ValueError("test"))

        # Act
        diagnostics.maybe_log_summary(force=True)

        # Assert
        call_kwargs = mock_logger.info.call_args[1]
        extra_fields = call_kwargs["extra"]["extra_fields"]
        assert "span_counts" in extra_fields
        assert "span_errors" in extra_fields
        assert "error_types" in extra_fields
        assert extra_fields["span_counts"]["span1"] == 1
        assert extra_fields["span_errors"]["span2"] == 1
        assert extra_fields["error_types"]["ValueError"] == 1


class TestDaemonDiagnosticsSnapshot:
    """Tests for DaemonDiagnostics.snapshot()."""

    @pytest.mark.usefixtures("cleanup_test_files")
    def test_snapshot_returns_complete_state(
        self, diagnostics: DaemonDiagnostics
    ) -> None:
        """Snapshot returns complete diagnostic state."""
        # Arrange
        diagnostics.record_span("span1", 10.0, ok=True)
        diagnostics.record_span("span2", 20.0, ok=False)
        diagnostics.record_error("error1", ValueError("test"))

        # Act
        snapshot = diagnostics.snapshot()

        # Assert
        assert snapshot["daemon_class"] == "TestDaemon"
        assert snapshot["diag_run_id"] == diagnostics.run_id
        assert snapshot["span_counts"]["span1"] == 1
        assert snapshot["span_errors"]["span2"] == 1
        assert snapshot["error_types"]["ValueError"] == 1
        assert len(snapshot["recent_events"]) == 3

    def test_snapshot_returns_empty_state_when_no_activity(
        self, diagnostics: DaemonDiagnostics
    ) -> None:
        """Snapshot returns empty state when no activity."""
        # Act
        snapshot = diagnostics.snapshot()

        # Assert
        assert snapshot["span_counts"] == {}
        assert snapshot["span_errors"] == {}
        assert snapshot["error_types"] == {}
        assert snapshot["recent_events"] == []


class TestDaemonDiagnosticsWriteEventJsonl:
    """Tests for DaemonDiagnostics._write_event_jsonl_best_effort()."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_write_event_jsonl_creates_file(
        self, diagnostics: DaemonDiagnostics
    ) -> None:
        """_write_event_jsonl_best_effort creates event log file."""
        # Arrange
        event = {"kind": "test", "field": "value"}

        # Act
        diagnostics._write_event_jsonl_best_effort(event)

        # Assert
        log_path = Path("test.events.jsonl")
        assert log_path.exists()
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        loaded = json.loads(lines[0])
        assert loaded["kind"] == "test"
        assert loaded["field"] == "value"

        # Clean up
        if log_path.exists():
            log_path.unlink()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_write_event_jsonl_appends_to_file(
        self, diagnostics: DaemonDiagnostics
    ) -> None:
        """_write_event_jsonl_best_effort appends events to file."""
        # Arrange
        event1 = {"kind": "event1"}
        event2 = {"kind": "event2"}

        # Act
        diagnostics._write_event_jsonl_best_effort(event1)
        diagnostics._write_event_jsonl_best_effort(event2)

        # Assert
        log_path = Path("test.events.jsonl")
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["kind"] == "event1"
        assert json.loads(lines[1])["kind"] == "event2"

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_write_event_jsonl_creates_parent_directories(
        self, diagnostics: DaemonDiagnostics
    ) -> None:
        """_write_event_jsonl_best_effort creates parent directories."""
        # Arrange
        config = DaemonDiagnosticsConfig(
            enabled=True, event_log_path="subdir/nested/test.events.jsonl"
        )
        diag = DaemonDiagnostics(
            logger=diagnostics._logger, daemon_name="TestDaemon", config=config
        )
        event = {"kind": "test"}

        # Act
        diag._write_event_jsonl_best_effort(event)

        # Assert
        log_path = Path("subdir/nested/test.events.jsonl")
        assert log_path.exists()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_write_event_jsonl_handles_io_error_gracefully(
        self, diagnostics: DaemonDiagnostics, mocker: MockerFixture
    ) -> None:
        """_write_event_jsonl_best_effort handles I/O errors gracefully."""
        # Arrange
        event = {"kind": "test"}
        mocker.patch(
            "cta_eta.data_collection.orchestration.diagnostics.Path.mkdir",
            side_effect=OSError("Permission denied"),
        )

        # Act - should not raise
        diagnostics._write_event_jsonl_best_effort(event)

        # Assert - should log debug message
        diagnostics._logger.debug.assert_called()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_write_event_jsonl_noop_when_disabled(
        self, disabled_diagnostics: DaemonDiagnostics
    ) -> None:
        """_write_event_jsonl_best_effort does nothing when disabled."""
        # Arrange
        event = {"kind": "test"}

        # Act
        disabled_diagnostics._write_event_jsonl_best_effort(event)

        # Assert
        assert not Path("test.events.jsonl").exists()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_write_event_jsonl_noop_when_no_path(self, mock_logger: MagicMock) -> None:
        """_write_event_jsonl_best_effort does nothing when event_log_path is None."""
        # Arrange
        config = DaemonDiagnosticsConfig(enabled=True, event_log_path=None)
        diag = DaemonDiagnostics(
            logger=mock_logger, daemon_name="TestDaemon", config=config
        )
        event = {"kind": "test"}

        # Act
        diag._write_event_jsonl_best_effort(event)

        # Assert - no file should be created
        assert not any(Path.cwd().glob("*.jsonl"))


class TestDaemonDiagnosticsRecentEventsBounded:
    """Tests for bounded memory in recent events."""

    @pytest.mark.usefixtures("cleanup_test_files")
    def test_recent_events_bounded_by_maxlen(
        self, diagnostics: DaemonDiagnostics
    ) -> None:
        """recent_events deque is bounded by max_recent_events."""
        # Arrange
        max_events = diagnostics._config.max_recent_events

        # Act - add more events than max
        for i in range(max_events + 10):
            diagnostics.record_event("test", index=i)

        # Assert
        assert len(diagnostics._recent_events) == max_events
        # Should have the last max_events events
        first_event = diagnostics._recent_events[0]
        assert first_event["index"] == 10  # First 10 were dropped
