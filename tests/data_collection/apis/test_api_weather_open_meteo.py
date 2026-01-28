"""Test the Open-Meteo API client."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from cta_eta.data_collection.apis import api_weather_open_meteo
from cta_eta.data_collection.exceptions import APIResponseError

if TYPE_CHECKING:
    from collections.abc import Callable
    from unittest.mock import AsyncMock

    from pytest_mock import MockerFixture


pytestmark = pytest.mark.asyncio


@pytest.fixture
def open_meteo_client(mocker: MockerFixture) -> AsyncMock:
    """AsyncClient mock for Open-Meteo API calls."""
    return mocker.AsyncMock(spec=httpx.AsyncClient)


async def test_discover_open_meteo_grid_returns_actual_coordinates(
    open_meteo_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that the discover_open_meteo_grid function returns the actual coordinates."""
    # Arrange
    open_meteo_client.get.return_value = httpx_json_response(
        {"latitude": 41.715942, "longitude": -87.63699},
        200,
        f"{api_weather_open_meteo.OPEN_METEO_URL}?latitude=41.72&longitude=-87.62&current=temperature_2m&timezone=America/Chicago",
    )

    # Act
    grid_id = await api_weather_open_meteo.discover_open_meteo_grid(
        open_meteo_client, 41.72, -87.62
    )

    # Assert
    assert grid_id == "41.715942,-87.63699"
    open_meteo_client.get.assert_awaited_once()
    assert (
        open_meteo_client.get.call_args.args[0] == api_weather_open_meteo.OPEN_METEO_URL
    )
    assert (
        open_meteo_client.get.call_args.kwargs["params"]["timezone"]
        == "America/Chicago"
    )


async def test_get_open_meteo_current_defaults_missing_fields_and_converts_visibility(
    open_meteo_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that the get_open_meteo_current function defaults missing fields and converts visibility."""
    # Arrange
    lat = 41.88
    lon = -87.63
    grid_id = f"{lat},{lon}"
    open_meteo_client.get.return_value = httpx_json_response(
        {
            "latitude": lat,
            "longitude": lon,
            "current": {
                "time": "2026-01-14T21:00",
                "visibility": 10560.0,  # feet -> 2 miles
                # omit all other keys -> default 0.0
            },
        },
        200,
        f"{api_weather_open_meteo.OPEN_METEO_URL}?latitude={lat}&longitude={lon}&current=temperature_2m&timezone=America/Chicago&forecast_days=2&wind_speed_unit=mph&temperature_unit=fahrenheit&precipitation_unit=inch",
    )

    # Act
    current = await api_weather_open_meteo.get_open_meteo_current(
        open_meteo_client, grid_id
    )

    # Assert
    assert current["latitude"] == lat
    assert current["longitude"] == lon
    assert current["timestamp"] == "2026-01-14T21:00"
    assert current["visibility_mi"] == pytest.approx(2.0, abs=1e-6)
    assert current["snow_depth_in"] is None
    assert current["surface_pressure_hpa"] is None
    assert current["wind_gusts_mph"] is None
    assert current["apparent_temp_f"] is None
    assert current["rain_in"] is None
    assert current["showers_in"] is None
    assert current["snowfall_in"] is None

    open_meteo_client.get.assert_awaited_once()
    assert (
        open_meteo_client.get.call_args.args[0] == api_weather_open_meteo.OPEN_METEO_URL
    )
    assert open_meteo_client.get.call_args.kwargs["params"]["forecast_days"] == 2
    assert open_meteo_client.get.call_args.kwargs["params"]["wind_speed_unit"] == "mph"
    assert (
        open_meteo_client.get.call_args.kwargs["params"]["temperature_unit"]
        == "fahrenheit"
    )
    assert (
        open_meteo_client.get.call_args.kwargs["params"]["precipitation_unit"] == "inch"
    )
    assert (
        open_meteo_client.get.call_args.kwargs["params"]["current"]
        == "visibility,snow_depth,surface_pressure,wind_gusts_10m,apparent_temperature,rain,showers,snowfall"
    )


async def test_get_open_meteo_current_rejects_bad_grid_id(
    open_meteo_client: AsyncMock,
) -> None:
    """Test that the get_open_meteo_current function rejects a bad grid ID."""
    # Arrange
    # Act / Assert
    with pytest.raises(ValueError, match="Invalid grid ID: not-a-grid-id"):
        await api_weather_open_meteo.get_open_meteo_current(
            open_meteo_client, "not-a-grid-id"
        )


async def test_get_open_meteo_current_rejects_bad_grid_id_list(
    open_meteo_client: AsyncMock,
) -> None:
    """Test that the get_open_meteo_current function rejects a bad grid ID."""
    # Arrange
    # Act / Assert
    with pytest.raises(ValueError, match="Invalid grid ID: not_coord,not_coord"):
        await api_weather_open_meteo.get_open_meteo_current(
            open_meteo_client, "not_coord,not_coord"
        )


async def test_get_open_meteo_current_rejects_grid_id_with_too_many_commas(
    open_meteo_client: AsyncMock,
) -> None:
    """Test that the get_open_meteo_current function rejects grid IDs with 2+ commas."""
    # Arrange / Act / Assert
    with pytest.raises(ValueError, match=r"Invalid grid ID: 41\.88,-87\.63,0"):
        await api_weather_open_meteo.get_open_meteo_current(
            open_meteo_client, "41.88,-87.63,0"
        )


async def test_open_meteo_propagates_http_errors(
    open_meteo_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that the open_meteo_propagates_http_errors function propagates HTTP errors."""
    # Arrange
    fn_no_retry = getattr(
        api_weather_open_meteo.discover_open_meteo_grid,
        "__wrapped__",
        api_weather_open_meteo.discover_open_meteo_grid,
    )
    open_meteo_client.get.return_value = httpx_json_response(
        {"error": "nope"}, 429, "https://example.com"
    )

    # Act / Assert
    with pytest.raises(httpx.HTTPStatusError):
        await fn_no_retry(open_meteo_client, 41.88, -87.63)


async def test_get_open_meteo_current_propagates_http_errors_without_retry_delay(
    open_meteo_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that current endpoint propagates HTTP errors without retry delay."""
    # Arrange
    fn_no_retry = getattr(
        api_weather_open_meteo.get_open_meteo_current,
        "__wrapped__",
        api_weather_open_meteo.get_open_meteo_current,
    )
    open_meteo_client.get.return_value = httpx_json_response(
        {"error": "nope"}, 503, "https://example.com"
    )

    # Act / Assert
    with pytest.raises(httpx.HTTPStatusError):
        await fn_no_retry(open_meteo_client, "41.88,-87.63")


async def test_discover_open_meteo_grid_parse_error_non_numeric_lat_lon(
    open_meteo_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """discover_open_meteo_grid raises when API returns non-numeric latitude/longitude."""
    # Arrange
    open_meteo_client.get.return_value = httpx_json_response(
        {"latitude": "41.71", "longitude": -87.63},
        200,
        api_weather_open_meteo.OPEN_METEO_URL,
    )

    # Act / Assert
    with pytest.raises(APIResponseError, match=r"latitude.*or.*longitude.*not numeric"):
        await api_weather_open_meteo.discover_open_meteo_grid(
            open_meteo_client, 41.72, -87.62
        )


async def test_discover_open_meteo_grid_parse_error_propagates(
    open_meteo_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """discover_open_meteo_grid logs and re-raises when parsing discovery response fails."""
    # Arrange: missing "latitude" causes safe_get_nested to raise
    open_meteo_client.get.return_value = httpx_json_response(
        {"longitude": -87.63699},
        200,
        api_weather_open_meteo.OPEN_METEO_URL,
    )

    # Act / Assert
    with pytest.raises(APIResponseError, match="missing required field"):
        await api_weather_open_meteo.discover_open_meteo_grid(
            open_meteo_client, 41.72, -87.62
        )


async def test_get_open_meteo_current_parse_error_current_not_dict(
    open_meteo_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """get_open_meteo_current raises when API returns current that is not a dict."""
    # Arrange
    open_meteo_client.get.return_value = httpx_json_response(
        {
            "latitude": 41.88,
            "longitude": -87.63,
            "current": "not-a-dict",
        },
        200,
        api_weather_open_meteo.OPEN_METEO_URL,
    )

    # Act / Assert
    with pytest.raises(
        APIResponseError, match="Open-Meteo API response 'current' is not a dict"
    ):
        await api_weather_open_meteo.get_open_meteo_current(
            open_meteo_client, "41.88,-87.63"
        )


async def test_get_open_meteo_current_parse_error_lat_lon_not_numeric(
    open_meteo_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """get_open_meteo_current raises when latitude/longitude in response are not numeric."""
    # Arrange
    open_meteo_client.get.return_value = httpx_json_response(
        {
            "latitude": None,
            "longitude": -87.63,
            "current": {"time": "2026-01-14T21:00"},
        },
        200,
        api_weather_open_meteo.OPEN_METEO_URL,
    )

    # Act / Assert
    with pytest.raises(APIResponseError, match="Open-Meteo API response"):
        await api_weather_open_meteo.get_open_meteo_current(
            open_meteo_client, "41.88,-87.63"
        )


async def test_get_open_meteo_current_parse_error_missing_current_time(
    open_meteo_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """get_open_meteo_current raises when current.time is missing."""
    # Arrange
    open_meteo_client.get.return_value = httpx_json_response(
        {
            "latitude": 41.88,
            "longitude": -87.63,
            "current": {},
        },
        200,
        api_weather_open_meteo.OPEN_METEO_URL,
    )

    # Act / Assert
    with pytest.raises(APIResponseError, match="Open-Meteo API response"):
        await api_weather_open_meteo.get_open_meteo_current(
            open_meteo_client, "41.88,-87.63"
        )
