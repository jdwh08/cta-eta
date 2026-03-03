"""Unit tests for AsyncBaseDaemon class."""

from __future__ import annotations

import asyncio
import inspect
import json
import signal
from pathlib import Path
from typing import TYPE_CHECKING, override
from unittest.mock import MagicMock

import pytest
from cta_eta.data_collection.orchestration.daemon import AsyncBaseDaemon

from cta_eta.data_collection.exceptions import DaemonNotStartedError

if TYPE_CHECKING:
    from collections.abc import Generator

    from pytest_mock import MockerFixture

EXPECTED_SIGNAL_HANDLER_COUNT = 2  # SIGTERM and SIGINT


@pytest.fixture
def mock_logger() -> MagicMock:
    """Create a mock logger for testing."""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.error = MagicMock()
    logger.exception = MagicMock()
    return logger


@pytest.fixture
def sample_config() -> dict[str, dict[str, str | int | float | bool]]:
    """Create sample configuration for testing."""
    return {
        "collection": {"train_interval_seconds": 15},
        "retry": {"max_retry_attempts": 10},
        "diagnostics": {"summary_interval_seconds": 10},
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


class ConcreteAsyncDaemon(AsyncBaseDaemon):
    """Concrete implementation of AsyncBaseDaemon for testing."""

    @override
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
        self.saw_shutdown_set_on_start = False

    @override
    async def run(self) -> None:
        """Run once and stop to keep tests fast."""
        self.run_called = True
        if self.run_exception:
            raise self.run_exception

        shutdown = self._shutdown
        self.saw_shutdown_set_on_start = bool(shutdown and shutdown.is_set())

        # End quickly so `start()` doesn't block unit tests.
        self.stop()

    @override
    def _get_state(self) -> dict[str, str | int | float]:
        """Return test state for persistence checks."""
        return self._test_state.copy()

    @override
    def _apply_state(self, state: dict[str, str | int | float]) -> None:
        """Apply state to daemon instance."""
        self._test_state = state.copy()
        self.logger.info(
            "Applied daemon state from previous run",
            extra={"extra_fields": {"state_keys": list(state.keys())}},
        )


class TestAsyncBaseDaemonInit:
    """Test cases for AsyncBaseDaemon initialization."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_init_handles_missing_state_file(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
    ) -> None:
        """Initialization logs that no prior state exists."""
        # Arrange & Act
        _ = ConcreteAsyncDaemon(sample_config, mock_logger)

        # Assert
        mock_logger.info.assert_any_call(
            "No previous state found, starting fresh",
            extra={
                "extra_fields": {"state_file": ".daemon_state/ConcreteAsyncDaemon.json"}
            },
        )

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_init_loads_state_when_exists(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        daemon_state_dir: Path,
    ) -> None:
        """Initialization loads persisted JSON state when present."""
        # Arrange
        state_file = daemon_state_dir / "ConcreteAsyncDaemon.json"
        state_data = {"last_poll": 1234567890, "count": 42}
        state_file.write_text(json.dumps(state_data))

        # Act
        _ = ConcreteAsyncDaemon(sample_config, mock_logger)

        # Assert
        mock_logger.info.assert_any_call(
            "Loaded daemon state from .daemon_state/ConcreteAsyncDaemon.json",
            extra={
                "extra_fields": {
                    "state_file": ".daemon_state/ConcreteAsyncDaemon.json",
                    "state_keys": ["last_poll", "count"],
                }
            },
        )

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_init_handles_corrupt_state_file(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        daemon_state_dir: Path,
    ) -> None:
        """Initialization handles corrupt JSON state gracefully."""
        # Arrange
        state_file = daemon_state_dir / "ConcreteAsyncDaemon.json"
        state_file.write_text("invalid json {")

        # Act
        _ = ConcreteAsyncDaemon(sample_config, mock_logger)

        # Assert
        mock_logger.exception.assert_called()
        assert "Failed to load daemon state" in str(mock_logger.exception.call_args)


class TestAsyncBaseDaemonStart:
    """Test cases for AsyncBaseDaemon.start() method."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_start_calls_run(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """start() invokes run() and returns cleanly."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        mocker.patch.object(daemon, "_register_signal_handlers", autospec=True)

        # Act
        daemon.start()

        # Assert
        assert daemon.run_called is True

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_start_logs_startup(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """start() logs startup with structured extra_fields."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        mocker.patch.object(daemon, "_register_signal_handlers", autospec=True)

        # Act
        daemon.start()

        # Assert
        mock_logger.info.assert_any_call(
            "Starting ConcreteAsyncDaemon daemon",
            extra={"extra_fields": {"daemon_class": "ConcreteAsyncDaemon"}},
        )

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_start_handles_exception_in_run(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """start() logs and re-raises exceptions from run()."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        daemon.run_exception = ValueError("Test error")
        mocker.patch.object(daemon, "_register_signal_handlers", autospec=True)

        # Act & Assert
        with pytest.raises(ValueError, match="Test error"):
            daemon.start()

        # Assert
        mock_logger.exception.assert_called_once()
        call_kwargs = mock_logger.exception.call_args[1]
        assert call_kwargs["extra"]["extra_fields"]["error_type"] == "ValueError"
        assert call_kwargs["extra"]["extra_fields"]["error_message"] == "Test error"


class TestAsyncBaseDaemonRunMain:
    """Test cases for AsyncBaseDaemon._run_main()."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_run_main_sets_running_true_then_false(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """_run_main() sets `running` during run() and clears it on exit."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        mocker.patch.object(daemon, "_register_signal_handlers", autospec=True)

        # Act
        asyncio.run(daemon._run_main())

        # Assert
        assert daemon.run_called is True
        assert daemon.running is False
        assert daemon._loop is not None
        assert daemon._shutdown is not None

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_run_main_honors_pre_start_stop_request(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """If stop() is called before start, shutdown event is set on entry."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        daemon.stop()
        mocker.patch.object(daemon, "_register_signal_handlers", autospec=True)

        # Act
        asyncio.run(daemon._run_main())

        # Assert
        assert daemon.run_called is True
        assert daemon.saw_shutdown_set_on_start is True


class TestAsyncBaseDaemonStop:
    """Test cases for AsyncBaseDaemon.stop() method."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_stop_saves_state(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        daemon_state_dir: Path,
        mocker: MockerFixture,
    ) -> None:
        """stop() persists state to `.daemon_state/<Class>.json`."""
        # Arrange
        daemon = ConcreteAsyncDaemon(
            sample_config, mock_logger, {"test_key": "test_value", "count": 100}
        )
        mocker.patch.object(daemon, "_register_signal_handlers", autospec=True)

        # Act
        daemon.start()

        # Assert
        state_file = daemon_state_dir / "ConcreteAsyncDaemon.json"
        assert state_file.exists()
        loaded_state = json.loads(state_file.read_text())
        assert loaded_state == {"test_key": "test_value", "count": 100}

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_stop_is_idempotent(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
    ) -> None:
        """stop() can be called multiple times and logs shutdown once."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        daemon.running = True

        mock_logger.info.reset_mock()

        # Act
        daemon.stop()
        daemon.stop()
        daemon.stop()

        # Assert
        stopping_calls = [
            call for call in mock_logger.info.call_args_list if "Stopping" in str(call)
        ]
        assert len(stopping_calls) == 1

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_stop_before_start_requests_shutdown_only(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """stop() before start() should not attempt state persistence."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        save_state = mocker.patch.object(daemon, "_save_state", autospec=True)

        # Act
        daemon.stop()

        # Assert
        assert daemon._shutdown_requested is True
        save_state.assert_not_called()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_stop_wakes_sleep_via_shutdown_event(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """stop() triggers shutdown Event via loop, enabling interruptible sleep()."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        daemon.running = True
        daemon._shutdown = asyncio.Event()
        daemon._loop = MagicMock()
        daemon._loop.is_running.return_value = True
        daemon._loop.call_soon_threadsafe = MagicMock()
        mocker.patch.object(daemon, "_save_state", autospec=True)

        # Act
        daemon.stop()

        # Assert
        daemon._loop.call_soon_threadsafe.assert_called_once()


class TestAsyncBaseDaemonSignalHandler:
    """Test cases for AsyncBaseDaemon signal handling."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_signal_handler_calls_stop(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """Signal handler logs signal details and stops the daemon."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        daemon.running = True
        mocker.patch.object(daemon, "_save_state", autospec=True)

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


class TestAsyncBaseDaemonSleep:
    """Test cases for AsyncBaseDaemon.sleep()."""

    def test_sleep_returns_immediately_for_non_positive_seconds(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """sleep() should be a no-op for zero/negative durations."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        wait_for = mocker.patch(
            "cta_eta.data_collection.orchestration.daemon.asyncio.wait_for",
            autospec=True,
        )

        # Act
        asyncio.run(daemon.sleep(0))
        asyncio.run(daemon.sleep(-1))

        # Assert
        wait_for.assert_not_called()

    def test_sleep_raises_before_start(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
    ) -> None:
        """sleep() raises if used before the daemon is started."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)

        # Act & Assert
        with pytest.raises(DaemonNotStartedError):
            asyncio.run(daemon.sleep(0.1))

    def test_sleep_returns_on_timeout(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """sleep() should return cleanly on timeout (no shutdown requested)."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        daemon._shutdown = asyncio.Event()

        def _timeout_without_leaking_coroutine(aw, *, timeout: float) -> None:  # noqa: ANN001
            _ = timeout
            if inspect.iscoroutine(aw):
                aw.close()
            raise TimeoutError

        mocker.patch(
            "cta_eta.data_collection.orchestration.daemon.asyncio.wait_for",
            side_effect=_timeout_without_leaking_coroutine,
            autospec=True,
        )

        # Act
        asyncio.run(daemon.sleep(123.0))

        # Assert
        assert True


class TestAsyncBaseDaemonSignalRegistration:
    """Test cases for AsyncBaseDaemon._register_signal_handlers()."""

    def test_register_signal_handlers_uses_loop_when_supported(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
    ) -> None:
        """_register_signal_handlers uses loop.add_signal_handler when supported."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        daemon._loop = MagicMock()
        daemon._loop.add_signal_handler = MagicMock()

        # Act
        daemon._register_signal_handlers()

        # Assert
        assert (
            daemon._loop.add_signal_handler.call_count == EXPECTED_SIGNAL_HANDLER_COUNT
        )

    def test_register_signal_handlers_falls_back_to_signal_signal(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """_register_signal_handlers falls back to signal.signal when needed."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        daemon._loop = MagicMock()
        daemon._loop.add_signal_handler.side_effect = NotImplementedError
        mock_signal = mocker.patch(
            "cta_eta.data_collection.orchestration.daemon.signal.signal",
            autospec=True,
        )

        # Act
        daemon._register_signal_handlers()

        # Assert
        assert mock_signal.call_count == EXPECTED_SIGNAL_HANDLER_COUNT


class TestAsyncBaseDaemonStatePersistence:
    """Test cases for state persistence methods."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_save_state_writes_json(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        daemon_state_dir: Path,
    ) -> None:
        """_save_state writes valid JSON to the state file."""
        # Arrange
        state_data = {"timestamp": 1234567890, "count": 42, "status": "active"}
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger, state_data)

        # Act
        daemon._save_state()

        # Assert
        state_file = daemon_state_dir / "ConcreteAsyncDaemon.json"
        assert state_file.exists()
        loaded = json.loads(state_file.read_text())
        assert loaded == state_data

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_save_state_handles_io_error(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """_save_state logs exceptions (e.g., permission errors) and does not raise."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger, {"key": "value"})
        mocker.patch(
            "cta_eta.data_collection.orchestration.daemon.Path.mkdir",
            side_effect=PermissionError("Permission denied"),
            autospec=True,
        )

        # Act
        daemon._save_state()

        # Assert
        mock_logger.exception.assert_called()
        assert "Failed to save daemon state" in str(mock_logger.exception.call_args)


class TestAsyncBaseDaemonDiagnostics:
    """Test cases for diagnostics integration."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_init_creates_diagnostics_instance(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
    ) -> None:
        """Initialization creates a DaemonDiagnostics instance."""
        # Arrange & Act
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)

        # Assert
        assert hasattr(daemon, "diagnostics")
        assert daemon.diagnostics is not None
        assert daemon._diagnostics_interval_s == 10.0

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_diagnostics_task_created_when_enabled(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """Diagnostics task is created when diagnostics are enabled."""
        # Arrange
        config_with_diagnostics = {
            **sample_config,
            "diagnostics": {
                "enabled": True,
                "summary_interval_seconds": 5.0,
            },
        }
        daemon = ConcreteAsyncDaemon(config_with_diagnostics, mock_logger)
        mocker.patch.object(daemon, "_register_signal_handlers", autospec=True)

        # Act
        asyncio.run(daemon._run_main())

        # Assert
        assert daemon._diagnostics_task is not None
        assert daemon._diagnostics_task.done()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_diagnostics_task_not_created_when_disabled(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """Diagnostics task is not created when diagnostics are disabled."""
        # Arrange
        config_without_diagnostics = {
            **sample_config,
            "diagnostics": {
                "enabled": False,
                "summary_interval_seconds": 5.0,
            },
        }
        daemon = ConcreteAsyncDaemon(config_without_diagnostics, mock_logger)
        mocker.patch.object(daemon, "_register_signal_handlers", autospec=True)

        # Act
        asyncio.run(daemon._run_main())

        # Assert
        assert daemon._diagnostics_task is None

    @pytest.mark.usefixtures("cleanup_state_files")
    @pytest.mark.asyncio
    async def test_run_diagnostics_loop_executes_periodically(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """Diagnostics loop calls maybe_log_summary periodically."""
        # Arrange
        config_with_diagnostics = {
            **sample_config,
            "diagnostics": {
                "enabled": True,
                "summary_interval_seconds": 0.1,  # Fast interval for testing
            },
        }
        daemon = ConcreteAsyncDaemon(config_with_diagnostics, mock_logger)
        daemon._loop = asyncio.get_running_loop()
        daemon._shutdown = asyncio.Event()
        daemon.running = True

        maybe_log_summary = mocker.patch.object(
            daemon.diagnostics, "maybe_log_summary", autospec=True
        )

        # Act
        diagnostics_task = asyncio.create_task(daemon._run_diagnostics_loop())
        await asyncio.sleep(0.15)  # Wait for at least one interval
        daemon.running = False
        daemon._shutdown.set()
        await diagnostics_task

        # Assert
        assert maybe_log_summary.call_count >= 1

    @pytest.mark.usefixtures("cleanup_state_files")
    @pytest.mark.asyncio
    async def test_run_diagnostics_loop_handles_no_shutdown(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
    ) -> None:
        """Diagnostics loop returns early if shutdown is None."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        daemon._shutdown = None

        # Act
        await daemon._run_diagnostics_loop()

        # Assert
        # Should return without error

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_save_diagnostics_snapshot_success(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        daemon_state_dir: Path,
    ) -> None:
        """_save_diagnostics_snapshot writes diagnostics snapshot to file."""
        # Arrange
        config_with_diagnostics = {
            **sample_config,
            "diagnostics": {
                "enabled": True,
                "summary_interval_seconds": 5.0,
            },
        }
        daemon = ConcreteAsyncDaemon(config_with_diagnostics, mock_logger)

        # Act
        daemon._save_diagnostics_snapshot()

        # Assert
        snapshot_file = daemon_state_dir / "ConcreteAsyncDaemon.diagnostics.json"
        assert snapshot_file.exists()
        snapshot_data = json.loads(snapshot_file.read_text())
        assert "daemon_class" in snapshot_data
        assert "diag_run_id" in snapshot_data
        assert snapshot_data["daemon_class"] == "ConcreteAsyncDaemon"

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_save_diagnostics_snapshot_handles_io_error(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """_save_diagnostics_snapshot logs exceptions and does not raise."""
        # Arrange
        config_with_diagnostics = {
            **sample_config,
            "diagnostics": {
                "enabled": True,
                "summary_interval_seconds": 5.0,
            },
        }
        daemon = ConcreteAsyncDaemon(config_with_diagnostics, mock_logger)
        mocker.patch(
            "cta_eta.data_collection.orchestration.daemon.Path.mkdir",
            side_effect=PermissionError("Permission denied"),
            autospec=True,
        )

        # Act
        daemon._save_diagnostics_snapshot()

        # Assert
        mock_logger.exception.assert_called()
        assert "Failed to save diagnostics snapshot" in str(
            mock_logger.exception.call_args
        )

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_stop_calls_diagnostics_summary_when_enabled(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """stop() calls diagnostics summary and snapshot when diagnostics enabled."""
        # Arrange
        config_with_diagnostics = {
            **sample_config,
            "diagnostics": {
                "enabled": True,
                "summary_interval_seconds": 5.0,
            },
        }
        daemon = ConcreteAsyncDaemon(config_with_diagnostics, mock_logger)
        daemon.running = True
        maybe_log_summary = mocker.patch.object(
            daemon.diagnostics, "maybe_log_summary", autospec=True
        )
        save_snapshot = mocker.patch.object(
            daemon, "_save_diagnostics_snapshot", autospec=True
        )
        mocker.patch.object(daemon, "_save_state", autospec=True)

        # Act
        daemon.stop()

        # Assert
        maybe_log_summary.assert_called_once_with(force=True)
        save_snapshot.assert_called_once()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_stop_skips_diagnostics_when_disabled(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """stop() skips diagnostics when diagnostics are disabled."""
        # Arrange
        config_without_diagnostics = {
            **sample_config,
            "diagnostics": {
                "enabled": False,
                "summary_interval_seconds": 5.0,
            },
        }
        daemon = ConcreteAsyncDaemon(config_without_diagnostics, mock_logger)
        daemon.running = True
        maybe_log_summary = mocker.patch.object(
            daemon.diagnostics, "maybe_log_summary", autospec=True
        )
        save_snapshot = mocker.patch.object(
            daemon, "_save_diagnostics_snapshot", autospec=True
        )
        mocker.patch.object(daemon, "_save_state", autospec=True)

        # Act
        daemon.stop()

        # Assert
        maybe_log_summary.assert_not_called()
        save_snapshot.assert_not_called()


class TestAsyncBaseDaemonPreShutdownHook:
    """Test cases for pre-shutdown hook."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_pre_shutdown_hook_flushes_storage_when_available(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
    ) -> None:
        """_pre_shutdown_hook flushes storage when storage attribute exists."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        mock_storage = MagicMock()
        mock_storage.flush = MagicMock()
        daemon.storage = mock_storage

        # Act
        daemon._pre_shutdown_hook()

        # Assert
        mock_storage.flush.assert_called_once()
        mock_logger.debug.assert_called_once_with("Flushed storage during shutdown")

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_pre_shutdown_hook_handles_missing_storage(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
    ) -> None:
        """_pre_shutdown_hook handles missing storage gracefully."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        # No storage attribute set

        # Act
        daemon._pre_shutdown_hook()

        # Assert
        mock_logger.warning.assert_called_once_with("No storage available to flush")

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_pre_shutdown_hook_handles_storage_without_flush(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
    ) -> None:
        """_pre_shutdown_hook handles storage without flush method."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        mock_storage = MagicMock()
        del mock_storage.flush  # Remove flush method
        daemon.storage = mock_storage

        # Act
        daemon._pre_shutdown_hook()

        # Assert
        mock_logger.warning.assert_called_once_with("Storage has no flush method")

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_pre_shutdown_hook_handles_flush_exception(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
    ) -> None:
        """_pre_shutdown_hook handles exceptions during storage flush."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        mock_storage = MagicMock()
        mock_storage.flush.side_effect = OSError("Flush failed")
        daemon.storage = mock_storage

        # Act
        daemon._pre_shutdown_hook()

        # Assert
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert "Failed to flush storage" in str(call_args)
        assert call_args[1]["extra"]["extra_fields"]["error_type"] == "OSError"

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_stop_calls_pre_shutdown_hook(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """stop() calls _pre_shutdown_hook before saving state."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        daemon.running = True
        pre_shutdown_hook = mocker.patch.object(
            daemon, "_pre_shutdown_hook", autospec=True
        )
        save_state = mocker.patch.object(daemon, "_save_state", autospec=True)
        mocker.patch.object(daemon, "_save_diagnostics_snapshot", autospec=True)

        # Act
        daemon.stop()

        # Assert
        pre_shutdown_hook.assert_called_once()
        # Verify hook is called before state save
        call_order = [str(call) for call in mock_logger.info.call_args_list]
        assert any("Stopping" in call for call in call_order)

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_stop_handles_pre_shutdown_hook_exception(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """stop() handles exceptions in _pre_shutdown_hook gracefully."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        daemon.running = True
        mocker.patch.object(
            daemon,
            "_pre_shutdown_hook",
            side_effect=RuntimeError("Hook failed"),
            autospec=True,
        )
        save_state = mocker.patch.object(daemon, "_save_state", autospec=True)
        mocker.patch.object(daemon, "_save_diagnostics_snapshot", autospec=True)

        # Act
        daemon.stop()

        # Assert
        mock_logger.exception.assert_called()
        call_args = mock_logger.exception.call_args
        assert "Error in pre-shutdown hook" in str(call_args)
        assert call_args[1]["extra"]["extra_fields"]["error_type"] == "RuntimeError"
        # State should still be saved despite hook failure
        save_state.assert_called_once()


class TestAsyncBaseDaemonRunMainEdgeCases:
    """Test cases for edge cases in _run_main()."""

    @pytest.mark.usefixtures("cleanup_state_files")
    @pytest.mark.asyncio
    async def test_run_main_cancels_run_task_on_shutdown(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """_run_main cancels run task when shutdown is requested."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        mocker.patch.object(daemon, "_register_signal_handlers", autospec=True)

        async def long_running_run() -> None:
            """Simulate a long-running run that would block shutdown."""
            daemon.run_called = True
            while daemon.running:
                await asyncio.sleep(0.1)

        original_run = daemon.run
        daemon.run = long_running_run

        # Act
        # Start _run_main in a task
        main_task = asyncio.create_task(daemon._run_main())
        # Wait a bit for it to start
        await asyncio.sleep(0.05)
        # Request shutdown
        daemon.stop()
        # Wait for completion
        await main_task

        # Assert
        assert daemon.running is False
        assert daemon.run_called is True

    @pytest.mark.usefixtures("cleanup_state_files")
    @pytest.mark.asyncio
    async def test_run_main_cancels_diagnostics_task_on_shutdown(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """_run_main cancels diagnostics task during shutdown."""
        # Arrange
        config_with_diagnostics = {
            **sample_config,
            "diagnostics": {
                "enabled": True,
                "summary_interval_seconds": 5.0,
            },
        }
        daemon = ConcreteAsyncDaemon(config_with_diagnostics, mock_logger)
        mocker.patch.object(daemon, "_register_signal_handlers", autospec=True)

        # Act
        await daemon._run_main()

        # Assert
        # Diagnostics task should be cancelled and done
        if daemon._diagnostics_task is not None:
            assert daemon._diagnostics_task.done()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_register_signal_handlers_when_loop_is_none(
        self,
        mock_logger: MagicMock,
        sample_config: dict[str, dict[str, str | int | float | bool]],
        mocker: MockerFixture,
    ) -> None:
        """_register_signal_handlers uses signal.signal when loop is None."""
        # Arrange
        daemon = ConcreteAsyncDaemon(sample_config, mock_logger)
        daemon._loop = None
        mock_signal = mocker.patch(
            "cta_eta.data_collection.orchestration.daemon.signal.signal",
            autospec=True,
        )

        # Act
        daemon._register_signal_handlers()

        # Assert
        assert mock_signal.call_count == EXPECTED_SIGNAL_HANDLER_COUNT
