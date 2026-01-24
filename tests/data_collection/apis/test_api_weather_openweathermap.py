"""Test the OpenWeatherMap API client."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from cta_eta.data_collection.apis import api_weather_openweathermap

if TYPE_CHECKING:
    from collections.abc import Callable
    from unittest.mock import AsyncMock

    from pytest_mock import MockerFixture


pytestmark = pytest.mark.asyncio


@pytest.fixture
def openweathermap_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required OpenWeatherMap API env vars for the duration of the test."""
    monkeypatch.setenv("OPENWEATHERMAP_API_KEY", "test-key")


@pytest.fixture
def owm_client(mocker: MockerFixture) -> AsyncMock:
    """AsyncClient mock for OpenWeatherMap API calls."""
    return mocker.AsyncMock(spec=httpx.AsyncClient)


async def test_get_api_key_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that the _get_api_key function requires OPENWEATHERMAP_API_KEY."""
    # Arrange
    monkeypatch.delenv("OPENWEATHERMAP_API_KEY", raising=False)

    # Act / Assert
    with pytest.raises(
        ValueError, match="OPENWEATHERMAP_API_KEY environment variable not set"
    ):
        api_weather_openweathermap._get_api_key()


async def test_get_api_key_returns_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that the _get_api_key function returns the env var value."""
    # Arrange
    monkeypatch.setenv("OPENWEATHERMAP_API_KEY", "test-key")

    # Act
    api_key = api_weather_openweathermap._get_api_key()

    # Assert
    assert api_key == "test-key"


async def test_discover_openweathermap_grid_returns_actual_coordinates(
    openweathermap_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that discover_openweathermap_grid returns the coordinates from response."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    latitude = 41.72
    longitude = -87.62

    client.get.return_value = httpx_json_response(
        {"coord": {"lat": 41.715942, "lon": -87.63699}},
        200,
        f"{api_weather_openweathermap.CURRENT_WEATHER_URL}?lat={latitude}&lon={longitude}&appid=test-key&units=imperial",
    )

    # Act
    grid_id = await api_weather_openweathermap.discover_openweathermap_grid(
        client, latitude, longitude
    )

    # Assert
    assert grid_id == "41.715942,-87.63699"
    client.get.assert_awaited_once()
    assert (
        client.get.call_args.args[0] == api_weather_openweathermap.CURRENT_WEATHER_URL
    )
    assert client.get.call_args.kwargs["params"] == {
        "lat": latitude,
        "lon": longitude,
        "appid": "test-key",
        "units": "imperial",
    }


async def test_get_openweathermap_current_defaults_missing_fields_and_converts_visibility(
    openweathermap_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test get_openweathermap_current defaults optional fields and converts visibility."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    grid_id = "41.88,-87.63"
    lat = 41.88
    lon = -87.63
    timestamp = 1736898000
    temperature_f = 10.0

    client.get.return_value = httpx_json_response(
        {
            "coord": {"lat": lat, "lon": lon},
            "dt": timestamp,
            "main": {
                "temp": temperature_f,
                "feels_like": 5.0,
                "temp_min": 8.0,
                "temp_max": 12.0,
                "pressure": 1013,
                "humidity": 80,
            },
            "wind": {"speed": 12.0},
            "weather": [{"main": "Clouds", "description": "overcast clouds"}],
            # visibility omitted -> 0.0mi
            # wind.deg omitted -> 0
            # wind.gust omitted -> 0.0
            # clouds omitted -> 0
        },
        200,
        "https://example.com",
    )

    # Act
    current = await api_weather_openweathermap.get_openweathermap_current(
        client, grid_id
    )

    # Assert
    assert current["latitude"] == lat
    assert current["longitude"] == lon
    assert current["timestamp"] == timestamp
    assert current["temperature_f"] == temperature_f
    assert current["visibility_mi"] is None
    assert current["wind_direction_deg"] is None
    assert current["wind_gust_mph"] is None
    assert current["cloud_cover_pct"] is None
    assert current["weather_main"] == "Clouds"
    assert current["weather_desc"] == "overcast clouds"

    client.get.assert_awaited_once()
    assert (
        client.get.call_args.args[0] == api_weather_openweathermap.CURRENT_WEATHER_URL
    )
    assert client.get.call_args.kwargs["params"]["units"] == "imperial"
    assert client.get.call_args.kwargs["params"]["appid"] == "test-key"


async def test_get_openweathermap_current_rejects_bad_grid_id(
    openweathermap_env: None,  # noqa: ARG001
    mocker: MockerFixture,
) -> None:
    """Test that get_openweathermap_current rejects invalid grid IDs."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)

    # Act / Assert
    with pytest.raises(ValueError, match="Invalid grid ID: not-a-grid-id"):
        await api_weather_openweathermap.get_openweathermap_current(
            client, "not-a-grid-id"
        )


async def test_get_openweathermap_current_rejects_non_numeric_grid_id(
    openweathermap_env: None,  # noqa: ARG001
    mocker: MockerFixture,
) -> None:
    """Test that get_openweathermap_current rejects grid IDs that can't be parsed to floats."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)

    # Act / Assert
    with pytest.raises(ValueError, match="Invalid grid ID: not_coord,not_coord"):
        await api_weather_openweathermap.get_openweathermap_current(
            client, "not_coord,not_coord"
        )


async def test_get_openweathermap_current_converts_visibility_meters_to_miles(
    openweathermap_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that OpenWeatherMap visibility meters are converted to miles."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    grid_id = "41.88,-87.63"
    lat = 41.88
    lon = -87.63
    visibility_m = 1609.34

    client.get.return_value = httpx_json_response(
        {
            "coord": {"lat": lat, "lon": lon},
            "dt": 1736898000,
            "main": {
                "temp": 10.0,
                "feels_like": 5.0,
                "temp_min": 8.0,
                "temp_max": 12.0,
                "pressure": 1013,
                "humidity": 80,
            },
            "wind": {"speed": 12.0},
            "weather": [{"main": "Clouds", "description": "overcast clouds"}],
            "visibility": visibility_m,
        },
        200,
        "https://example.com",
    )

    # Act
    current = await api_weather_openweathermap.get_openweathermap_current(
        client, grid_id
    )

    # Assert
    assert current["visibility_mi"] == pytest.approx(1.0, abs=1e-6)


async def test_get_openweathermap_forecast_hourly_converts_pop_and_visibility_and_defaults(
    openweathermap_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test get_openweathermap_forecast_hourly normalizes forecast fields correctly."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    grid_id = "41.88,-87.63"
    lat = 41.88
    lon = -87.63
    timestamp = 1736898000
    cloud_cover_pct = 75
    wind_direction_deg = 270

    client.get.return_value = httpx_json_response(
        {
            "city": {"coord": {"lat": lat, "lon": lon}},
            "list": [
                {
                    "dt": timestamp,
                    "dt_txt": "2026-01-14 21:00:00",
                    "main": {
                        "temp": 10.0,
                        "feels_like": 5.0,
                        "temp_min": 8.0,
                        "temp_max": 12.0,
                        "pressure": 1013,
                        "humidity": 80,
                    },
                    "wind": {"speed": 12.0, "deg": wind_direction_deg},
                    "weather": [{"main": "Snow", "description": "light snow"}],
                    "visibility": 1609.34,  # meters -> 1 mile
                    "clouds": {"all": cloud_cover_pct},
                    "pop": 0.25,  # -> 25%
                }
            ],
        },
        200,
        "https://example.com",
    )

    # Act
    forecast = await api_weather_openweathermap.get_openweathermap_forecast_hourly(
        client, grid_id
    )

    # Assert
    assert forecast["latitude"] == lat
    assert forecast["longitude"] == lon
    assert forecast["timestamp"] == timestamp
    assert forecast["dt_txt"] == "2026-01-14 21:00:00"
    assert forecast["visibility_mi"] == pytest.approx(1.0, abs=1e-6)
    assert forecast["cloud_cover_pct"] == cloud_cover_pct
    assert forecast["prob_precip_pct"] == pytest.approx(25.0, abs=1e-6)
    assert forecast["wind_direction_deg"] == wind_direction_deg
    assert forecast["wind_gust_mph"] is None

    client.get.assert_awaited_once()
    assert client.get.call_args.args[0] == api_weather_openweathermap.FORECAST_URL
    assert client.get.call_args.kwargs["params"]["cnt"] == 1
    assert client.get.call_args.kwargs["params"]["units"] == "imperial"
    assert client.get.call_args.kwargs["params"]["appid"] == "test-key"


async def test_get_openweathermap_forecast_hourly_defaults_missing_optional_fields(
    openweathermap_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test forecast defaults when pop/visibility/clouds/wind.gust are missing."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    grid_id = "41.88,-87.63"
    lat = 41.88
    lon = -87.63

    client.get.return_value = httpx_json_response(
        {
            "city": {"coord": {"lat": lat, "lon": lon}},
            "list": [
                {
                    "dt": 1736898000,
                    "dt_txt": "2026-01-14 21:00:00",
                    "main": {
                        "temp": 10.0,
                        "feels_like": 5.0,
                        "temp_min": 8.0,
                        "temp_max": 12.0,
                        "pressure": 1013,
                        "humidity": 80,
                    },
                    "wind": {"speed": 12.0},
                    "weather": [{"main": "Clouds", "description": "overcast clouds"}],
                }
            ],
        },
        200,
        "https://example.com",
    )

    # Act
    forecast = await api_weather_openweathermap.get_openweathermap_forecast_hourly(
        client, grid_id
    )

    # Assert
    assert forecast["visibility_mi"] is None
    assert forecast["prob_precip_pct"] is None
    assert forecast["cloud_cover_pct"] is None
    assert forecast["wind_gust_mph"] is None
    assert forecast["wind_direction_deg"] is None


async def test_get_openweathermap_forecast_hourly_rejects_non_numeric_grid_id(
    openweathermap_env: None,  # noqa: ARG001
    mocker: MockerFixture,
) -> None:
    """Test that forecast rejects grid IDs that can't be parsed to floats."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)

    # Act / Assert
    with pytest.raises(ValueError, match="Invalid grid ID: not_coord,not_coord"):
        await api_weather_openweathermap.get_openweathermap_forecast_hourly(
            client, "not_coord,not_coord"
        )


async def test_openweathermap_propagates_http_errors_without_retry_delay(
    openweathermap_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that OpenWeatherMap functions propagate HTTP errors without retry delay."""
    # Arrange
    # Avoid stamina retry/backoff by calling through one wrapper level.
    # (Decorator order: stamina.retry(log_api_call(original)))
    fn_no_retry = getattr(
        api_weather_openweathermap.discover_openweathermap_grid,
        "__wrapped__",
        api_weather_openweathermap.discover_openweathermap_grid,
    )

    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = httpx_json_response(
        {"error": "nope"},
        503,
        f"{api_weather_openweathermap.CURRENT_WEATHER_URL}?lat=41.88&lon=-87.63",
    )

    # Act / Assert
    with pytest.raises(httpx.HTTPStatusError):
        await fn_no_retry(client, 41.88, -87.63)


async def test_discover_openweathermap_grid_parse_error_coord_not_dict(
    openweathermap_env: None,  # noqa: ARG001
    owm_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """discover_openweathermap_grid raises when API returns coord that is not a dict."""
    # Arrange
    owm_client.get.return_value = httpx_json_response(
        {"coord": "not-a-dict"},
        200,
        api_weather_openweathermap.CURRENT_WEATHER_URL,
    )

    # Act / Assert
    with pytest.raises(TypeError, match="'coord' is not a dict"):
        await api_weather_openweathermap.discover_openweathermap_grid(
            owm_client, 41.72, -87.62
        )


async def test_discover_openweathermap_grid_parse_error_coord_lat_lon_none(
    openweathermap_env: None,  # noqa: ARG001
    owm_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """discover_openweathermap_grid raises when coord.lat or coord.lon is missing."""
    # Arrange
    owm_client.get.return_value = httpx_json_response(
        {"coord": {"lat": 41.71}},
        200,
        api_weather_openweathermap.CURRENT_WEATHER_URL,
    )

    # Act / Assert
    with pytest.raises(TypeError, match=r"missing 'coord\.lat' or 'coord\.lon'"):
        await api_weather_openweathermap.discover_openweathermap_grid(
            owm_client, 41.72, -87.62
        )


async def test_discover_openweathermap_grid_parse_error_coord_lat_lon_not_numeric(
    openweathermap_env: None,  # noqa: ARG001
    owm_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """discover_openweathermap_grid raises when coord.lat or coord.lon is not numeric."""
    # Arrange
    owm_client.get.return_value = httpx_json_response(
        {"coord": {"lat": "41.71", "lon": -87.63}},
        200,
        api_weather_openweathermap.CURRENT_WEATHER_URL,
    )

    # Act / Assert
    with pytest.raises(TypeError, match=r"coord\.lat.*or.*coord\.lon.*is not numeric"):
        await api_weather_openweathermap.discover_openweathermap_grid(
            owm_client, 41.72, -87.62
        )


async def test_discover_openweathermap_grid_parse_error_propagates(
    openweathermap_env: None,  # noqa: ARG001
    owm_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """discover_openweathermap_grid logs and re-raises when parse fails."""
    # Arrange: missing "coord" causes safe_get_nested to raise
    owm_client.get.return_value = httpx_json_response(
        {},
        200,
        api_weather_openweathermap.CURRENT_WEATHER_URL,
    )

    # Act / Assert
    with pytest.raises(TypeError, match="missing required field"):
        await api_weather_openweathermap.discover_openweathermap_grid(
            owm_client, 41.72, -87.62
        )


async def test_get_openweathermap_current_parse_error_coord_not_dict(
    openweathermap_env: None,  # noqa: ARG001
    owm_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """get_openweathermap_current raises when coord is not a dict."""
    # Arrange
    owm_client.get.return_value = httpx_json_response(
        {
            "coord": [],
            "main": {"temp": 10},
            "wind": {},
            "weather": [{"main": "x", "description": "x"}],
            "dt": 1736898000,
        },
        200,
        api_weather_openweathermap.CURRENT_WEATHER_URL,
    )

    # Act / Assert
    with pytest.raises(ValueError, match="structure unexpected"):
        await api_weather_openweathermap.get_openweathermap_current(
            owm_client, "41.88,-87.63"
        )


async def test_get_openweathermap_current_parse_error_main_not_dict(
    openweathermap_env: None,  # noqa: ARG001
    owm_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """get_openweathermap_current raises when main is not a dict."""
    # Arrange
    owm_client.get.return_value = httpx_json_response(
        {
            "coord": {"lat": 41.88, "lon": -87.63},
            "main": "x",
            "wind": {},
            "weather": [{"main": "x", "description": "x"}],
            "dt": 1736898000,
        },
        200,
        api_weather_openweathermap.CURRENT_WEATHER_URL,
    )

    # Act / Assert
    with pytest.raises(ValueError, match="structure unexpected"):
        await api_weather_openweathermap.get_openweathermap_current(
            owm_client, "41.88,-87.63"
        )


async def test_get_openweathermap_current_parse_error_wind_not_dict(
    openweathermap_env: None,  # noqa: ARG001
    owm_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """get_openweathermap_current raises when wind is not a dict."""
    # Arrange
    owm_client.get.return_value = httpx_json_response(
        {
            "coord": {"lat": 41.88, "lon": -87.63},
            "main": {"temp": 10},
            "wind": None,
            "weather": [{"main": "x", "description": "x"}],
            "dt": 1736898000,
        },
        200,
        api_weather_openweathermap.CURRENT_WEATHER_URL,
    )

    # Act / Assert
    with pytest.raises(ValueError, match="structure unexpected"):
        await api_weather_openweathermap.get_openweathermap_current(
            owm_client, "41.88,-87.63"
        )


async def test_get_openweathermap_current_parse_error_weather_missing_or_empty(
    openweathermap_env: None,  # noqa: ARG001
    owm_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """get_openweathermap_current raises when weather is missing or empty."""
    # Arrange
    owm_client.get.return_value = httpx_json_response(
        {
            "coord": {"lat": 41.88, "lon": -87.63},
            "main": {"temp": 10},
            "wind": {},
            "weather": [],
            "dt": 1736898000,
        },
        200,
        api_weather_openweathermap.CURRENT_WEATHER_URL,
    )

    # Act / Assert
    with pytest.raises(ValueError, match="structure unexpected"):
        await api_weather_openweathermap.get_openweathermap_current(
            owm_client, "41.88,-87.63"
        )


async def test_get_openweathermap_current_parse_error_weather0_not_dict(
    openweathermap_env: None,  # noqa: ARG001
    owm_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """get_openweathermap_current raises when weather[0] is not a dict."""
    # Arrange
    owm_client.get.return_value = httpx_json_response(
        {
            "coord": {"lat": 41.88, "lon": -87.63},
            "main": {"temp": 10},
            "wind": {},
            "weather": ["x"],
            "dt": 1736898000,
        },
        200,
        api_weather_openweathermap.CURRENT_WEATHER_URL,
    )

    # Act / Assert
    with pytest.raises(ValueError, match="structure unexpected"):
        await api_weather_openweathermap.get_openweathermap_current(
            owm_client, "41.88,-87.63"
        )


async def test_get_openweathermap_current_parse_error_dt_missing(
    openweathermap_env: None,  # noqa: ARG001
    owm_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """get_openweathermap_current raises when dt is missing."""
    # Arrange
    owm_client.get.return_value = httpx_json_response(
        {
            "coord": {"lat": 41.88, "lon": -87.63},
            "main": {"temp": 10},
            "wind": {},
            "weather": [{"main": "x", "description": "x"}],
        },
        200,
        api_weather_openweathermap.CURRENT_WEATHER_URL,
    )

    # Act / Assert
    with pytest.raises(ValueError, match="structure unexpected"):
        await api_weather_openweathermap.get_openweathermap_current(
            owm_client, "41.88,-87.63"
        )


async def test_get_openweathermap_forecast_hourly_rejects_grid_id_too_many_commas(
    openweathermap_env: None,  # noqa: ARG001
    owm_client: AsyncMock,
) -> None:
    """get_openweathermap_forecast_hourly rejects grid_id with more than one comma."""
    # Arrange / Act / Assert
    with pytest.raises(ValueError, match=r"Invalid grid ID: 41\.88,-87\.63,0"):
        await api_weather_openweathermap.get_openweathermap_forecast_hourly(
            owm_client, "41.88,-87.63,0"
        )


async def test_get_openweathermap_forecast_hourly_parse_error_list_missing_or_empty(
    openweathermap_env: None,  # noqa: ARG001
    owm_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """get_openweathermap_forecast_hourly raises when list is missing or empty."""
    # Arrange
    owm_client.get.return_value = httpx_json_response(
        {"list": [], "city": {"coord": {"lat": 41.88, "lon": -87.63}}},
        200,
        api_weather_openweathermap.FORECAST_URL,
    )

    # Act / Assert
    with pytest.raises(ValueError, match="structure unexpected"):
        await api_weather_openweathermap.get_openweathermap_forecast_hourly(
            owm_client, "41.88,-87.63"
        )


async def test_get_openweathermap_forecast_hourly_parse_error_list0_not_dict(
    openweathermap_env: None,  # noqa: ARG001
    owm_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """get_openweathermap_forecast_hourly raises when list[0] is not a dict."""
    # Arrange
    owm_client.get.return_value = httpx_json_response(
        {
            "list": ["x"],
            "city": {"coord": {"lat": 41.88, "lon": -87.63}},
        },
        200,
        api_weather_openweathermap.FORECAST_URL,
    )

    # Act / Assert
    with pytest.raises(ValueError, match="structure unexpected"):
        await api_weather_openweathermap.get_openweathermap_forecast_hourly(
            owm_client, "41.88,-87.63"
        )


async def test_get_openweathermap_forecast_hourly_parse_error_main_not_dict(
    openweathermap_env: None,  # noqa: ARG001
    owm_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """get_openweathermap_forecast_hourly raises when list[0].main is not a dict."""
    # Arrange
    owm_client.get.return_value = httpx_json_response(
        {
            "list": [
                {
                    "main": 1,
                    "wind": {},
                    "weather": [{"main": "x", "description": "x"}],
                }
            ],
            "city": {"coord": {"lat": 41.88, "lon": -87.63}},
        },
        200,
        api_weather_openweathermap.FORECAST_URL,
    )

    # Act / Assert
    with pytest.raises(ValueError, match="structure unexpected"):
        await api_weather_openweathermap.get_openweathermap_forecast_hourly(
            owm_client, "41.88,-87.63"
        )


async def test_get_openweathermap_forecast_hourly_parse_error_city_not_dict(
    openweathermap_env: None,  # noqa: ARG001
    owm_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """get_openweathermap_forecast_hourly raises when city is not a dict."""
    # Arrange
    owm_client.get.return_value = httpx_json_response(
        {
            "list": [
                {
                    "dt": 1736898000,
                    "main": {"temp": 10},
                    "wind": {},
                    "weather": [{"main": "x", "description": "x"}],
                }
            ],
            "city": "x",
        },
        200,
        api_weather_openweathermap.FORECAST_URL,
    )

    # Act / Assert
    with pytest.raises(ValueError, match="structure unexpected"):
        await api_weather_openweathermap.get_openweathermap_forecast_hourly(
            owm_client, "41.88,-87.63"
        )


async def test_get_openweathermap_forecast_hourly_parse_error_city_coord_invalid(
    openweathermap_env: None,  # noqa: ARG001
    owm_client: AsyncMock,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """get_openweathermap_forecast_hourly raises when city.coord is invalid."""
    # Arrange: city.coord not a dict
    owm_client.get.return_value = httpx_json_response(
        {
            "list": [
                {
                    "dt": 1736898000,
                    "main": {"temp": 10},
                    "wind": {},
                    "weather": [{"main": "x", "description": "x"}],
                }
            ],
            "city": {"coord": "x"},
        },
        200,
        api_weather_openweathermap.FORECAST_URL,
    )

    # Act / Assert
    with pytest.raises(ValueError, match="structure unexpected"):
        await api_weather_openweathermap.get_openweathermap_forecast_hourly(
            owm_client, "41.88,-87.63"
        )
