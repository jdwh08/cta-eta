"""Sync tests for the Open-Meteo API client.

These exist primarily to cover the non-async code paths in `api_weather_open_meteo`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from cta_eta.data_collection.apis import api_weather_open_meteo

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture


def test_discover_open_meteo_grid_returns_actual_coordinates_sync(
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that `discover_open_meteo_grid` returns the actual coordinates."""
    # Arrange
    client = mocker.Mock(spec=httpx.Client)
    client.get.return_value = httpx_json_response(
        {"latitude": 41.715942, "longitude": -87.63699},
        200,
        f"{api_weather_open_meteo.OPEN_METEO_URL}?latitude=41.72&longitude=-87.62&current=temperature_2m&timezone=America/Chicago",
    )

    # Act
    grid_id = api_weather_open_meteo.discover_open_meteo_grid(client, 41.72, -87.62)

    # Assert
    assert grid_id == "41.715942,-87.63699"
    client.get.assert_called_once()
    assert client.get.call_args.args[0] == api_weather_open_meteo.OPEN_METEO_URL
    assert client.get.call_args.kwargs["params"]["timezone"] == "America/Chicago"


def test_get_open_meteo_current_defaults_missing_fields_and_converts_visibility_sync(
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that `get_open_meteo_current` defaults missing fields and converts visibility."""
    # Arrange
    client = mocker.Mock(spec=httpx.Client)
    lat = 41.88
    lon = -87.63
    grid_id = f"{lat},{lon}"
    client.get.return_value = httpx_json_response(
        {
            "latitude": lat,
            "longitude": lon,
            "current": {"time": "2026-01-14T21:00", "visibility": 10560.0},
        },
        200,
        "https://example.com",
    )

    # Act
    current = api_weather_open_meteo.get_open_meteo_current(client, grid_id)

    # Assert
    assert current["latitude"] == lat
    assert current["longitude"] == lon
    assert current["timestamp"] == "2026-01-14T21:00"
    assert current["visibility_mi"] == pytest.approx(2.0, abs=1e-6)
    assert current["snow_depth_in"] == 0.0
    assert current["surface_pressure_hpa"] == 0.0
    assert current["wind_gusts_mph"] == 0.0
    assert current["apparent_temp_f"] == 0.0
    assert current["rain_in"] == 0.0
    assert current["showers_in"] == 0.0
    assert current["snowfall_in"] == 0.0


def test_get_open_meteo_current_rejects_bad_grid_id_sync(
    mocker: MockerFixture,
) -> None:
    """Test that `get_open_meteo_current` rejects a bad grid ID."""
    # Arrange
    client = mocker.Mock(spec=httpx.Client)

    # Act / Assert
    with pytest.raises(ValueError, match="Invalid grid ID: not-a-grid-id"):
        api_weather_open_meteo.get_open_meteo_current(client, "not-a-grid-id")


def test_get_open_meteo_current_propagates_http_errors_without_retry_delay_sync(
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that `get_open_meteo_current` propagates HTTP errors without retry delay."""
    # Arrange
    fn_no_retry = getattr(
        api_weather_open_meteo.get_open_meteo_current,
        "__wrapped__",
        api_weather_open_meteo.get_open_meteo_current,
    )
    client = mocker.Mock(spec=httpx.Client)
    client.get.return_value = httpx_json_response(
        {"error": "nope"}, 503, "https://example.com"
    )

    # Act / Assert
    with pytest.raises(httpx.HTTPStatusError):
        fn_no_retry(client, "41.88,-87.63")
