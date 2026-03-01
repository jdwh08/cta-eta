"""Unit tests for WeatherDaemon orchestration."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from cta_eta.data_collection.orchestration.weather_daemon import WeatherDaemon

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
    """Minimal config for WeatherDaemon initialization (deps are mocked)."""
    return {
        "collection": {"weather_interval_minutes": 15},
        "diagnostics": {"summary_interval_seconds": 10},
        "rate_limits": {
            "nws": {"max_per_second": 10, "max_at_once": 1},
            "open_meteo": {"max_per_second": 10, "max_at_once": 1},
            "openweathermap": {"max_per_second": 10, "max_at_once": 1},
        },
        "storage": {
            "immediate": {
                "data_path": "data/journals",
                "journal_rotation_minutes": 15,
                "partition_hour": 3,
            },
            "compaction": {
                "backend": "local",
                "staging_path": "data/compaction",
                "upload_prefix": "raw",
                "archive_path": "data/archive",
                "journal_retention_days": 7,
            },
        },
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
        "cta_eta.data_collection.orchestration.weather_daemon.create_journal_writer",
        return_value=storage,
        autospec=True,
    )

    daemon = WeatherDaemon(mock_logger, sample_config)
    return (daemon, stations_cache, nws_cache, om_cache, storage)


@pytest.fixture
def mock_http_clients() -> tuple[MagicMock, MagicMock, MagicMock]:
    """Create mock HTTP clients for testing."""
    nws_client = MagicMock(spec=httpx.AsyncClient)
    om_client = MagicMock(spec=httpx.AsyncClient)
    owm_client = MagicMock(spec=httpx.AsyncClient)
    return (nws_client, om_client, owm_client)


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

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.discover_nws_grid",
            new=mocker.AsyncMock(return_value="LOT/9,9"),
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_grid_discovery.discover_open_meteo_grid",
            new=mocker.AsyncMock(return_value="41.10,-87.10"),
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
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.discover_nws_grid",
            new=mocker.AsyncMock(side_effect=RuntimeError("boom")),
        )

        # Act
        mappings = asyncio.run(daemon._get_station_grid_mappings())

        # Assert
        assert mappings == []
        cast("MagicMock", daemon.logger).exception.assert_called()

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
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_grid_discovery.discover_open_meteo_grid",
            new=mocker.AsyncMock(side_effect=RuntimeError("nope")),
        )

        # Act
        mappings = asyncio.run(daemon._get_station_grid_mappings())

        # Assert
        assert mappings == []
        cast("MagicMock", daemon.logger).exception.assert_called()


class TestWeatherDaemonCollectWeatherCycle:
    """Tests for WeatherDaemon._collect_weather_cycle()."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_weather_cycle_runs_fallback_and_stores_records(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mock_http_clients: tuple[MagicMock, MagicMock, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """When a source fails, uses OpenWeatherMap fallback and stores merged output."""
        # Arrange
        daemon, *_caches, storage = weather_daemon
        nws_client, om_client, owm_client = mock_http_clients

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
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.get_nws_hourly_forecast",
            new=mocker.AsyncMock(side_effect=RuntimeError("nws down")),
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.get_open_meteo_current",
            new=mocker.AsyncMock(return_value={"om": True}),
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.get_openweathermap_current",
            new=mocker.AsyncMock(return_value={"owm": True}),
        )

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.merge_weather_sources",
            return_value={"temp_f": 10.0},
            autospec=True,
        )

        async def run_all_sequential(
            jobs: list, *, max_at_once: int, max_per_second: float
        ) -> list:
            _ = (max_at_once, max_per_second)
            return [await j() for j in jobs]

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.aiometer.run_all",
            side_effect=run_all_sequential,
            autospec=True,
        )

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.time.time",
            side_effect=[1000.0, 1000.5, 1002.0, 1003.0],
            autospec=True,
        )

        # Act
        asyncio.run(daemon._collect_weather_cycle(nws_client, om_client, owm_client))

        # Assert
        storage.append_batch.assert_called_once()
        _, kwargs = storage.append_batch.call_args
        assert kwargs["dataset_name"] == "weather"

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
    def test_collect_weather_cycle_handles_all_fetches_empty_gracefully(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mock_http_clients: tuple[MagicMock, MagicMock, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """When all run_all calls return empty or all-None, merge gets no data and we do not store."""
        # Arrange
        daemon, *_caches, storage = weather_daemon
        nws_client, om_client, owm_client = mock_http_clients

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
            "cta_eta.data_collection.orchestration.weather_daemon.aiometer.run_all",
            return_value=[],
            autospec=True,
        )

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.merge_weather_sources",
            return_value=None,
            autospec=True,
        )

        # Act
        asyncio.run(daemon._collect_weather_cycle(nws_client, om_client, owm_client))

        # Assert
        cast("MagicMock", daemon.logger).warning.assert_any_call(
            "No weather records to store this cycle"
        )
        storage.append_batch.assert_not_called()
        assert daemon.records_stored_last_cycle == 0

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_weather_cycle_warns_when_no_merged_records(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mock_http_clients: tuple[MagicMock, MagicMock, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """If merging produces no records, it warns and does not write."""
        # Arrange
        daemon, *_caches, storage = weather_daemon
        nws_client, om_client, owm_client = mock_http_clients

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

        async def run_all_sequential(
            jobs: list, *, max_at_once: int, max_per_second: float
        ) -> list:
            _ = (max_at_once, max_per_second)
            return [await j() for j in jobs]

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.aiometer.run_all",
            side_effect=run_all_sequential,
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.merge_weather_sources",
            return_value=None,
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.get_nws_hourly_forecast",
            new=mocker.AsyncMock(return_value={"nws": True}),
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.get_open_meteo_current",
            new=mocker.AsyncMock(return_value={"om": True}),
        )

        # Act
        asyncio.run(daemon._collect_weather_cycle(nws_client, om_client, owm_client))

        # Assert
        cast("MagicMock", daemon.logger).warning.assert_any_call(
            "No weather records to store this cycle"
        )
        assert daemon.records_stored_last_cycle == 0
        storage.append_batch.assert_not_called()

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_weather_cycle_logs_and_recovers_from_storage_failure(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mock_http_clients: tuple[MagicMock, MagicMock, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """Storage failures are logged and do not raise."""
        # Arrange
        daemon, *_caches, storage = weather_daemon
        nws_client, om_client, owm_client = mock_http_clients

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

        async def run_all_sequential(
            jobs: list, *, max_at_once: int, max_per_second: float
        ) -> list:
            _ = (max_at_once, max_per_second)
            return [await j() for j in jobs]

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.aiometer.run_all",
            side_effect=run_all_sequential,
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.merge_weather_sources",
            return_value={"temp_f": 10.0},
            autospec=True,
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.get_nws_hourly_forecast",
            new=mocker.AsyncMock(return_value={"nws": True}),
        )
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.get_open_meteo_current",
            new=mocker.AsyncMock(return_value={"om": True}),
        )
        storage.append_batch.side_effect = RuntimeError("disk full")

        # Act
        asyncio.run(daemon._collect_weather_cycle(nws_client, om_client, owm_client))

        # Assert
        cast("MagicMock", daemon.logger).exception.assert_called()
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

        # Mock httpx.AsyncClient context manager
        mock_client = MagicMock(spec=httpx.AsyncClient)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.httpx.AsyncClient",
            return_value=mock_client,
        )

        # Act
        asyncio.run(daemon.run())

        # Assert
        assert collect.call_count == 1
        assert sleep.call_count == 1
        # Check that error was logged (with error classification extra fields)
        exception_calls = [
            call
            for call in cast("MagicMock", daemon.logger).exception.call_args_list
            if "Weather collection cycle failed" in str(call)
            or "error" in str(call).lower()
        ]
        assert len(exception_calls) > 0, "Expected exception to be logged"

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_run_creates_and_reuses_http_clients(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mocker: MockerFixture,
    ) -> None:
        """run() creates HTTP clients once and reuses them across cycles."""
        # Arrange
        daemon, *_ = weather_daemon
        daemon.running = True

        cycle_count = 0

        async def collect_side_effect(
            nws_client: httpx.AsyncClient,
            om_client: httpx.AsyncClient,
            owm_client: httpx.AsyncClient,
        ) -> None:
            nonlocal cycle_count
            cycle_count += 1
            # Verify clients are provided
            assert nws_client is not None
            assert om_client is not None
            assert owm_client is not None
            if cycle_count >= 2:
                daemon.running = False

        mocker.patch.object(
            daemon,
            "_collect_weather_cycle",
            side_effect=collect_side_effect,
            autospec=True,
        )

        async def sleep_side_effect(_: float) -> None:
            # Don't stop running, let collect_side_effect handle it
            pass

        mocker.patch.object(
            daemon, "sleep", side_effect=sleep_side_effect, autospec=True
        )

        # Mock httpx.AsyncClient context manager
        mock_client = MagicMock(spec=httpx.AsyncClient)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        client_constructor = mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.httpx.AsyncClient",
            return_value=mock_client,
        )

        # Act
        asyncio.run(daemon.run())

        # Assert
        # Should create 3 clients (nws, om, owm) once
        assert client_constructor.call_count == 3
        assert cycle_count == 2


class TestWeatherDaemonDiscoveryTimeouts:
    """Tests for timeout handling in discovery operations."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_discovery_handles_overall_batch_timeout(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mocker: MockerFixture,
    ) -> None:
        """Discovery handles overall batch timeout via time.monotonic check and returns partial dict."""
        # Arrange
        daemon, _stations_cache, _nws_cache, _, _storage = weather_daemon
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_grid_discovery._OVERALL_BATCH_TIMEOUT_S",
            new=0.02,
        )

        async def slow_discover(_client: object, lat: float, lon: float) -> str:
            await asyncio.sleep(0.05)
            return f"{lat},{lon}"

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_grid_discovery.discover_open_meteo_grid",
            new=mocker.AsyncMock(side_effect=slow_discover),
        )

        requests = [("s1", 1.0, 1.0), ("s2", 2.0, 2.0)]
        mock_client = MagicMock(spec=httpx.AsyncClient)

        # Act
        result = asyncio.run(
            daemon._discover_open_meteo_grids_for_stations(mock_client, requests)
        )

        # Assert
        assert isinstance(result, dict)


class TestWeatherDaemonErrorHandling:
    """Tests for improved error handling."""

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_weather_cycle_handles_empty_station_mappings(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mock_http_clients: tuple[MagicMock, MagicMock, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """Cycle skips gracefully when no station mappings are resolved."""
        # Arrange
        daemon, *_caches, storage = weather_daemon
        nws_client, om_client, owm_client = mock_http_clients

        mocker.patch.object(
            daemon,
            "_get_station_grid_mappings",
            return_value=[],  # Empty mappings
            autospec=True,
        )

        # Act
        asyncio.run(daemon._collect_weather_cycle(nws_client, om_client, owm_client))

        # Assert
        cast("MagicMock", daemon.logger).warning.assert_any_call(
            "No station mappings resolved, skipping cycle"
        )
        storage.append_batch.assert_not_called()
        assert daemon.records_stored_last_cycle == 0

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_collect_weather_cycle_handles_exception_during_cycle(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mock_http_clients: tuple[MagicMock, MagicMock, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """Cycle logs exceptions and re-raises them."""
        # Arrange
        daemon, *_caches, _storage = weather_daemon
        nws_client, om_client, owm_client = mock_http_clients

        test_error = RuntimeError("Test error")

        mocker.patch.object(
            daemon,
            "_get_station_grid_mappings",
            side_effect=test_error,
            autospec=True,
        )

        # Act & Assert
        with pytest.raises(RuntimeError, match="Test error"):
            asyncio.run(
                daemon._collect_weather_cycle(nws_client, om_client, owm_client)
            )

        cast("MagicMock", daemon.logger).exception.assert_called_once()
        # Verify it logged the error with cycle context
        call_args = cast("MagicMock", daemon.logger).exception.call_args
        assert "Error during weather collection cycle" in str(call_args)

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_fetch_nws_by_grid_returns_empty_dict_when_run_all_returns_empty_list(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mocker: MockerFixture,
    ) -> None:
        """_fetch_nws_by_grid returns empty dict when run_all returns []."""
        # Arrange
        daemon, *_ = weather_daemon
        mock_client = MagicMock(spec=httpx.AsyncClient)

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.aiometer.run_all",
            return_value=[],
            autospec=True,
        )

        # Act
        result = asyncio.run(daemon._fetch_nws_by_grid(mock_client, ["LOT/1,2"]))

        # Assert
        assert result == {}

    @pytest.mark.usefixtures("cleanup_state_files")
    def test_fetch_open_meteo_by_grid_returns_empty_dict_when_run_all_returns_empty_list(
        self,
        weather_daemon: tuple[
            WeatherDaemon, MagicMock, MagicMock, MagicMock, MagicMock
        ],
        mocker: MockerFixture,
    ) -> None:
        """_fetch_open_meteo_by_grid returns empty dict when run_all returns []."""
        # Arrange
        daemon, *_ = weather_daemon
        mock_client = MagicMock(spec=httpx.AsyncClient)

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_daemon.aiometer.run_all",
            return_value=[],
            autospec=True,
        )

        # Act
        result = asyncio.run(
            daemon._fetch_open_meteo_by_grid(mock_client, ["41.0,-87.0"])
        )

        # Assert
        assert result == {}
