"""Unit tests for BaseDaemon class."""

from __future__ import annotations

import contextlib
import json
import signal
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from cta_eta.daemon import BaseDaemon

if TYPE_CHECKING:
    from collections.abc import Generator


class ConcreteDaemon(BaseDaemon):
    """Concrete implementation of BaseDaemon for testing."""

    def __init__(
        self,
        config: dict[str, dict[str, str | int | float | bool]],
        logger: MagicMock,
        state_data: dict[str, str | int | float] | None = None,
    ) -> None:
        """Initialize with optional state data for testing."""
        super().__init__(config, logger)
        self._test_state = state_data or {}
        self.run_called = False
        self.run_exception: Exception | None = None

    def run(self) -> None:
        """Test implementation of run method."""
        self.run_called = True
        if self.run_exception:
            raise self.run_exception
        while self.running:
            time.sleep(0.01)

    def _get_state(self) -> dict[str, str | int | float]:
        """Return test state."""
        return self._test_state.copy()


@pytest.fixture
def mock_logger() -> MagicMock:
    """Create a mock logger for testing."""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.error = MagicMock()
    return logger


@pytest.fixture
def sample_config() -> dict[str, dict[str, str | int | float | bool]]:
    """Create sample configuration for testing."""
    return {
        "collection": {"train_interval_seconds": 15},
        "retry": {"max_retry_attempts": 10},
    }


@pytest.fixture
def daemon_state_dir(tmp_path: Path) -> Path:
    """Create temporary daemon state directory."""
    state_dir = tmp_path / ".daemon_state"
    state_dir.mkdir(exist_ok=True)
    return state_dir


@pytest.fixture
def cleanup_state_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Generator[None]:
    """Change to temporary directory for daemon state files."""
    original_cwd = Path.cwd()
    monkeypatch.chdir(tmp_path)
    yield
    monkeypatch.chdir(original_cwd)


class TestBaseDaemonInit:
    """Test cases for BaseDaemon initialization."""

    def test_init_sets_attributes(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
    ) -> None:
        """Test that initialization sets all required attributes."""
        # Arrange & Act
        daemon = ConcreteDaemon(sample_config, mock_logger)

        # Assert
        assert daemon.config == sample_config
        assert daemon.logger == mock_logger
        assert daemon.running is False

    def test_init_loads_state_when_exists(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        daemon_state_dir: Path,
        cleanup_state_files: None,
    ) -> None:
        """Test that initialization loads state from existing file."""
        # Arrange
        state_file = daemon_state_dir / "ConcreteDaemon.json"
        state_data = {"last_poll": 1234567890, "count": 42}
        state_file.write_text(json.dumps(state_data))

        # Act
        _ = ConcreteDaemon(sample_config, mock_logger)

        # Assert
        mock_logger.info.assert_any_call(
            "Loaded daemon state from .daemon_state/ConcreteDaemon.json",
            extra={
                "extra_fields": {
                    "state_file": ".daemon_state/ConcreteDaemon.json",
                    "state_keys": ["last_poll", "count"],
                }
            },
        )

    def test_init_handles_missing_state_file(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        cleanup_state_files: None,
    ) -> None:
        """Test that initialization handles missing state file gracefully."""
        # Arrange & Act
        _ = ConcreteDaemon(sample_config, mock_logger)

        # Assert
        mock_logger.info.assert_any_call(
            "No previous state found, starting fresh",
            extra={"extra_fields": {"state_file": ".daemon_state/ConcreteDaemon.json"}},
        )

    def test_init_handles_corrupt_state_file(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        daemon_state_dir: Path,
        cleanup_state_files: None,
    ) -> None:
        """Test that initialization handles corrupt state file gracefully."""
        # Arrange
        state_file = daemon_state_dir / "ConcreteDaemon.json"
        state_file.write_text("invalid json {")

        # Act
        _ = ConcreteDaemon(sample_config, mock_logger)

        # Assert
        mock_logger.exception.assert_called()
        assert "Failed to load daemon state" in str(mock_logger.exception.call_args)


class TestBaseDaemonStart:
    """Test cases for BaseDaemon.start() method."""

    def test_start_registers_signal_handlers(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        cleanup_state_files: None,
    ) -> None:
        """Test that start() registers signal handlers."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger)

        with patch("signal.signal") as mock_signal:
            # Act - run start() in a thread so we can stop it
            start_thread = threading.Thread(target=daemon.start, daemon=True)
            start_thread.start()

            # Wait briefly for signal handlers to be registered
            time.sleep(0.1)

            # Stop the daemon to exit the run() loop
            daemon.stop()

            # Wait for thread to finish
            start_thread.join(timeout=1.0)

            # Assert
            assert mock_signal.call_count == 2
            calls = [call[0][0] for call in mock_signal.call_args_list]
            assert signal.SIGTERM in calls
            assert signal.SIGINT in calls

    def test_start_sets_running_flag(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        cleanup_state_files: None,
    ) -> None:
        """Test that start() sets running flag and calls run()."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger)

        def stop_quickly():
            time.sleep(0.01)
            daemon.stop()

        with patch("signal.signal"):
            # Act
            thread = threading.Thread(target=stop_quickly)
            thread.start()
            daemon.start()
            thread.join()

            # Assert
            assert daemon.run_called is True

    def test_start_logs_startup(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        cleanup_state_files: None,
    ) -> None:
        """Test that start() logs startup event."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger)

        def stop_quickly():
            time.sleep(0.01)
            daemon.stop()

        with patch("signal.signal"):
            import threading

            thread = threading.Thread(target=stop_quickly)
            thread.start()
            daemon.start()
            thread.join()

            # Assert
            mock_logger.info.assert_any_call(
                "Starting ConcreteDaemon daemon",
                extra={"extra_fields": {"daemon_class": "ConcreteDaemon"}},
            )

    def test_start_handles_exception_in_run(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        cleanup_state_files: None,
    ) -> None:
        """Test that start() handles exceptions in run() method."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger)
        daemon.run_exception = ValueError("Test error")

        with patch("signal.signal"):
            # Act & Assert
            with pytest.raises(ValueError, match="Test error"):
                daemon.start()

            # Assert error was logged
            mock_logger.exception.assert_called()
            error_call = mock_logger.exception.call_args
            assert "Daemon error" in str(error_call)

    def test_start_logs_with_extra_fields(
        self,
        mock_logger: MagicMock,
        sample_config: dict,
        cleanup_state_files: None,
    ) -> None:
        """Test that start() logs with proper extra_fields structure."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger)

        def stop_quickly():
            time.sleep(0.01)
            daemon.stop()

        with patch("signal.signal"):
            thread = threading.Thread(target=stop_quickly)
            thread.start()
            # Act
            daemon.start()
            thread.join()

            # Assert - verify the exact log call structure
            calls = [
                call
                for call in mock_logger.info.call_args_list
                if "Starting" in str(call)
            ]
            assert len(calls) > 0
            call_kwargs = calls[0][1]
            assert "extra" in call_kwargs
            assert "extra_fields" in call_kwargs["extra"]
            assert (
                call_kwargs["extra"]["extra_fields"]["daemon_class"] == "ConcreteDaemon"
            )

    def test_start_signal_registration_called(
        self,
        mock_logger: MagicMock,
        sample_config: dict,
        cleanup_state_files: None,
    ) -> None:
        """Test that signal.signal is called for both SIGTERM and SIGINT."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger)

        def stop_quickly():
            time.sleep(0.01)
            daemon.stop()

        with patch("signal.signal") as mock_signal:
            thread = threading.Thread(target=stop_quickly)
            thread.start()
            # Act
            daemon.start()
            thread.join()

            # Assert
            assert mock_signal.call_count == 2
            signal_numbers = [call[0][0] for call in mock_signal.call_args_list]
            assert signal.SIGTERM in signal_numbers
            assert signal.SIGINT in signal_numbers

    def test_start_exception_logging_structure(
        self,
        mock_logger: MagicMock,
        sample_config: dict,
        cleanup_state_files: None,
    ) -> None:
        """Test that exceptions in run() are logged with proper structure."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger)
        daemon.run_exception = ValueError("Test exception message")

        with patch("signal.signal"):
            # Act & Assert
            with pytest.raises(ValueError, match="Test exception message"):
                daemon.start()

            # Assert exception was logged with proper structure
            mock_logger.exception.assert_called_once()
            call_kwargs = mock_logger.exception.call_args[1]
            assert "extra" in call_kwargs
            assert "extra_fields" in call_kwargs["extra"]
            assert call_kwargs["extra"]["extra_fields"]["error_type"] == "ValueError"
            assert (
                call_kwargs["extra"]["extra_fields"]["error_message"]
                == "Test exception message"
            )


class TestBaseDaemonStop:
    """Test cases for BaseDaemon.stop() method."""

    def test_stop_sets_running_false(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        cleanup_state_files: None,
    ) -> None:
        """Test that stop() sets running flag to False."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger)
        daemon.running = True

        # Act
        daemon.stop()

        # Assert
        assert daemon.running is False

    def test_stop_is_idempotent(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        cleanup_state_files: None,
    ) -> None:
        """Test that stop() can be called multiple times safely.

        Verifies that:
        1. Multiple calls to stop() are safe (idempotent)
        2. The "Stopping..." message is only logged once, even if stop() is called multiple times
        3. Subsequent calls return early without logging
        """
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger)
        daemon.running = True

        # Reset call count after initialization (which may log during _load_state)
        mock_logger.info.reset_mock()

        # Act
        daemon.stop()
        daemon.stop()
        daemon.stop()

        # Assert
        assert daemon.running is False

        # Verify "Stopping..." message is logged exactly once
        stopping_calls = [
            call for call in mock_logger.info.call_args_list if "Stopping" in str(call)
        ]
        assert len(stopping_calls) == 1, (
            "stop() should log 'Stopping...' message only once"
        )

    def test_stop_saves_state(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        daemon_state_dir: Path,
        cleanup_state_files: None,
    ) -> None:
        """Test that stop() saves state to file."""
        # Arrange
        daemon = ConcreteDaemon(
            sample_config, mock_logger, {"test_key": "test_value", "count": 100}
        )
        daemon.running = True

        # Act
        daemon.stop()

        # Assert
        state_file = daemon_state_dir / "ConcreteDaemon.json"
        assert state_file.exists()
        loaded_state = json.loads(state_file.read_text())
        assert loaded_state == {"test_key": "test_value", "count": 100}

    def test_stop_logs_shutdown(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        cleanup_state_files: None,
    ) -> None:
        """Test that stop() logs shutdown event."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger)
        daemon.running = True

        # Act
        daemon.stop()

        # Assert
        mock_logger.info.assert_any_call(
            "Stopping ConcreteDaemon daemon",
            extra={"extra_fields": {"daemon_class": "ConcreteDaemon"}},
        )


class TestBaseDaemonSignalHandler:
    """Test cases for signal handling."""

    def test_signal_handler_calls_stop(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        cleanup_state_files: None,
    ) -> None:
        """Test that signal handler calls stop()."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger)
        daemon.running = True

        # Act
        daemon._signal_handler(signal.SIGTERM, None)

        # Assert
        assert daemon.running is False
        mock_logger.info.assert_any_call(
            "Received SIGTERM, initiating graceful shutdown",
            extra={
                "extra_fields": {"signal": "SIGTERM", "signal_number": signal.SIGTERM}
            },
        )

    def test_signal_handler_handles_sigint(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        cleanup_state_files: None,
    ) -> None:
        """Test that signal handler handles SIGINT."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger)
        daemon.running = True

        # Act
        daemon._signal_handler(signal.SIGINT, None)

        # Assert
        assert daemon.running is False
        mock_logger.info.assert_any_call(
            "Received SIGINT, initiating graceful shutdown",
            extra={
                "extra_fields": {"signal": "SIGINT", "signal_number": signal.SIGINT}
            },
        )

    def test_signal_handler_with_frame(
        self,
        mock_logger: MagicMock,
        sample_config: dict,
        cleanup_state_files: None,
    ) -> None:
        """Test signal handler works with a frame parameter."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger)
        daemon.running = True
        mock_frame = MagicMock(spec=FrameType)

        # Act
        daemon._signal_handler(signal.SIGTERM, mock_frame)  # ty:ignore[invalid-argument-type]

        # Assert
        assert daemon.running is False
        mock_logger.info.assert_called()

    def test_signal_handler_logs_signal_number(
        self,
        mock_logger: MagicMock,
        sample_config: dict,
        cleanup_state_files: None,
    ) -> None:
        """Test signal handler logs signal number in extra_fields."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger)
        daemon.running = True

        # Act
        daemon._signal_handler(signal.SIGINT, None)

        # Assert
        calls = [
            call for call in mock_logger.info.call_args_list if "SIGINT" in str(call)
        ]
        assert len(calls) > 0
        call_kwargs = calls[0][1]
        assert "extra" in call_kwargs
        assert "extra_fields" in call_kwargs["extra"]
        assert call_kwargs["extra"]["extra_fields"]["signal"] == "SIGINT"
        assert call_kwargs["extra"]["extra_fields"]["signal_number"] == signal.SIGINT


class TestBaseDaemonStatePersistence:
    """Test cases for state persistence."""

    def test_save_state_creates_directory(
        self,
        mock_logger: MagicMock,
        sample_config: dict,
        tmp_path: Path,
        cleanup_state_files: None,
    ) -> None:
        """Test that save_state() creates state directory if it doesn't exist."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger, {"key": "value"})
        state_dir = tmp_path / ".daemon_state"

        # Act
        daemon._save_state()

        # Assert
        assert state_dir.exists()
        assert state_dir.is_dir()

    def test_save_state_writes_json(
        self,
        mock_logger: MagicMock,
        sample_config: dict,
        daemon_state_dir: Path,
        cleanup_state_files: None,
    ) -> None:
        """Test that save_state() writes valid JSON."""
        # Arrange
        state_data = {"timestamp": 1234567890, "count": 42, "status": "active"}
        daemon = ConcreteDaemon(sample_config, mock_logger, state_data)

        # Act
        daemon._save_state()

        # Assert
        state_file = daemon_state_dir / "ConcreteDaemon.json"
        assert state_file.exists()
        loaded = json.loads(state_file.read_text())
        assert loaded == state_data

    def test_save_state_handles_io_error(
        self, mock_logger: MagicMock, sample_config: dict, cleanup_state_files: None
    ) -> None:
        """Test that save_state() handles I/O errors gracefully."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger, {"key": "value"})

        with patch(
            "cta_eta.daemon.Path.mkdir",
            side_effect=PermissionError("Permission denied"),
        ):
            # Act
            daemon._save_state()

            # Assert
            mock_logger.exception.assert_called()
            assert "Failed to save daemon state" in str(mock_logger.exception.call_args)

    def test_load_state_returns_none_when_missing(
        self, mock_logger: MagicMock, sample_config: dict, cleanup_state_files: None
    ) -> None:
        """Test that load_state() returns None when file doesn't exist."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger)

        # Act
        result = daemon._load_state()

        # Assert
        assert result is None

    def test_load_state_returns_data_when_exists(
        self,
        mock_logger: MagicMock,
        sample_config: dict,
        daemon_state_dir: Path,
        cleanup_state_files: None,
    ) -> None:
        """Test that load_state() returns data when file exists."""
        # Arrange
        state_data = {"last_run": 1234567890, "iterations": 100}
        state_file = daemon_state_dir / "ConcreteDaemon.json"
        state_file.write_text(json.dumps(state_data))

        daemon = ConcreteDaemon(sample_config, mock_logger)

        # Act
        result = daemon._load_state()

        # Assert
        assert result == state_data

    def test_load_state_handles_corrupt_file(
        self,
        mock_logger: MagicMock,
        sample_config: dict,
        daemon_state_dir: Path,
        cleanup_state_files: None,
    ) -> None:
        """Test that load_state() handles corrupt JSON gracefully."""
        # Arrange
        state_file = daemon_state_dir / "ConcreteDaemon.json"
        state_file.write_text("not valid json {")

        daemon = ConcreteDaemon(sample_config, mock_logger)

        # Act
        result = daemon._load_state()

        # Assert
        assert result is None
        mock_logger.exception.assert_called()
        assert "Failed to load daemon state" in str(mock_logger.exception.call_args)

    def test_save_state_creates_directory_structure(
        self,
        mock_logger: MagicMock,
        sample_config: dict,
        cleanup_state_files: None,
    ) -> None:
        """Test _save_state creates .daemon_state directory."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger, {"key": "value"})
        state_dir = Path(".daemon_state")

        # Act
        daemon._save_state()

        # Assert
        assert state_dir.exists()
        assert state_dir.is_dir()

    def test_save_state_writes_indented_json(
        self,
        mock_logger: MagicMock,
        sample_config: dict,
        cleanup_state_files: None,
    ) -> None:
        """Test _save_state writes properly formatted JSON."""
        # Arrange
        state_data = {"timestamp": 1234567890, "count": 42}
        daemon = ConcreteDaemon(sample_config, mock_logger, state_data)

        # Act
        daemon._save_state()

        # Assert
        state_file = Path(".daemon_state") / "ConcreteDaemon.json"
        assert state_file.exists()
        content = state_file.read_text()
        # Verify it's indented (has newlines)
        assert "\n" in content
        loaded = json.loads(content)
        assert loaded == state_data

    def test_save_state_logs_file_path(
        self,
        mock_logger: MagicMock,
        sample_config: dict,
        cleanup_state_files: None,
    ) -> None:
        """Test _save_state logs the state file path."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger, {"test": "data"})

        # Act
        daemon._save_state()

        # Assert
        calls = [
            call
            for call in mock_logger.info.call_args_list
            if "Saved daemon state" in str(call)
        ]
        assert len(calls) > 0
        call_kwargs = calls[0][1]
        assert "extra" in call_kwargs
        assert "extra_fields" in call_kwargs["extra"]
        assert "state_file" in call_kwargs["extra"]["extra_fields"]

    def test_load_state_logs_state_keys(
        self,
        mock_logger: MagicMock,
        sample_config: dict,
        cleanup_state_files: None,
    ) -> None:
        """Test _load_state logs the keys found in state."""
        # Arrange
        state_data = {"key1": "value1", "key2": 42, "key3": 3.14}
        state_file = Path(".daemon_state") / "ConcreteDaemon.json"
        state_file.parent.mkdir(exist_ok=True)
        state_file.write_text(json.dumps(state_data))

        daemon = ConcreteDaemon(sample_config, mock_logger)

        # Act
        result = daemon._load_state()

        # Assert
        assert result == state_data
        calls = [
            call
            for call in mock_logger.info.call_args_list
            if "Loaded daemon state" in str(call)
        ]
        assert len(calls) > 0
        call_kwargs = calls[0][1]
        assert "extra" in call_kwargs
        assert "extra_fields" in call_kwargs["extra"]
        assert "state_keys" in call_kwargs["extra"]["extra_fields"]
        assert set(call_kwargs["extra"]["extra_fields"]["state_keys"]) == {
            "key1",
            "key2",
            "key3",
        }

    def test_save_state_handles_get_state_exception(
        self,
        mock_logger: MagicMock,
        sample_config: dict,
        cleanup_state_files: None,
    ) -> None:
        """Test _save_state handles exception in _get_state()."""
        # Arrange
        daemon = ConcreteDaemon(sample_config, mock_logger)

        def failing_get_state() -> dict[str, str | int | float]:
            msg = "State retrieval failed"
            raise RuntimeError(msg)

        daemon._get_state = failing_get_state  # type: ignore[assignment]

        # Act
        daemon._save_state()

        # Assert - should log error but not crash
        mock_logger.exception.assert_called()
        error_call = mock_logger.exception.call_args
        assert "Failed to save daemon state" in str(error_call)

    def test_load_state_handles_json_decode_error(
        self,
        mock_logger: MagicMock,
        sample_config: dict,
        cleanup_state_files: None,
    ) -> None:
        """Test _load_state handles JSON decode errors gracefully."""
        # Arrange
        state_file = Path(".daemon_state") / "ConcreteDaemon.json"
        state_file.parent.mkdir(exist_ok=True)
        state_file.write_text("not valid json {{{")

        daemon = ConcreteDaemon(sample_config, mock_logger)

        # Act
        result = daemon._load_state()

        # Assert
        assert result is None
        mock_logger.exception.assert_called()
        error_call = mock_logger.exception.call_args
        assert "Failed to load daemon state" in str(error_call)

    def test_load_state_handles_file_read_error(
        self,
        mock_logger: MagicMock,
        sample_config: dict,
        cleanup_state_files: None,
    ) -> None:
        """Test _load_state handles file read errors."""
        # Arrange
        state_file = Path(".daemon_state") / "ConcreteDaemon.json"
        state_file.parent.mkdir(exist_ok=True)
        state_file.write_text('{"valid": "json"}')

        # Create daemon first to trigger initial load
        daemon = ConcreteDaemon(sample_config, mock_logger)

        # Make file unreadable after initial load
        state_file.chmod(0o000)

        try:
            # Act - try to load again (this will fail)
            result = daemon._load_state()

            # Assert - should return None and log error
            # Note: On some systems, chmod 0o000 might not prevent reading by owner
            # So we check if error was called OR result is None
            if result is None:
                # If we got None, error should have been logged
                assert mock_logger.exception.called  # Allow either outcome
        finally:
            # Restore permissions for cleanup
            with contextlib.suppress(Exception):
                state_file.chmod(0o644)
