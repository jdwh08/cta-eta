"""Unit tests for OpenMeteoWeatherGridDiscoverer."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from cta_eta.data_collection.orchestration.weather_grid_discovery import (
    OpenMeteoWeatherGridDiscoverer,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


def _make_diagnostics_mock() -> MagicMock:
    d = MagicMock()
    d.record_event = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=None)
    cm.__aexit__ = AsyncMock(return_value=None)
    d.span = MagicMock(return_value=cm)
    return d


@pytest.fixture
def discoverer(
    tmp_path: Path,
) -> tuple[OpenMeteoWeatherGridDiscoverer, MagicMock, MagicMock]:
    """Create a OpenMeteoWeatherGridDiscoverer with mocked dependencies."""
    marker_path = tmp_path / "marker.json"

    def write(d: dict) -> None:
        marker_path.write_text(json.dumps(d), encoding="utf-8")

    logger = MagicMock()
    diagnostics = _make_diagnostics_mock()
    om_cache = MagicMock()
    discoverer = OpenMeteoWeatherGridDiscoverer(
        logger=logger,
        diagnostics=diagnostics,
        om_grid_cache=om_cache,
        write_discovery_state_marker=write,
        daemon_class="TestDiscoverer",
    )
    return (discoverer, om_cache, logger)


class TestOpenMeteoWeatherGridDiscovererPersistsOnCancellation:
    """Tests for incremental persistence when discovery is cancelled."""

    def test_persists_partial_results_on_cancellation(
        self,
        discoverer: tuple[OpenMeteoWeatherGridDiscoverer, MagicMock, MagicMock],
        tmp_path: Path,
        mocker: MockerFixture,
    ) -> None:
        """Successful discoveries are persisted even if the batch is cancelled."""
        d, om_cache, _ = discoverer
        call_count = 0

        async def discover_side_effect(_client: object, lat: float, lon: float) -> str:
            nonlocal call_count
            call_count += 1
            if lat == 1.0 and lon == 1.0:
                await asyncio.sleep(0.01)
                return "1.0,1.0"
            await asyncio.sleep(0.01)
            msg = "cancelled"
            raise asyncio.CancelledError(msg)

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_grid_discovery.discover_open_meteo_grid",
            new=AsyncMock(side_effect=discover_side_effect),
        )

        requests = [("s1", 1.0, 1.0), ("s2", 2.0, 2.0)]
        mock_client = MagicMock(spec=httpx.AsyncClient)

        async def run_and_cancel() -> dict[str, str]:
            task = asyncio.create_task(
                d.discover_open_meteo_grids_for_stations(mock_client, requests)
            )
            await asyncio.sleep(0.02)
            task.cancel()
            return await task

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run_and_cancel())

        om_cache.set_grid_identifier.assert_any_call("s1", "1.0,1.0")
        marker = tmp_path / "marker.json"
        assert marker.exists()
        payload = json.loads(marker.read_text())
        assert payload["daemon_class"] == "TestDiscoverer"
        assert payload["provider"] == "open_meteo"
        assert payload["status"] == "cancelled"
        assert payload["total"] == 2
        assert payload["succeeded"] == 1


class TestOpenMeteoWeatherGridDiscovererTimeouts:
    """Tests for per-station and batch timeout handling."""

    def test_handles_per_station_timeout(
        self,
        discoverer: tuple[OpenMeteoWeatherGridDiscoverer, MagicMock, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        """Stations that exceed per-station timeout are logged and not cached."""
        d, om_cache, logger = discoverer
        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_grid_discovery._PER_STATION_DISCOVERY_TIMEOUT_S",
            new=0.05,
        )

        async def slow_discover(_c: object, lat: float, _lon: float) -> str:
            if lat == 1.0:
                await asyncio.sleep(0.01)
                return "1.0,1.0"
            await asyncio.sleep(0.1)
            return "2.0,2.0"

        mocker.patch(
            "cta_eta.data_collection.orchestration.weather_grid_discovery.discover_open_meteo_grid",
            new=AsyncMock(side_effect=slow_discover),
        )

        requests = [("s1", 1.0, 1.0), ("s2", 2.0, 2.0)]
        mock_client = MagicMock(spec=httpx.AsyncClient)

        result = asyncio.run(
            d.discover_open_meteo_grids_for_stations(mock_client, requests)
        )

        assert "s1" in result
        assert result["s1"] == "1.0,1.0"
        assert "s2" not in result
        om_cache.set_grid_identifier.assert_called_once_with("s1", "1.0,1.0")
        logger.warning.assert_any_call(
            "Open-Meteo grid discovery timed out for station s2",
            extra={"extra_fields": {"station_id": "s2", "timeout_seconds": 0.05}},
        )
