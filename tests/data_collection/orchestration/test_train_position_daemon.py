"""Unit tests for TrainPositionDaemon orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import httpx
import pytest

from cta_eta.data_collection.exceptions import CTATrackerAPIError
from cta_eta.data_collection.orchestration.train_position_daemon import (
    TrainPositionDaemon,
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
    logger.exception = MagicMock()
    return logger


@pytest.fixture
def sample_config() -> dict[
    str, dict[str, str | int | float | bool | dict[str, str | int | float]]
]:
    """Minimal config for TrainPositionDaemon (deps mocked)."""
    return {
        "collection": {"train_poll_interval_seconds": 15},
        "diagnostics": {"summary_interval_seconds": 10, "enabled": False},
        "rate_limits": {"cta": {"max_per_second": 0.1, "max_at_once": 3}},
    }


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
def train_daemon(
    mocker: MockerFixture,
    sample_config: dict[
        str, dict[str, str | int | float | bool | dict[str, str | int | float]]
    ],
    mock_logger: MagicMock,
) -> tuple[TrainPositionDaemon, MagicMock]:
    """Create a TrainPositionDaemon with external deps mocked."""
    storage = MagicMock()
    mocker.patch(
        "cta_eta.data_collection.orchestration.train_position_daemon.create_parquet_writer",
        return_value=storage,
        autospec=True,
    )
    mocker.patch(
        "cta_eta.data_collection.orchestration.train_position_daemon.get_config_section",
        return_value={"max_per_second": 0.1, "max_at_once": 3},
        autospec=True,
    )
    daemon = TrainPositionDaemon(mock_logger, config=sample_config)
    return (daemon, storage)


@pytest.fixture
def mock_http_client() -> MagicMock:
    """Mock httpx.AsyncClient for run() tests."""
    client = MagicMock(spec=httpx.AsyncClient)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


class TestTrainPositionDaemonInit:
    """Tests for TrainPositionDaemon.__init__."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_init_loads_storage_and_rate_limits(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """__init__ creates storage and loads CTA rate limits from config."""
        # Arrange & Act
        daemon, _ = train_daemon
        get_section = mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_config_section",
            return_value={"max_per_second": 0.5, "max_at_once": 2},
        )

        # Rebuild daemon so we see get_config_section call
        create_writer = mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.create_parquet_writer",
            return_value=MagicMock(),
        )
        daemon2 = TrainPositionDaemon(daemon.logger, daemon.config)

        # Assert
        create_writer.assert_called_once()
        get_section.assert_called_with("rate_limits.cta")
        assert daemon2.storage is not None
        assert daemon2.cta_max_per_second == 0.5
        assert daemon2.cta_max_at_once == 2

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_init_uses_default_train_poll_interval_when_missing(
        self,
        mocker: MockerFixture,
        mock_logger: MagicMock,
    ) -> None:
        """__init__ uses 15 when train_poll_interval_seconds missing from config."""
        # Arrange
        config = {
            "collection": {},
            "diagnostics": {"summary_interval_seconds": 10, "enabled": False},
            "rate_limits": {"cta": {"max_per_second": 0.1, "max_at_once": 3}},
        }
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.create_parquet_writer",
            return_value=MagicMock(),
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_config_section",
            return_value={"max_per_second": 0.1, "max_at_once": 3},
            autospec=True,
        )

        # Act
        daemon = TrainPositionDaemon(mock_logger, config)

        # Assert
        assert daemon.train_poll_interval == 15

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_init_uses_config_train_poll_interval(
        self,
        mocker: MockerFixture,
        mock_logger: MagicMock,
    ) -> None:
        """__init__ uses train_poll_interval_seconds from config when present."""
        # Arrange
        config = {
            "collection": {"train_poll_interval_seconds": 30},
            "diagnostics": {"summary_interval_seconds": 10, "enabled": False},
            "rate_limits": {"cta": {"max_per_second": 0.1, "max_at_once": 3}},
        }
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.create_parquet_writer",
            return_value=MagicMock(),
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_config_section",
            return_value={"max_per_second": 0.1, "max_at_once": 3},
            autospec=True,
        )

        # Act
        daemon = TrainPositionDaemon(mock_logger, config)

        # Assert
        assert daemon.train_poll_interval == 30

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_init_initializes_state_tracking(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
    ) -> None:
        """__init__ sets last_poll_timestamp, total_records_collected, current_poll_count to 0."""
        # Arrange & Act
        daemon, _ = train_daemon

        # Assert
        assert daemon.last_poll_timestamp == 0.0
        assert daemon.total_records_collected == 0
        assert daemon.current_poll_count == 0


class TestTrainPositionDaemonCollectCycle:
    """Tests for TrainPositionDaemon._collect_train_positions_cycle."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_cycle_fetches_normalizes_and_stores(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """Happy path: fetch -> normalize -> store with dataset_name=train_positions."""
        # Arrange
        daemon, storage = train_daemon
        raw = {"ctatt": {"tmst": "2026-01-25T12:00:00", "route": []}}
        records = [
            {
                "poll_timestamp": None,
                "api_timestamp": "2026-01-25T12:00:00",
                "route": "red",
            }
        ]
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_train_positions",
            new=mocker.AsyncMock(return_value=raw),
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.normalize_train_positions",
            return_value=records,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.time.time",
            side_effect=[1000.0, 1000.1, 1000.5],
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.aiometer.run_all",
            new=mocker.AsyncMock(side_effect=lambda _jobs, **_kw: [raw]),
        )
        mock_client = MagicMock(spec=httpx.AsyncClient)

        # Act
        asyncio.run(daemon._collect_train_positions_cycle(mock_client))

        # Assert
        storage.append_batch.assert_called_once_with(
            records, dataset_name="train_positions", metadata=None
        )
        assert daemon.last_poll_timestamp == 1000.1
        assert daemon.total_records_collected == 1
        assert daemon.current_poll_count == 1

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_cycle_aiometer_run_all_invoked_with_rate_limits(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """aiometer.run_all is called with cta_max_at_once and cta_max_per_second."""
        # Arrange
        daemon, _ = train_daemon
        daemon.cta_max_at_once = 5
        daemon.cta_max_per_second = 0.2
        raw = {"ctatt": {"tmst": "2026-01-25T12:00:00", "route": []}}
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_train_positions",
            new=mocker.AsyncMock(return_value=raw),
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.normalize_train_positions",
            return_value=[],
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.time.time",
            return_value=1000.0,
            autospec=True,
        )
        run_all = mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.aiometer.run_all",
            new=mocker.AsyncMock(return_value=[raw]),
        )
        mock_client = MagicMock(spec=httpx.AsyncClient)

        # Act
        asyncio.run(daemon._collect_train_positions_cycle(mock_client))

        # Assert
        run_all.assert_called_once()
        call_kw = run_all.call_args[1]
        assert call_kw["max_at_once"] == 5
        assert call_kw["max_per_second"] == 0.2

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_cycle_storage_failure_logs_and_does_not_update_state(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """On append_batch failure, logs exception and does not update state."""
        # Arrange
        daemon, storage = train_daemon
        raw = {"ctatt": {"tmst": "2026-01-25T12:00:00", "route": []}}
        records = [{"route": "red"}]
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_train_positions",
            new=mocker.AsyncMock(return_value=raw),
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.normalize_train_positions",
            return_value=records,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.time.time",
            return_value=1000.0,
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.aiometer.run_all",
            new=mocker.AsyncMock(return_value=[raw]),
        )
        storage.append_batch.side_effect = OSError("disk full")
        mock_client = MagicMock(spec=httpx.AsyncClient)
        before_ts = daemon.last_poll_timestamp
        before_total = daemon.total_records_collected
        before_count = daemon.current_poll_count

        # Act
        asyncio.run(daemon._collect_train_positions_cycle(mock_client))

        # Assert
        cast("MagicMock", daemon.logger).exception.assert_called_once()
        assert "Failed to store train position records" in str(
            cast("MagicMock", daemon.logger).exception.call_args
        )
        assert daemon.last_poll_timestamp == before_ts
        assert daemon.total_records_collected == before_total
        assert daemon.current_poll_count == before_count

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_cycle_api_failure_raises(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """When get_train_positions raises, cycle logs and re-raises."""
        # Arrange
        daemon, _ = train_daemon
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_train_positions",
            new=mocker.AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "500", request=MagicMock(), response=MagicMock()
                )
            ),
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.time.time",
            return_value=1000.0,
            autospec=True,
        )

        async def run_all_side_effect(
            jobs: list, *, max_at_once: int, max_per_second: float
        ) -> list:
            _ = (max_at_once, max_per_second)
            return [await j() for j in jobs]

        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.aiometer.run_all",
            new=mocker.AsyncMock(side_effect=run_all_side_effect),
        )
        mock_client = MagicMock(spec=httpx.AsyncClient)

        # Act & Assert
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(daemon._collect_train_positions_cycle(mock_client))

        cast("MagicMock", daemon.logger).exception.assert_called_once()
        assert "Error during train position collection cycle" in str(
            cast("MagicMock", daemon.logger).exception.call_args
        )

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_cycle_empty_records_stores_empty_batch(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """When normalize returns [], append_batch is still called with []."""
        # Arrange
        daemon, storage = train_daemon
        raw = {"ctatt": {"tmst": "2026-01-25T12:00:00", "route": []}}
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_train_positions",
            new=mocker.AsyncMock(return_value=raw),
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.normalize_train_positions",
            return_value=[],
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.time.time",
            return_value=1000.0,
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.aiometer.run_all",
            new=mocker.AsyncMock(return_value=[raw]),
        )
        mock_client = MagicMock(spec=httpx.AsyncClient)

        # Act
        asyncio.run(daemon._collect_train_positions_cycle(mock_client))

        # Assert
        storage.append_batch.assert_called_once_with(
            [], dataset_name="train_positions", metadata=None
        )
        assert daemon.total_records_collected == 0
        assert daemon.current_poll_count == 1

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_cycle_cancelled_error_propagates(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """asyncio.CancelledError in cycle is re-raised."""
        # Arrange
        daemon, _ = train_daemon
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_train_positions",
            new=mocker.AsyncMock(side_effect=asyncio.CancelledError()),
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.time.time",
            return_value=1000.0,
            autospec=True,
        )

        async def run_all_side_effect(
            jobs: list, *, max_at_once: int, max_per_second: float
        ) -> list:
            _ = (max_at_once, max_per_second)
            return [await j() for j in jobs]

        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.aiometer.run_all",
            new=mocker.AsyncMock(side_effect=run_all_side_effect),
        )
        mock_client = MagicMock(spec=httpx.AsyncClient)

        # Act & Assert
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(daemon._collect_train_positions_cycle(mock_client))

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_cycle_keyboard_interrupt_propagates(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """KeyboardInterrupt in cycle is re-raised."""
        # Arrange
        daemon, _ = train_daemon
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_train_positions",
            new=mocker.AsyncMock(side_effect=KeyboardInterrupt()),
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.time.time",
            return_value=1000.0,
            autospec=True,
        )

        async def run_all_side_effect(
            jobs: list, *, max_at_once: int, max_per_second: float
        ) -> list:
            _ = (max_at_once, max_per_second)
            return [await j() for j in jobs]

        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.aiometer.run_all",
            new=mocker.AsyncMock(side_effect=run_all_side_effect),
        )
        mock_client = MagicMock(spec=httpx.AsyncClient)

        # Act & Assert
        with pytest.raises(KeyboardInterrupt):
            asyncio.run(daemon._collect_train_positions_cycle(mock_client))


class TestTrainPositionDaemonGetState:
    """Tests for TrainPositionDaemon._get_state."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_get_state_returns_persisted_fields(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
    ) -> None:
        """_get_state returns last_poll_timestamp, total_records_collected, current_poll_count, train_poll_interval_seconds."""
        # Arrange
        daemon, _ = train_daemon
        daemon.last_poll_timestamp = 2000.0
        daemon.total_records_collected = 100
        daemon.current_poll_count = 5
        daemon.train_poll_interval = 15

        # Act
        state = daemon._get_state()

        # Assert
        assert state["last_poll_timestamp"] == 2000.0
        assert state["total_records_collected"] == 100
        assert state["current_poll_count"] == 5
        assert state["train_poll_interval_seconds"] == 15


class TestTrainPositionDaemonRunLoop:
    """Tests for TrainPositionDaemon.run()."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_run_creates_httpx_client_and_loops(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mock_http_client: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """run() creates AsyncClient once and calls _collect_train_positions_cycle with it."""
        # Arrange
        daemon, _ = train_daemon
        daemon.running = True
        cycle_calls: list[object] = []

        async def collect_side_effect(client: httpx.AsyncClient) -> None:
            cycle_calls.append(client)
            if len(cycle_calls) >= 2:
                daemon.running = False

        mocker.patch.object(
            daemon,
            "_collect_train_positions_cycle",
            side_effect=collect_side_effect,
            autospec=True,
        )

        async def sleep_side_effect(_: float) -> None:
            pass

        mocker.patch.object(
            daemon, "sleep", side_effect=sleep_side_effect, autospec=True
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.httpx.AsyncClient",
            return_value=mock_http_client,
        )

        # Act
        asyncio.run(daemon.run())

        # Assert
        assert len(cycle_calls) == 2
        assert cycle_calls[0] is mock_http_client

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_run_transient_error_calls_extended_retry(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mock_http_client: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """On TRANSIENT error, calls _retry_with_extended_backoff method."""
        # Arrange
        daemon, _ = train_daemon
        daemon.running = True
        mocker.patch.object(
            daemon,
            "_collect_train_positions_cycle",
            side_effect=httpx.TimeoutException("timeout", request=MagicMock()),
            autospec=True,
        )

        retry_called = [False]

        async def mock_retry(_client: httpx.AsyncClient, _error: Exception) -> bool:
            retry_called[0] = True
            return False  # Simulate exhausted retry

        mocker.patch.object(
            daemon,
            "_retry_with_extended_backoff",
            side_effect=mock_retry,
            autospec=True,
        )

        async def stop_after_sleep(_: float) -> None:
            daemon.running = False

        mocker.patch.object(
            daemon, "sleep", side_effect=stop_after_sleep, autospec=True
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.httpx.AsyncClient",
            return_value=mock_http_client,
        )

        # Act
        asyncio.run(daemon.run())

        # Assert
        assert retry_called[0]
        cast("MagicMock", daemon.logger).warning.assert_called()
        assert (
            "retry exhausted"
            in str(cast("MagicMock", daemon.logger).warning.call_args).lower()
        )

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_run_rate_limit_error_applies_backoff_and_continues(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mock_http_client: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """On RATE_LIMIT (429), logs warning, sleeps 2x interval, then continues."""
        # Arrange
        daemon, _ = train_daemon
        daemon.running = True
        daemon.train_poll_interval = 15
        resp = MagicMock()
        resp.status_code = httpx.codes.TOO_MANY_REQUESTS
        mocker.patch.object(
            daemon,
            "_collect_train_positions_cycle",
            side_effect=httpx.HTTPStatusError(
                "429", request=MagicMock(), response=resp
            ),
            autospec=True,
        )
        sleep_args: list[float] = []

        async def stop_after_two_sleeps(secs: float) -> None:
            sleep_args.append(secs)
            if len(sleep_args) >= 2:
                daemon.running = False

        mocker.patch.object(
            daemon, "sleep", side_effect=stop_after_two_sleeps, autospec=True
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.httpx.AsyncClient",
            return_value=mock_http_client,
        )

        # Act
        asyncio.run(daemon.run())

        # Assert
        cast("MagicMock", daemon.logger).warning.assert_called()
        assert "Rate limit" in str(cast("MagicMock", daemon.logger).warning.call_args)
        assert 30 in sleep_args  # 15 * 2

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_run_configuration_error_exits_and_raises(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mock_http_client: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """On CONFIGURATION error, sets running=False and re-raises."""
        # Arrange
        daemon, _ = train_daemon
        daemon.running = True
        mocker.patch.object(
            daemon,
            "_collect_train_positions_cycle",
            side_effect=ValueError("CTA_API_KEY must be set"),
            autospec=True,
        )
        mocker.patch.object(daemon, "sleep", autospec=True)
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.httpx.AsyncClient",
            return_value=mock_http_client,
        )

        # Act & Assert
        with pytest.raises(ValueError, match="CTA_API_KEY must be set"):
            asyncio.run(daemon.run())

        assert daemon.running is False
        cast("MagicMock", daemon.logger).exception.assert_called()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_run_unknown_error_logs_exception_and_continues(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mock_http_client: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """On UNKNOWN (e.g. RuntimeError), logs exception and continues to sleep."""
        # Arrange
        daemon, _ = train_daemon
        daemon.running = True
        mocker.patch.object(
            daemon,
            "_collect_train_positions_cycle",
            side_effect=RuntimeError("unexpected"),
            autospec=True,
        )

        async def stop_after_sleep(_: float) -> None:
            daemon.running = False

        mocker.patch.object(
            daemon, "sleep", side_effect=stop_after_sleep, autospec=True
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.httpx.AsyncClient",
            return_value=mock_http_client,
        )

        # Act
        asyncio.run(daemon.run())

        # Assert
        cast("MagicMock", daemon.logger).exception.assert_called()
        assert "Train position collection cycle failed" in str(
            cast("MagicMock", daemon.logger).exception.call_args
        )

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_run_cancelled_error_propagates(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mock_http_client: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """asyncio.CancelledError from cycle propagates out of run()."""
        # Arrange
        daemon, _ = train_daemon
        daemon.running = True
        mocker.patch.object(
            daemon,
            "_collect_train_positions_cycle",
            side_effect=asyncio.CancelledError(),
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.httpx.AsyncClient",
            return_value=mock_http_client,
        )

        # Act & Assert
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(daemon.run())

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_run_calls_sleep_after_cycle(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mock_http_client: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """run() sleeps before the next scheduled cycle."""
        # Arrange
        daemon, _ = train_daemon
        daemon.running = True
        daemon.train_poll_interval = 15
        mocker.patch.object(
            daemon,
            "_collect_train_positions_cycle",
            autospec=True,
        )
        sleep_args: list[float] = []

        async def stop_after_sleep(secs: float) -> None:
            sleep_args.append(secs)
            daemon.running = False

        mocker.patch.object(
            daemon, "sleep", side_effect=stop_after_sleep, autospec=True
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.time.monotonic",
            return_value=0.0,
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.httpx.AsyncClient",
            return_value=mock_http_client,
        )

        # Act
        asyncio.run(daemon.run())

        # Assert
        assert 15 in sleep_args


class TestTrainPositionDaemonStateApplication:
    """Tests for _apply_state and restart gap detection."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_apply_state_restores_daemon_state(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
    ) -> None:
        """_apply_state correctly restores last_poll_timestamp and counters from state."""
        # Arrange
        daemon, _ = train_daemon
        state = {
            "last_poll_timestamp": 1234567890.5,
            "total_records_collected": 42,
            "current_poll_count": 10,
        }

        # Act
        daemon._apply_state(state)

        # Assert
        assert daemon.last_poll_timestamp == 1234567890.5
        assert daemon.total_records_collected == 42
        assert daemon.current_poll_count == 10
        cast("MagicMock", daemon.logger).info.assert_called()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_apply_state_handles_empty_state(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
    ) -> None:
        """_apply_state does not change anything when state is empty."""
        # Arrange
        daemon, _ = train_daemon
        daemon.last_poll_timestamp = 999.0
        daemon.total_records_collected = 5
        daemon.current_poll_count = 2

        # Act
        daemon._apply_state({})

        # Assert - values unchanged since state was empty
        assert daemon.last_poll_timestamp == 999.0
        assert daemon.total_records_collected == 5
        assert daemon.current_poll_count == 2

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_check_restart_gap_no_gap_on_first_run(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
    ) -> None:
        """_check_restart_gap does not report gap when last_poll_timestamp is 0."""
        # Arrange
        daemon, _ = train_daemon
        daemon.last_poll_timestamp = 0.0

        # Act
        daemon._check_restart_gap()

        # Assert - should log first run, no gap detected
        cast("MagicMock", daemon.logger).info.assert_any_call(
            "First daemon run - no restart gap check needed"
        )
        assert daemon.pending_gap_metadata is None

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_check_restart_gap_detects_downtime_gap(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """_check_restart_gap detects and logs restart gap when downtime exceeds threshold."""
        # Arrange
        daemon, _ = train_daemon
        daemon.train_poll_interval = 15
        daemon.last_poll_timestamp = 1000.0
        current_time = 1000.0 + 60.0  # 60 seconds later, exceeds 2x15=30s threshold

        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.time.time",
            return_value=current_time,
        )

        # Act
        daemon._check_restart_gap()

        # Assert - gap detected and flagged
        assert daemon.pending_gap_metadata is not None
        assert daemon.pending_gap_metadata["is_gap"] is True
        assert daemon.pending_gap_metadata["gap_reason"] == "downtime"
        cast("MagicMock", daemon.logger).warning.assert_called()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_check_restart_gap_no_gap_within_threshold(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """_check_restart_gap does not report gap when restart is within threshold."""
        # Arrange
        daemon, _ = train_daemon
        daemon.train_poll_interval = 15
        daemon.last_poll_timestamp = 1000.0
        current_time = 1000.0 + 20.0  # 20 seconds, within 2x15=30s threshold

        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.time.time",
            return_value=current_time,
        )

        # Act
        daemon._check_restart_gap()

        # Assert - no gap
        assert daemon.pending_gap_metadata is None
        # Should log info about no gap
        cast("MagicMock", daemon.logger).info.assert_called()


class TestTrainPositionDaemonGapDetection:
    """Tests for gap detection during collection cycle."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_cycle_detects_gap_and_sets_pending_metadata(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """When gap is detected, pending_gap_metadata is set for next successful write."""
        # Arrange
        daemon, storage = train_daemon
        daemon.train_poll_interval = 15
        daemon.last_poll_timestamp = 1000.0  # Last poll was at 1000.0
        raw = {"ctatt": {"tmst": "2026-01-25T12:00:00", "route": []}}
        records = [{"route": "red"}]
        # Current time is 1000.0 + 60.0 = 1060.0, which exceeds 2*15=30s threshold
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.time.time",
            side_effect=[1000.0, 1060.0, 1060.1, 1060.5],
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_train_positions",
            new=mocker.AsyncMock(return_value=raw),
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.normalize_train_positions",
            return_value=records,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.aiometer.run_all",
            new=mocker.AsyncMock(return_value=[raw]),
        )
        mock_client = MagicMock(spec=httpx.AsyncClient)

        # Act
        asyncio.run(daemon._collect_train_positions_cycle(mock_client))

        # Assert - gap metadata was attached to storage call (then cleared after success)
        storage.append_batch.assert_called_once()
        call_kwargs = storage.append_batch.call_args[1]
        assert call_kwargs["metadata"] is not None
        assert call_kwargs["metadata"]["is_gap"] is True
        # After successful write, metadata should be cleared
        assert daemon.pending_gap_metadata is None

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_cycle_no_gap_when_within_threshold(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """When time delta is within threshold, no gap is detected."""
        # Arrange
        daemon, storage = train_daemon
        daemon.train_poll_interval = 15
        daemon.last_poll_timestamp = 1000.0
        raw = {"ctatt": {"tmst": "2026-01-25T12:00:00", "route": []}}
        records = [{"route": "red"}]
        # Current time is 1000.0 + 20.0 = 1020.0, which is within 2*15=30s threshold
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.time.time",
            side_effect=[1000.0, 1020.0, 1020.1, 1020.5],
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_train_positions",
            new=mocker.AsyncMock(return_value=raw),
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.normalize_train_positions",
            return_value=records,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.aiometer.run_all",
            new=mocker.AsyncMock(return_value=[raw]),
        )
        mock_client = MagicMock(spec=httpx.AsyncClient)

        # Act
        asyncio.run(daemon._collect_train_positions_cycle(mock_client))

        # Assert - no gap metadata set
        assert daemon.pending_gap_metadata is None
        storage.append_batch.assert_called_once()
        call_kwargs = storage.append_batch.call_args[1]
        assert call_kwargs["metadata"] is None

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_cycle_clears_pending_gap_metadata_after_successful_write(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """After successful storage, pending_gap_metadata is cleared."""
        # Arrange
        daemon, storage = train_daemon
        daemon.train_poll_interval = 15
        daemon.last_poll_timestamp = 1000.0
        # Set pending gap metadata from previous cycle
        daemon.pending_gap_metadata = {
            "is_gap": True,
            "gap_reason": "downtime",
            "gap_duration_seconds": 60.0,
        }
        raw = {"ctatt": {"tmst": "2026-01-25T12:00:00", "route": []}}
        records = [{"route": "red"}]
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.time.time",
            side_effect=[1000.0, 1000.1, 1000.5],
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_train_positions",
            new=mocker.AsyncMock(return_value=raw),
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.normalize_train_positions",
            return_value=records,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.aiometer.run_all",
            new=mocker.AsyncMock(return_value=[raw]),
        )
        mock_client = MagicMock(spec=httpx.AsyncClient)

        # Act
        asyncio.run(daemon._collect_train_positions_cycle(mock_client))

        # Assert - metadata was attached and then cleared
        storage.append_batch.assert_called_once()
        call_kwargs = storage.append_batch.call_args[1]
        assert call_kwargs["metadata"] == {
            "is_gap": True,
            "gap_reason": "downtime",
            "gap_duration_seconds": 60.0,
        }
        assert daemon.pending_gap_metadata is None
        # Verify gap metadata was logged (check any info call contains gap metadata)
        info_calls = [
            str(call) for call in cast("MagicMock", daemon.logger).info.call_args_list
        ]
        gap_logged = any("Gap metadata attached" in call for call in info_calls)
        assert gap_logged

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_cycle_preserves_pending_gap_metadata_on_storage_failure(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """If storage fails, pending_gap_metadata is preserved for next attempt."""
        # Arrange
        daemon, storage = train_daemon
        daemon.train_poll_interval = 15
        daemon.last_poll_timestamp = 1000.0
        gap_metadata = {
            "is_gap": True,
            "gap_reason": "downtime",
            "gap_duration_seconds": 60.0,
        }
        daemon.pending_gap_metadata = gap_metadata
        raw = {"ctatt": {"tmst": "2026-01-25T12:00:00", "route": []}}
        records = [{"route": "red"}]
        storage.append_batch.side_effect = OSError("disk full")
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.time.time",
            return_value=1000.0,
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_train_positions",
            new=mocker.AsyncMock(return_value=raw),
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.normalize_train_positions",
            return_value=records,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.aiometer.run_all",
            new=mocker.AsyncMock(return_value=[raw]),
        )
        mock_client = MagicMock(spec=httpx.AsyncClient)

        # Act
        asyncio.run(daemon._collect_train_positions_cycle(mock_client))

        # Assert - metadata preserved
        assert daemon.pending_gap_metadata == gap_metadata


class TestTrainPositionDaemonRetry:
    """Tests for _retry_with_extended_backoff."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_retry_with_extended_backoff_succeeds_on_first_attempt(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """Retry succeeds on first attempt and returns True."""
        # Arrange
        daemon, _ = train_daemon
        mock_client = MagicMock(spec=httpx.AsyncClient)
        original_error = httpx.TimeoutException("timeout", request=MagicMock())

        mocker.patch.object(
            daemon,
            "_collect_train_positions_cycle",
            autospec=True,
        )

        # Act
        result = asyncio.run(
            daemon._retry_with_extended_backoff(
                mock_client, original_error, max_attempts=3, max_wait=0.1
            )
        )

        # Assert
        assert result is True
        cast("MagicMock", daemon.logger).info.assert_any_call(
            "Retry succeeded on attempt 1",
            extra={"extra_fields": {"retry_attempt": 1}},
        )

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_retry_with_extended_backoff_exhausts_after_all_attempts(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """Retry exhausts all attempts and returns False."""
        # Arrange
        daemon, _ = train_daemon
        mock_client = MagicMock(spec=httpx.AsyncClient)
        original_error = httpx.TimeoutException("timeout", request=MagicMock())

        # Make cycle always fail with transient error
        cycle_mock = mocker.patch.object(
            daemon,
            "_collect_train_positions_cycle",
            side_effect=httpx.TimeoutException("timeout", request=MagicMock()),
            autospec=True,
        )

        # Act
        result = asyncio.run(
            daemon._retry_with_extended_backoff(
                mock_client, original_error, max_attempts=3, max_wait=0.1
            )
        )

        # Assert
        assert result is False
        # Should have attempted multiple retries (stamina retries 10 times)
        assert cycle_mock.call_count == 3

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_retry_with_extended_backoff_stops_on_category_change_to_configuration(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """Retry detects category change from TRANSIENT to CONFIGURATION and logs warning."""
        # Arrange
        daemon, _ = train_daemon
        mock_client = MagicMock(spec=httpx.AsyncClient)
        original_error = httpx.TimeoutException("timeout", request=MagicMock())

        # First attempt fails with transient, second with configuration
        config_error = ValueError("CTA_API_KEY must be set")
        cycle_mock = mocker.patch.object(
            daemon,
            "_collect_train_positions_cycle",
            side_effect=[
                httpx.TimeoutException("timeout", request=MagicMock()),
                config_error,
            ],
            autospec=True,
        )

        # Act & Assert
        asyncio.run(
            daemon._retry_with_extended_backoff(
                mock_client, original_error, max_attempts=3, max_wait=0.1
            )
        )

        # Assert - verify warning was logged about category change
        warning_calls = [
            str(call)
            for call in cast("MagicMock", daemon.logger).warning.call_args_list
        ]
        category_change_logged = any(
            "Error category changed" in call for call in warning_calls
        )
        # Verify cycle was called at least twice (first transient, then config)
        assert cycle_mock.call_count >= 2
        assert category_change_logged

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_retry_with_extended_backoff_stops_on_category_change_to_daily_quota(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """Retry stops if error category changes from TRANSIENT to DAILY_QUOTA."""
        # Arrange

        daemon, _ = train_daemon
        mock_client = MagicMock(spec=httpx.AsyncClient)
        original_error = httpx.TimeoutException("timeout", request=MagicMock())

        daemon.probe_102_attempts = 1

        # First attempt fails with transient, second with daily quota
        quota_error = CTATrackerAPIError("102", "Daily quota exceeded")
        cycle_mock = mocker.patch.object(
            daemon,
            "_collect_train_positions_cycle",
            side_effect=[
                httpx.TimeoutException("timeout", request=MagicMock()),
                quota_error,
            ],
            autospec=True,
        )

        handle_daily_quota_error_mock = mocker.patch.object(
            daemon,
            "_handle_daily_quota_error",
            autospec=True,
        )

        # Act
        # Note: stamina's retry behavior may vary, but we verify the cycle is called
        with contextlib.suppress(CTATrackerAPIError, httpx.TimeoutException):
            asyncio.run(
                daemon._retry_with_extended_backoff(
                    mock_client, original_error, max_attempts=10, max_wait=0.1
                )
            )

        # Assert - verify that cycle was called at least twice (first transient, then quota)
        handle_daily_quota_error_mock.assert_called_once_with(mock_client, quota_error)
        assert cycle_mock.call_count == 2

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_retry_with_extended_backoff_propagates_cancelled_error(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """CancelledError during retry is propagated immediately."""
        # Arrange
        daemon, _ = train_daemon
        mock_client = MagicMock(spec=httpx.AsyncClient)
        original_error = httpx.TimeoutException("timeout", request=MagicMock())

        mocker.patch.object(
            daemon,
            "_collect_train_positions_cycle",
            side_effect=asyncio.CancelledError(),
            autospec=True,
        )

        # Act & Assert
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                daemon._retry_with_extended_backoff(mock_client, original_error)
            )


class TestTrainPositionDaemonDailyQuota:
    """Tests for daily quota error handling."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_run_daily_quota_error_calls_handle_daily_quota_error(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mock_http_client: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """On DAILY_QUOTA error, calls _handle_daily_quota_error."""
        # Arrange
        daemon, _ = train_daemon
        daemon.running = True
        quota_error = CTATrackerAPIError("102", "Daily quota exceeded")
        mocker.patch.object(
            daemon,
            "_collect_train_positions_cycle",
            side_effect=quota_error,
            autospec=True,
        )

        handle_called = [False]

        async def mock_handle(_client: httpx.AsyncClient, _error: Exception) -> None:
            handle_called[0] = True
            daemon.running = False  # Stop after handling

        mocker.patch.object(
            daemon,
            "_handle_daily_quota_error",
            side_effect=mock_handle,
            autospec=True,
        )
        mocker.patch.object(daemon, "sleep", autospec=True)
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.httpx.AsyncClient",
            return_value=mock_http_client,
        )

        # Act
        asyncio.run(daemon.run())

        # Assert
        assert handle_called[0]

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_handle_daily_quota_error_probe_succeeds_on_first_attempt(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """Probe succeeds on first attempt and returns early."""
        # Arrange
        daemon, storage = train_daemon
        daemon.probe_102_attempts = 2
        daemon.probe_102_intervals = [300, 900]
        daemon.running = True
        mock_client = MagicMock(spec=httpx.AsyncClient)
        quota_error = CTATrackerAPIError("102", "Daily quota exceeded")

        # Mock cycle to succeed (no exception raised)
        raw = {"ctatt": {"tmst": "2026-01-25T12:00:00", "route": []}}
        records = [{"route": "red"}]
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_train_positions",
            new=mocker.AsyncMock(return_value=raw),
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.normalize_train_positions",
            return_value=records,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.time.time",
            return_value=1000.0,
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.aiometer.run_all",
            new=mocker.AsyncMock(return_value=[raw]),
        )

        sleep_calls: list[float] = []

        async def mock_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        mocker.patch.object(daemon, "sleep", side_effect=mock_sleep, autospec=True)

        # Act
        asyncio.run(daemon._handle_daily_quota_error(mock_client, quota_error))

        # Assert - should sleep for first interval, then succeed
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == 300
        # Verify that cycle succeeded (storage was called, meaning probe succeeded)
        assert storage.append_batch.called
        # Verify that only one probe was attempted (didn't continue to second probe)
        assert len(sleep_calls) == 1

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_handle_daily_quota_error_all_probes_fail_then_sleeps_until_midnight(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """All probes fail, then sleeps until midnight."""
        # Arrange
        daemon, _ = train_daemon
        daemon.probe_102_attempts = 2
        daemon.probe_102_intervals = [300, 900]
        daemon.running = True
        mock_client = MagicMock(spec=httpx.AsyncClient)
        quota_error = CTATrackerAPIError("102", "Daily quota exceeded")

        # All probes fail with 102
        cycle_mock = mocker.patch.object(
            daemon,
            "_collect_train_positions_cycle",
            side_effect=quota_error,
            autospec=True,
        )
        sleep_calls: list[float] = []

        async def mock_sleep(secs: float) -> None:
            sleep_calls.append(secs)
            if len(sleep_calls) >= 2:  # Stop after probe sleeps
                pass  # Continue to midnight sleep

        mocker.patch.object(daemon, "sleep", side_effect=mock_sleep, autospec=True)

        # Mock datetime to control midnight calculation
        chicago_tz = ZoneInfo("America/Chicago")
        # Set current time to 10:00 PM Chicago
        mock_now = datetime.datetime(2026, 1, 26, 22, 0, 0, tzinfo=chicago_tz)
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.datetime.datetime",
            now=lambda tz=None: mock_now if tz is None else mock_now.astimezone(tz),
        )

        sleep_until_midnight_called = [False]

        async def mock_sleep_until_midnight(_target: datetime.datetime) -> None:
            sleep_until_midnight_called[0] = True
            daemon.running = False

        mocker.patch.object(
            daemon,
            "_sleep_until_midnight",
            side_effect=mock_sleep_until_midnight,
            autospec=True,
        )

        # Act
        asyncio.run(daemon._handle_daily_quota_error(mock_client, quota_error))

        # Assert - should have tried both probes, then slept until midnight
        assert cycle_mock.call_count == 2
        assert sleep_until_midnight_called[0]

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_handle_daily_quota_error_probe_returns_non_102_error_reraises(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """If probe returns non-102 error, it is re-raised."""
        # Arrange
        daemon, _ = train_daemon
        daemon.probe_102_attempts = 2
        daemon.probe_102_intervals = [300, 900]
        daemon.running = True
        mock_client = MagicMock(spec=httpx.AsyncClient)
        quota_error = CTATrackerAPIError("102", "Daily quota exceeded")
        config_error = ValueError("CTA_API_KEY must be set")

        # First probe fails with non-102 error
        mocker.patch.object(
            daemon,
            "_collect_train_positions_cycle",
            side_effect=config_error,
            autospec=True,
        )

        async def mock_sleep(_secs: float) -> None:
            pass

        mocker.patch.object(daemon, "sleep", side_effect=mock_sleep, autospec=True)

        # Act & Assert
        with pytest.raises(ValueError, match="CTA_API_KEY must be set"):
            asyncio.run(daemon._handle_daily_quota_error(mock_client, quota_error))

        # Verify warning was logged
        warning_calls = [
            str(call)
            for call in cast("MagicMock", daemon.logger).warning.call_args_list
        ]
        probe_warning_logged = any("non-102 error" in call for call in warning_calls)
        assert probe_warning_logged

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_handle_daily_quota_error_stops_on_shutdown(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """If daemon stops running during probe, returns early."""
        # Arrange
        daemon, _ = train_daemon
        daemon.probe_102_attempts = 2
        daemon.probe_102_intervals = [300, 900]
        daemon.running = True
        mock_client = MagicMock(spec=httpx.AsyncClient)
        quota_error = CTATrackerAPIError("102", "Daily quota exceeded")

        async def mock_sleep(_secs: float) -> None:
            daemon.running = False  # Stop during first sleep

        mocker.patch.object(daemon, "sleep", side_effect=mock_sleep, autospec=True)

        # Act
        cycle_mock = mocker.patch.object(
            daemon,
            "_collect_train_positions_cycle",
            autospec=True,
        )
        asyncio.run(daemon._handle_daily_quota_error(mock_client, quota_error))

        # Assert - should return early, no probe attempts
        cycle_mock.assert_not_called()


class TestTrainPositionDaemonSleepUntilMidnight:
    """Tests for _sleep_until_midnight."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_sleep_until_midnight_sleeps_in_chunks(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """Sleeps in 60-second chunks until target time."""
        # Arrange
        daemon, _ = train_daemon
        daemon.running = True
        chicago_tz = ZoneInfo("America/Chicago")
        # Target is 2 minutes from now
        target_time = datetime.datetime(2026, 1, 26, 12, 2, 0, tzinfo=chicago_tz)

        # Mock datetime.now to advance time
        current_time = datetime.datetime(2026, 1, 26, 12, 0, 0, tzinfo=chicago_tz)
        time_advances = [
            current_time,
            current_time + datetime.timedelta(seconds=60),
            current_time + datetime.timedelta(seconds=120),
        ]

        def mock_now(tz: ZoneInfo | None = None) -> datetime.datetime:
            if len(time_advances) > 0:
                result = time_advances.pop(0)
            else:
                result = time_advances[-1]
            if tz is not None:
                return result.astimezone(tz)
            return result

        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.datetime.datetime",
            now=mock_now,
        )

        sleep_calls: list[float] = []

        async def mock_sleep(secs: float) -> None:
            sleep_calls.append(secs)
            if sum(sleep_calls) >= 120:  # After 2 minutes total
                daemon.running = False

        mocker.patch.object(daemon, "sleep", side_effect=mock_sleep, autospec=True)

        # Act
        asyncio.run(daemon._sleep_until_midnight(target_time))

        # Assert - should sleep in chunks
        assert len(sleep_calls) >= 2
        # Each chunk should be <= 60 seconds
        assert all(secs <= 60.0 for secs in sleep_calls)

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_sleep_until_midnight_returns_immediately_if_target_passed(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """Returns immediately if target time has already passed."""
        # Arrange
        daemon, _ = train_daemon
        daemon.running = True
        chicago_tz = ZoneInfo("America/Chicago")
        # Target is in the past
        target_time = datetime.datetime(2026, 1, 26, 12, 0, 0, tzinfo=chicago_tz)
        current_time = datetime.datetime(2026, 1, 26, 12, 1, 0, tzinfo=chicago_tz)

        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.datetime.datetime",
            now=lambda tz=None: current_time
            if tz is None
            else current_time.astimezone(tz),
        )

        sleep_called = [False]

        async def mock_sleep(_secs: float) -> None:
            sleep_called[0] = True

        mocker.patch.object(daemon, "sleep", side_effect=mock_sleep, autospec=True)

        # Act
        asyncio.run(daemon._sleep_until_midnight(target_time))

        # Assert - should not sleep
        assert not sleep_called[0]
        cast("MagicMock", daemon.logger).info.assert_any_call(
            "Reached target time (midnight). Resuming normal schedule.",
            extra={"extra_fields": {"target_time": target_time.isoformat()}},
        )

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_sleep_until_midnight_stops_on_shutdown(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """Stops sleeping if daemon stops running."""
        # Arrange
        daemon, _ = train_daemon
        daemon.running = True
        chicago_tz = ZoneInfo("America/Chicago")
        target_time = datetime.datetime(2026, 1, 26, 12, 5, 0, tzinfo=chicago_tz)
        current_time = datetime.datetime(2026, 1, 26, 12, 0, 0, tzinfo=chicago_tz)

        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.datetime.datetime",
            now=lambda tz=None: current_time
            if tz is None
            else current_time.astimezone(tz),
        )

        async def mock_sleep(_secs: float) -> None:
            daemon.running = False  # Stop during first sleep

        mocker.patch.object(daemon, "sleep", side_effect=mock_sleep, autospec=True)

        # Act
        asyncio.run(daemon._sleep_until_midnight(target_time))

        # Assert - should exit loop early
        # No exception should be raised


class TestTrainPositionDaemonProbeConfig:
    """Tests for probe 102 configuration."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_init_uses_default_probe_102_config_when_missing(
        self,
        mocker: MockerFixture,
        mock_logger: MagicMock,
    ) -> None:
        """__init__ uses defaults when probe_102 config is missing."""
        # Arrange
        config = {
            "collection": {"train_poll_interval_seconds": 15},
            "diagnostics": {"summary_interval_seconds": 10, "enabled": False},
            "rate_limits": {"cta": {"max_per_second": 0.1, "max_at_once": 3}},
        }
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.create_parquet_writer",
            return_value=MagicMock(),
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_config_section",
            return_value={"max_per_second": 0.1, "max_at_once": 3},
            autospec=True,
        )

        # Act
        daemon = TrainPositionDaemon(mock_logger, config)

        # Assert
        assert daemon.probe_102_attempts == 2
        assert daemon.probe_102_intervals == [300, 900]

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_init_uses_custom_probe_102_config(
        self,
        mocker: MockerFixture,
        mock_logger: MagicMock,
    ) -> None:
        """__init__ uses custom probe_102 config when provided."""
        # Arrange
        config = {
            "collection": {
                "train_poll_interval_seconds": 15,
                "probe_102_attempts": 3,
                "probe_102_intervals": [100, 200, 300],
            },
            "diagnostics": {"summary_interval_seconds": 10, "enabled": False},
            "rate_limits": {"cta": {"max_per_second": 0.1, "max_at_once": 3}},
        }
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.create_parquet_writer",
            return_value=MagicMock(),
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_config_section",
            return_value={"max_per_second": 0.1, "max_at_once": 3},
            autospec=True,
        )

        # Act
        daemon = TrainPositionDaemon(mock_logger, config)

        # Assert
        assert daemon.probe_102_attempts == 3
        assert daemon.probe_102_intervals == [100, 200, 300]

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_init_handles_probe_102_intervals_not_list(
        self,
        mocker: MockerFixture,
        mock_logger: MagicMock,
    ) -> None:
        """__init__ falls back to defaults if probe_102_intervals is not a list."""
        # Arrange
        config = {
            "collection": {
                "train_poll_interval_seconds": 15,
                "probe_102_intervals": "not a list",
            },
            "diagnostics": {"summary_interval_seconds": 10, "enabled": False},
            "rate_limits": {"cta": {"max_per_second": 0.1, "max_at_once": 3}},
        }
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.create_parquet_writer",
            return_value=MagicMock(),
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.get_config_section",
            return_value={"max_per_second": 0.1, "max_at_once": 3},
            autospec=True,
        )

        # Act
        daemon = TrainPositionDaemon(mock_logger, config)

        # Assert - should fall back to defaults
        assert daemon.probe_102_intervals == [300, 900]

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_handle_daily_quota_error_uses_last_interval_if_more_attempts_than_intervals(
        self,
        train_daemon: tuple[TrainPositionDaemon, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """If probe_102_attempts > len(probe_102_intervals), uses last interval for extra attempts."""
        # Arrange
        daemon, _ = train_daemon
        daemon.probe_102_attempts = 4  # More than intervals
        daemon.probe_102_intervals = [100, 200]  # Only 2 intervals
        daemon.running = True
        mock_client = MagicMock(spec=httpx.AsyncClient)
        quota_error = CTATrackerAPIError("102", "Daily quota exceeded")

        # All probes fail
        mocker.patch.object(
            daemon,
            "_collect_train_positions_cycle",
            side_effect=quota_error,
            autospec=True,
        )
        sleep_calls: list[float] = []

        async def mock_sleep(secs: float) -> None:
            sleep_calls.append(secs)
            if len(sleep_calls) >= 4:
                daemon.running = False

        mocker.patch.object(daemon, "sleep", side_effect=mock_sleep, autospec=True)

        # Mock datetime
        chicago_tz = ZoneInfo("America/Chicago")
        mock_now = datetime.datetime(2026, 1, 26, 22, 0, 0, tzinfo=chicago_tz)
        mocker.patch(
            "cta_eta.data_collection.orchestration.train_position_daemon.datetime.datetime",
            now=lambda tz=None: mock_now if tz is None else mock_now.astimezone(tz),
        )

        mocker.patch.object(
            daemon,
            "_sleep_until_midnight",
            autospec=True,
        )

        # Act
        asyncio.run(daemon._handle_daily_quota_error(mock_client, quota_error))

        # Assert - should use intervals [100, 200, 200, 200]
        assert sleep_calls == [100, 200, 200, 200]
