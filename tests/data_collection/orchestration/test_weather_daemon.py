"""Unit tests for WeatherDaemon orchestration."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from cta_eta.data_collection.orchestration.weather_daemon import WeatherDaemon

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Generator
    from typing import Any

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
def sample_config() -> dict[str, dict[str, str | int | float | bool]]:
    """Minimal config for WeatherDaemon initialization (deps are mocked)."""
    return {"collection": {"weather_interval_minutes": 15}}


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
def weather_daemon(
    mocker: MockerFixture,
    sample_config: dict[str, dict[str, str | int | float | bool]],
    mock_logger: MagicMock,
) -> tuple[
    WeatherDaemon,
    MagicMock,
    MagicMock,
    MagicMock,
    MagicMock,
]:
    """Create a WeatherDaemon with all external dependencies mocked."""
    stations_cache = MagicMock()
    nws_cache = MagicMock()
    om_cache = MagicMock()
    storage = MagicMock()

    mocker.patch(
        "cta_eta.data_collection.orchestration.weather_daemon.get_stations_cache",
        return_value=stations_cache,
        autospec=True,
    )
    mocker.patch(
        "cta_eta.data_collection.orchestration.weather_daemon.get_nws_grid_cache",
        return_value=nws_cache,
        autospec=True,
    )
    mocker.patch(
        "cta_eta.data_collection.orchestration.weather_daemon.get_open_meteo_grid_cache",
        return_value=om_cache,
        autospec=True,
    )
    mocker.patch(
        "cta_eta.data_collection.orchestration.weather_daemon.create_parquet_writer",
        return_value=storage,
        autospec=True,
    )

    daemon = WeatherDaemon(sample_config, mock_logger)
    return (daemon, stations_cache, nws_cache, om_cache, storage)


class TestWeatherDaemonInit:
    """Tests for WeatherDaemon initialization."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_init_loads_dependencies_and_sets_interval(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
    ) -> None:
        """__init__ loads caches/storage and converts minutes to seconds."""
        # Arrange & Act
        daemon, stations_cache, nws_cache, om_cache, storage = weather_daemon

        # Assert
        assert daemon.stations_cache is stations_cache
        assert daemon.nws_grid_cache is nws_cache
        assert daemon.om_grid_cache is om_cache
        assert daemon.storage is storage
        assert daemon.weather_interval == 15 * 60
        assert daemon.last_collection_time == 0.0
        assert daemon.records_stored_last_cycle == 0


class TestWeatherDaemonGetUniqueGridPoints:
    """Tests for WeatherDaemon._get_station_grid_mappings()."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_get_station_grid_mappings_returns_station_scoped_mappings_on_cache_hits(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
    ) -> None:
        """Returns one mapping per station even when grids are shared."""
        # Arrange
        daemon, stations_cache, nws_cache, om_cache, _ = weather_daemon
        stations_cache.get.return_value = [
            {"id": "s1", "latitude": "41.0", "longitude": "-87.0"},
            {"id": "s2", "latitude": "41.0", "longitude": "-87.0"},
        ]
        nws_cache.get_grid_identifier.side_effect = ["LOT/1,2", "LOT/1,2"]
        om_cache.get_grid_identifier.side_effect = ["41.0,-87.0", "41.0,-87.0"]

        # Act
        mappings = asyncio.run(daemon._get_station_grid_mappings())

        # Assert
        assert len(mappings) == 2
        assert {m.station_id for m in mappings} == {"s1", "s2"}
        assert {m.nws_grid_id for m in mappings} == {"LOT/1,2"}
        assert {m.open_meteo_grid_id for m in mappings} == {"41.0,-87.0"}
        assert mappings[0].station_latitude == 41.0
        assert mappings[0].station_longitude == -87.0
        assert mappings[1].station_latitude == 41.0
        assert mappings[1].station_longitude == -87.0
        om_cache.set_grid_identifier.assert_not_called()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_get_station_grid_mappings_discovers_and_caches_misses(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mocker: MockerFixture,
    ) -> None:
        """On cache miss, discovers grids and updates caches."""
        # Arrange
        daemon, stations_cache, nws_cache, om_cache, _ = weather_daemon
        stations_cache.get.return_value = [
            {"id": "s1", "latitude": "41.1", "longitude": "-87.1"}
        ]
        nws_cache.get_grid_identifier.return_value = None
        om_cache.get_grid_identifier.return_value = None

        mocker.patch.object(
            daemon,
            "_discover_nws_grid",
            return_value="LOT/9,9",
            autospec=True,
        )
        mocker.patch.object(
            daemon,
            "_discover_open_meteo_grid",
            return_value="41.10,-87.10",
            autospec=True,
        )

        async def run_sequentially(
            func: Callable[[Any], Awaitable[Any]],
            items: list[Any],
            *,
            max_per_second: float,
            max_at_once: int,
        ) -> list[Any]:
            _ = (max_per_second, max_at_once)
            return [await func(item) for item in items]

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.aiometer.run_on_each",
            side_effect=run_sequentially,
            autospec=True,
        )

        # Act
        mappings = asyncio.run(daemon._get_station_grid_mappings())

        # Assert
        assert len(mappings) == 1
        assert mappings[0].station_id == "s1"
        assert mappings[0].station_latitude == 41.1
        assert mappings[0].station_longitude == -87.1
        assert mappings[0].nws_grid_id == "LOT/9,9"
        assert mappings[0].open_meteo_grid_id == "41.10,-87.10"
        nws_cache.set_grid_identifier.assert_called_once_with("s1", "LOT/9,9")
        om_cache.set_grid_identifier.assert_called_once_with("s1", "41.10,-87.10")

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_get_station_grid_mappings_skips_station_on_nws_discovery_failure(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mocker: MockerFixture,
    ) -> None:
        """If NWS discovery fails for a station, that station is skipped."""
        # Arrange
        daemon, stations_cache, nws_cache, om_cache, _ = weather_daemon
        stations_cache.get.return_value = [
            {"id": "s1", "latitude": "41.0", "longitude": "-87.0"}
        ]
        nws_cache.get_grid_identifier.return_value = None
        om_cache.get_grid_identifier.return_value = "41.0,-87.0"
        mocker.patch.object(
            daemon,
            "_discover_nws_grid",
            side_effect=RuntimeError("boom"),
            autospec=True,
        )

        # Act
        mappings = asyncio.run(daemon._get_station_grid_mappings())

        # Assert
        assert mappings == []
        daemon.logger.exception.assert_called()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_get_station_grid_mappings_skips_station_on_open_meteo_discovery_failure(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mocker: MockerFixture,
    ) -> None:
        """If Open-Meteo discovery fails for a station, that station is skipped."""
        # Arrange
        daemon, stations_cache, nws_cache, om_cache, _ = weather_daemon
        stations_cache.get.return_value = [
            {"id": "s1", "latitude": "41.0", "longitude": "-87.0"}
        ]
        nws_cache.get_grid_identifier.return_value = "LOT/1,2"
        om_cache.get_grid_identifier.return_value = None
        mocker.patch.object(
            daemon,
            "_discover_open_meteo_grid",
            side_effect=RuntimeError("nope"),
            autospec=True,
        )

        async def run_sequentially(
            func: Callable[[Any], Awaitable[Any]],
            items: list[Any],
            *,
            max_per_second: float,
            max_at_once: int,
        ) -> list[Any]:
            _ = (max_per_second, max_at_once)
            return [await func(item) for item in items]

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.aiometer.run_on_each",
            side_effect=run_sequentially,
            autospec=True,
        )

        # Act
        mappings = asyncio.run(daemon._get_station_grid_mappings())

        # Assert
        assert mappings == []
        daemon.logger.exception.assert_called()


class TestWeatherDaemonCollectWeatherCycle:
    """Tests for WeatherDaemon._collect_weather_cycle()."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_weather_cycle_runs_fallback_and_stores_records(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mocker: MockerFixture,
    ) -> None:
        """When a source fails, uses OpenWeatherMap fallback and stores merged output."""
        # Arrange
        daemon, *_caches, storage = weather_daemon

        mocker.patch.object(
            daemon,
            "_get_station_grid_mappings",
            return_value=[
                mocker.Mock(
                    station_id="s1",
                    station_latitude=41.0,
                    station_longitude=-87.0,
                    nws_grid_id="LOT/1,2",
                    open_meteo_grid_id="41.0,-87.0",
                )
            ],
            autospec=True,
        )

        # Simulate NWS failure -> triggers OpenWeatherMap fallback
        mocker.patch.object(
            daemon,
            "_get_nws_hourly_forecast",
            side_effect=RuntimeError("nws down"),
            autospec=True,
        )
        mocker.patch.object(
            daemon,
            "_get_open_meteo_current",
            return_value={"om": True},
            autospec=True,
        )
        mocker.patch.object(
            daemon,
            "_get_openweathermap_current",
            return_value={"owm": True},
            autospec=True,
        )

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.merge_weather_sources",
            return_value={"temp_f": 10.0},
            autospec=True,
        )

        async def run_sequentially(
            func: Callable[[Any], Awaitable[Any]],
            items: list[Any],
            *,
            max_per_second: float,
            max_at_once: int,
        ) -> list[Any]:
            _ = (max_per_second, max_at_once)
            return [await func(item) for item in items]

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.aiometer.run_on_each",
            side_effect=run_sequentially,
            autospec=True,
        )

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.time.time",
            side_effect=[1000.0, 1000.5, 1002.0, 1003.0],
            autospec=True,
        )

        # Act
        asyncio.run(daemon._collect_weather_cycle())

        # Assert
        storage.append_batch.assert_called_once()
        _, kwargs = storage.append_batch.call_args
        assert kwargs["dataset_name"] == "weather_unified"

        stored_records = storage.append_batch.call_args[0][0]
        assert len(stored_records) == 1
        assert stored_records[0]["temp_f"] == 10.0
        assert stored_records[0]["station_id"] == "s1"
        assert stored_records[0]["nws_grid_id"] == "LOT/1,2"
        assert stored_records[0]["open_meteo_grid_id"] == "41.0,-87.0"
        assert stored_records[0]["latitude"] == 41.0
        assert stored_records[0]["longitude"] == -87.0
        assert stored_records[0]["collection_timestamp"] == 1000.5
        assert daemon.records_stored_last_cycle == 1

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_weather_cycle_returns_early_when_aiometer_returns_none(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mocker: MockerFixture,
    ) -> None:
        """If aiometer returns None, logs an error and does not write."""
        # Arrange
        daemon, *_caches, storage = weather_daemon

        mocker.patch.object(
            daemon,
            "_get_station_grid_mappings",
            return_value=[
                mocker.Mock(
                    station_id="s1",
                    station_latitude=41.0,
                    station_longitude=-87.0,
                    nws_grid_id="LOT/1,2",
                    open_meteo_grid_id="41.0,-87.0",
                )
            ],
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.aiometer.run_on_each",
            return_value=None,
            autospec=True,
        )

        # Act
        asyncio.run(daemon._collect_weather_cycle())

        # Assert
        daemon.logger.error.assert_called_once_with(
            "No results from weather collection cycle"
        )
        storage.append_batch.assert_not_called()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_weather_cycle_warns_when_no_merged_records(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mocker: MockerFixture,
    ) -> None:
        """If merging produces no records, it warns and does not write."""
        # Arrange
        daemon, *_caches, storage = weather_daemon

        mocker.patch.object(
            daemon,
            "_get_station_grid_mappings",
            return_value=[
                mocker.Mock(
                    station_id="s1",
                    station_latitude=41.0,
                    station_longitude=-87.0,
                    nws_grid_id="LOT/1,2",
                    open_meteo_grid_id="41.0,-87.0",
                )
            ],
            autospec=True,
        )

        async def run_sequentially(
            func: Callable[[Any], Awaitable[Any]],
            items: list[Any],
            *,
            max_per_second: float,
            max_at_once: int,
        ) -> list[Any]:
            _ = (max_per_second, max_at_once)
            return [await func(item) for item in items]

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.aiometer.run_on_each",
            side_effect=run_sequentially,
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.merge_weather_sources",
            return_value=None,
            autospec=True,
        )
        mocker.patch.object(
            daemon,
            "_get_nws_hourly_forecast",
            return_value={"nws": True},
            autospec=True,
        )
        mocker.patch.object(
            daemon,
            "_get_open_meteo_current",
            return_value={"om": True},
            autospec=True,
        )

        # Act
        asyncio.run(daemon._collect_weather_cycle())

        # Assert
        daemon.logger.warning.assert_any_call("No weather records to store this cycle")
        assert daemon.records_stored_last_cycle == 0
        storage.append_batch.assert_not_called()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_weather_cycle_logs_and_recovers_from_storage_failure(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mocker: MockerFixture,
    ) -> None:
        """Storage failures are logged and do not raise."""
        # Arrange
        daemon, *_caches, storage = weather_daemon

        mocker.patch.object(
            daemon,
            "_get_station_grid_mappings",
            return_value=[
                mocker.Mock(
                    station_id="s1",
                    station_latitude=41.0,
                    station_longitude=-87.0,
                    nws_grid_id="LOT/1,2",
                    open_meteo_grid_id="41.0,-87.0",
                )
            ],
            autospec=True,
        )

        async def run_sequentially(
            func: Callable[[Any], Awaitable[Any]],
            items: list[Any],
            *,
            max_per_second: float,
            max_at_once: int,
        ) -> list[Any]:
            _ = (max_per_second, max_at_once)
            return [await func(item) for item in items]

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.aiometer.run_on_each",
            side_effect=run_sequentially,
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.merge_weather_sources",
            return_value={"temp_f": 10.0},
            autospec=True,
        )
        mocker.patch.object(
            daemon,
            "_get_nws_hourly_forecast",
            return_value={"nws": True},
            autospec=True,
        )
        mocker.patch.object(
            daemon,
            "_get_open_meteo_current",
            return_value={"om": True},
            autospec=True,
        )
        storage.append_batch.side_effect = RuntimeError("disk full")

        # Act
        asyncio.run(daemon._collect_weather_cycle())

        # Assert
        daemon.logger.exception.assert_called()
        assert daemon.records_stored_last_cycle == 0


class TestWeatherDaemonRunLoop:
    """Tests for WeatherDaemon.run()."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_run_logs_and_continues_on_cycle_exception(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mocker: MockerFixture,
    ) -> None:
        """run() catches cycle exceptions, logs, and still sleeps once."""
        # Arrange
        daemon, *_ = weather_daemon
        daemon.running = True

        collect = mocker.patch.object(
            daemon,
            "_collect_weather_cycle",
            side_effect=RuntimeError("boom"),
            autospec=True,
        )

        async def stop_after_sleep(_: float) -> None:
            daemon.running = False

        sleep = mocker.patch.object(
            daemon, "sleep", side_effect=stop_after_sleep, autospec=True
        )

        # Act
        asyncio.run(daemon.run())

        # Assert
        assert collect.call_count == 1
        assert sleep.call_count == 1
        daemon.logger.exception.assert_any_call("Weather collection cycle failed")
