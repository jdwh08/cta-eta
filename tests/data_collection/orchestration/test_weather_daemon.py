"""Unit tests for WeatherDaemon orchestration."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from cta_eta.data_collection.orchestration.weather_daemon import WeatherDaemon

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Generator
    from typing import Any

    import httpx
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
        weather_daemon: tuple[WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock],
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
        assert daemon.unique_grid_points_count == 0
        assert daemon.records_stored_last_cycle == 0


class TestWeatherDaemonGetUniqueGridPoints:
    """Tests for WeatherDaemon._get_unique_grid_points()."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_get_unique_grid_points_deduplicates_cache_hits(
        self,
        weather_daemon: tuple[WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Deduplicates stations when both grid caches hit."""
        # Arrange
        daemon, stations_cache, nws_cache, om_cache, _ = weather_daemon
        stations_cache.get.return_value = [
            {"id": "s1", "latitude": "41.0", "longitude": "-87.0"},
            {"id": "s2", "latitude": "41.0", "longitude": "-87.0"},
        ]
        nws_cache.get_grid_identifier.side_effect = ["LOT/1,2", "LOT/1,2"]
        om_cache.get_grid_identifier.side_effect = ["41.0,-87.0", "41.0,-87.0"]

        client = MagicMock()

        # Act
        unique = asyncio.run(daemon._get_unique_grid_points(client))

        # Assert
        assert unique == [("LOT/1,2", "41.0,-87.0", 41.0, -87.0)]
        nws_cache.resolve_grid_identifier.assert_not_called()
        om_cache.set_grid_identifier.assert_not_called()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_get_unique_grid_points_discovers_and_caches_misses(
        self,
        weather_daemon: tuple[WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """On cache miss, discovers grids and updates caches."""
        # Arrange
        daemon, stations_cache, nws_cache, om_cache, _ = weather_daemon
        stations_cache.get.return_value = [
            {"id": "s1", "latitude": "41.1", "longitude": "-87.1"}
        ]
        nws_cache.get_grid_identifier.return_value = None
        nws_cache.resolve_grid_identifier.return_value = "LOT/9,9"
        om_cache.get_grid_identifier.return_value = None

        discover = mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.discover_open_meteo_grid",
            return_value="41.10,-87.10",
            autospec=True,
        )
        client = MagicMock()

        # Act
        unique = asyncio.run(daemon._get_unique_grid_points(client))

        # Assert
        assert unique == [("LOT/9,9", "41.10,-87.10", 41.1, -87.1)]
        nws_cache.resolve_grid_identifier.assert_called_once_with("s1", 41.1, -87.1)
        discover.assert_called_once_with(client, 41.1, -87.1)
        om_cache.set_grid_identifier.assert_called_once_with("s1", "41.10,-87.10")

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_get_unique_grid_points_skips_station_on_nws_discovery_failure(
        self,
        weather_daemon: tuple[WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """If NWS discovery fails for a station, that station is skipped."""
        # Arrange
        daemon, stations_cache, nws_cache, om_cache, _ = weather_daemon
        stations_cache.get.return_value = [
            {"id": "s1", "latitude": "41.0", "longitude": "-87.0"}
        ]
        nws_cache.get_grid_identifier.return_value = None
        nws_cache.resolve_grid_identifier.side_effect = RuntimeError("boom")
        om_cache.get_grid_identifier.return_value = "41.0,-87.0"

        client = MagicMock()

        # Act
        unique = asyncio.run(daemon._get_unique_grid_points(client))

        # Assert
        assert unique == []
        daemon.logger.exception.assert_called()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_get_unique_grid_points_skips_station_on_open_meteo_discovery_failure(
        self,
        weather_daemon: tuple[WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock],
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
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.discover_open_meteo_grid",
            side_effect=RuntimeError("nope"),
            autospec=True,
        )
        client = MagicMock()

        # Act
        unique = asyncio.run(daemon._get_unique_grid_points(client))

        # Assert
        assert unique == []
        daemon.logger.exception.assert_called()


class TestWeatherDaemonFetchWeatherForGridPoint:
    """Tests for WeatherDaemon._fetch_weather_for_grid_point()."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_fetch_weather_for_grid_point_returns_both_sources(
        self,
        weather_daemon: tuple[WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """Returns dicts for both providers when both succeed."""
        # Arrange
        daemon, *_ = weather_daemon
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.time.time",
            return_value=123.0,
            autospec=True,
        )

        async def immediate_to_thread(
            func: Callable[..., Any], /, *args: Any, **kwargs: Any
        ) -> Any:
            _ = kwargs
            return func(*args)

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.asyncio.to_thread",
            side_effect=immediate_to_thread,
            autospec=True,
        )

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.get_nws_hourly_forecast",
            return_value={"nws": True},
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.get_open_meteo_current",
            return_value={"om": True},
            autospec=True,
        )
        client = MagicMock()

        # Act
        nws, om, lat, lon, ts = asyncio.run(
            daemon._fetch_weather_for_grid_point(client, "LOT/1,2", "41,-87", 41.0, -87.0)
        )

        # Assert
        assert nws == {"nws": True}
        assert om == {"om": True}
        assert lat == 41.0
        assert lon == -87.0
        assert ts == 123.0

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_fetch_weather_for_grid_point_logs_partial_failures(
        self,
        weather_daemon: tuple[WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """Logs warnings and returns None for providers that raise."""
        # Arrange
        daemon, *_ = weather_daemon
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.time.time",
            return_value=999.0,
            autospec=True,
        )

        def nws_fail(_: httpx.AsyncClient, __: str) -> dict[str, Any]:  # noqa: ARG001
            raise httpx.HTTPError("nws down")

        def om_ok(_: httpx.AsyncClient, __: str) -> dict[str, Any]:  # noqa: ARG001
            return {"om": "ok"}

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.get_nws_hourly_forecast",
            side_effect=nws_fail,
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.get_open_meteo_current",
            side_effect=om_ok,
            autospec=True,
        )

        async def immediate_to_thread(
            func: Callable[..., Any], /, *args: Any, **kwargs: Any
        ) -> Any:
            _ = kwargs
            return func(*args)

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.asyncio.to_thread",
            side_effect=immediate_to_thread,
            autospec=True,
        )
        client = MagicMock()

        # Act
        nws, om, _, _, ts = asyncio.run(
            daemon._fetch_weather_for_grid_point(client, "LOT/1,2", "41,-87", 1.0, 2.0)
        )

        # Assert
        assert nws is None
        assert om == {"om": "ok"}
        assert ts == 999.0
        daemon.logger.warning.assert_called()


class TestWeatherDaemonCollectWeatherCycle:
    """Tests for WeatherDaemon._collect_weather_cycle()."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_weather_cycle_runs_fallback_and_stores_records(
        self,
        weather_daemon: tuple[WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """When a source fails, uses OpenWeatherMap fallback and stores merged output."""
        # Arrange
        daemon, *_caches, storage = weather_daemon
        client = MagicMock()

        mocker.patch.object(
            daemon,
            "_get_unique_grid_points",
            return_value=[("LOT/1,2", "41.0,-87.0", 41.0, -87.0)],
            autospec=True,
        )

        mocker.patch.object(
            daemon,
            "_fetch_weather_for_grid_point",
            return_value=(None, {"om": True}, 41.0, -87.0, 111.0),
            autospec=True,
        )

        async def immediate_to_thread(
            func: Callable[..., Any], /, *args: Any, **kwargs: Any
        ) -> Any:
            _ = kwargs
            return func(*args)

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.asyncio.to_thread",
            side_effect=immediate_to_thread,
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.get_openweathermap_current",
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
            side_effect=[1000.0, 1001.0, 1002.0],
            autospec=True,
        )

        # Act
        asyncio.run(daemon._collect_weather_cycle(client))

        # Assert
        storage.append_batch.assert_called_once()
        _, kwargs = storage.append_batch.call_args
        assert kwargs["dataset_name"] == "weather_unified"

        stored_records = storage.append_batch.call_args[0][0]
        assert len(stored_records) == 1
        assert stored_records[0]["temp_f"] == 10.0
        assert stored_records[0]["latitude"] == 41.0
        assert stored_records[0]["longitude"] == -87.0
        assert stored_records[0]["collection_timestamp"] == 111.0
        assert daemon.records_stored_last_cycle == 1
        assert daemon.unique_grid_points_count == 1

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_weather_cycle_returns_early_when_aiometer_returns_none(
        self,
        weather_daemon: tuple[WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """If aiometer returns None, logs an error and does not write."""
        # Arrange
        daemon, *_caches, storage = weather_daemon
        client = MagicMock()

        mocker.patch.object(
            daemon,
            "_get_unique_grid_points",
            return_value=[("LOT/1,2", "41.0,-87.0", 41.0, -87.0)],
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.aiometer.run_on_each",
            return_value=None,
            autospec=True,
        )

        # Act
        asyncio.run(daemon._collect_weather_cycle(client))

        # Assert
        daemon.logger.error.assert_called_once_with("No results from weather collection cycle")
        storage.append_batch.assert_not_called()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_weather_cycle_warns_when_no_merged_records(
        self,
        weather_daemon: tuple[WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """If merging produces no records, it warns and does not write."""
        # Arrange
        daemon, *_caches, storage = weather_daemon
        client = MagicMock()

        mocker.patch.object(
            daemon,
            "_get_unique_grid_points",
            return_value=[("LOT/1,2", "41.0,-87.0", 41.0, -87.0)],
            autospec=True,
        )
        mocker.patch.object(
            daemon,
            "_fetch_weather_for_grid_point",
            return_value=({"nws": True}, {"om": True}, 41.0, -87.0, 111.0),
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

        # Act
        asyncio.run(daemon._collect_weather_cycle(client))

        # Assert
        daemon.logger.warning.assert_any_call("No weather records to store this cycle")
        assert daemon.records_stored_last_cycle == 0
        storage.append_batch.assert_not_called()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_weather_cycle_logs_and_recovers_from_storage_failure(
        self,
        weather_daemon: tuple[WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """Storage failures are logged and do not raise."""
        # Arrange
        daemon, *_caches, storage = weather_daemon
        client = MagicMock()

        mocker.patch.object(
            daemon,
            "_get_unique_grid_points",
            return_value=[("LOT/1,2", "41.0,-87.0", 41.0, -87.0)],
            autospec=True,
        )
        mocker.patch.object(
            daemon,
            "_fetch_weather_for_grid_point",
            return_value=({"nws": True}, {"om": True}, 41.0, -87.0, 111.0),
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
        storage.append_batch.side_effect = RuntimeError("disk full")

        # Act
        asyncio.run(daemon._collect_weather_cycle(client))

        # Assert
        daemon.logger.exception.assert_called()
        assert daemon.records_stored_last_cycle == 0


class TestWeatherDaemonRunLoop:
    """Tests for WeatherDaemon.run()."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_run_logs_and_continues_on_cycle_exception(
        self,
        weather_daemon: tuple[WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock],
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

        sleep = mocker.patch.object(daemon, "sleep", side_effect=stop_after_sleep, autospec=True)

        client = MagicMock()
        async_cm = MagicMock()
        async_cm.__aenter__ = AsyncMock(return_value=client)
        async_cm.__aexit__ = AsyncMock(return_value=None)
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.httpx.AsyncClient",
            return_value=async_cm,
            autospec=True,
        )

        # Act
        asyncio.run(daemon.run())

        # Assert
        assert collect.call_count == 1
        assert sleep.call_count == 1
        daemon.logger.exception.assert_any_call("Weather collection cycle failed")

