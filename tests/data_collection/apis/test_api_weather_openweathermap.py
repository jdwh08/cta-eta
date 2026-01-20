"""Test the OpenWeatherMap API client."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from cta_eta.data_collection.apis import api_weather_openweathermap

if TYPE_CHECKING:
    from collections.abc import Callable


def test_get_api_key_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that the _get_api_key function requires OPENWEATHERMAP_API_KEY."""
    # Arrange
    monkeypatch.delenv("OPENWEATHERMAP_API_KEY", raising=False)

    # Act / Assert
    with pytest.raises(
        ValueError, match="OPENWEATHERMAP_API_KEY environment variable not set"
    ):
        api_weather_openweathermap._get_api_key()


def test_get_api_key_returns_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that the _get_api_key function returns the env var value."""
    # Arrange
    monkeypatch.setenv("OPENWEATHERMAP_API_KEY", "test-key")

    # Act
    api_key = api_weather_openweathermap._get_api_key()

    # Assert
    assert api_key == "test-key"


def test_discover_openweathermap_grid_returns_actual_coordinates(
    monkeypatch: pytest.MonkeyPatch,
    mocker: pytest.MockFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that discover_openweathermap_grid returns the coordinates from response."""
    # Arrange
    monkeypatch.setenv("OPENWEATHERMAP_API_KEY", "test-key")
    client = mocker.Mock(spec=httpx.Client)
    latitude = 41.72
    longitude = -87.62

    client.get.return_value = httpx_json_response(
        {"coord": {"lat": 41.715942, "lon": -87.63699}},
        200,
        f"{api_weather_openweathermap.CURRENT_WEATHER_URL}?lat={latitude}&lon={longitude}&appid=test-key&units=imperial",
    )

    # Act
    grid_id = api_weather_openweathermap.discover_openweathermap_grid(
        client, latitude, longitude
    )

    # Assert
    assert grid_id == "41.715942,-87.63699"
    client.get.assert_called_once()
    assert (
        client.get.call_args.args[0] == api_weather_openweathermap.CURRENT_WEATHER_URL
    )
    assert client.get.call_args.kwargs["params"] == {
        "lat": latitude,
        "lon": longitude,
        "appid": "test-key",
        "units": "imperial",
    }


def test_get_openweathermap_current_defaults_missing_fields_and_converts_visibility(
    monkeypatch: pytest.MonkeyPatch,
    mocker: pytest.MockFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test get_openweathermap_current defaults optional fields and converts visibility."""
    # Arrange
    monkeypatch.setenv("OPENWEATHERMAP_API_KEY", "test-key")
    client = mocker.Mock(spec=httpx.Client)
    grid_id = "41.88,-87.63"

    client.get.return_value = httpx_json_response(
        {
            "coord": {"lat": 41.88, "lon": -87.63},
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
            # visibility omitted -> 0.0mi
            # wind.deg omitted -> 0
            # wind.gust omitted -> 0.0
            # clouds omitted -> 0
        },
        200,
        "https://example.com",
    )

    # Act
    current = api_weather_openweathermap.get_openweathermap_current(client, grid_id)

    # Assert
    assert current["latitude"] == 41.88
    assert current["longitude"] == -87.63
    assert current["timestamp"] == 1736898000
    assert current["temperature_f"] == 10.0
    assert current["visibility_mi"] == 0.0
    assert current["wind_direction_deg"] == 0
    assert current["wind_gust_mph"] == 0.0
    assert current["cloud_cover_pct"] == 0
    assert current["weather_main"] == "Clouds"
    assert current["weather_desc"] == "overcast clouds"

    client.get.assert_called_once()
    assert (
        client.get.call_args.args[0] == api_weather_openweathermap.CURRENT_WEATHER_URL
    )
    assert client.get.call_args.kwargs["params"]["units"] == "imperial"
    assert client.get.call_args.kwargs["params"]["appid"] == "test-key"


def test_get_openweathermap_current_rejects_bad_grid_id(
    monkeypatch: pytest.MonkeyPatch,
    mocker: pytest.MockFixture,
) -> None:
    """Test that get_openweathermap_current rejects invalid grid IDs."""
    # Arrange
    monkeypatch.setenv("OPENWEATHERMAP_API_KEY", "test-key")
    client = mocker.Mock(spec=httpx.Client)

    # Act / Assert
    with pytest.raises(ValueError):
        api_weather_openweathermap.get_openweathermap_current(client, "not-a-grid-id")


def test_get_openweathermap_forecast_hourly_converts_pop_and_visibility_and_defaults(
    monkeypatch: pytest.MonkeyPatch,
    mocker: pytest.MockFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test get_openweathermap_forecast_hourly normalizes forecast fields correctly."""
    # Arrange
    monkeypatch.setenv("OPENWEATHERMAP_API_KEY", "test-key")
    client = mocker.Mock(spec=httpx.Client)
    grid_id = "41.88,-87.63"

    client.get.return_value = httpx_json_response(
        {
            "city": {"coord": {"lat": 41.88, "lon": -87.63}},
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
                    "wind": {"speed": 12.0, "deg": 270},
                    "weather": [{"main": "Snow", "description": "light snow"}],
                    "visibility": 1609.34,  # meters -> 1 mile
                    "clouds": {"all": 75},
                    "pop": 0.25,  # -> 25%
                }
            ],
        },
        200,
        "https://example.com",
    )

    # Act
    forecast = api_weather_openweathermap.get_openweathermap_forecast_hourly(
        client, grid_id
    )

    # Assert
    assert forecast["latitude"] == 41.88
    assert forecast["longitude"] == -87.63
    assert forecast["timestamp"] == 1736898000
    assert forecast["dt_txt"] == "2026-01-14 21:00:00"
    assert forecast["visibility_mi"] == pytest.approx(1.0, abs=1e-6)
    assert forecast["cloud_cover_pct"] == 75
    assert forecast["prob_precip_pct"] == pytest.approx(25.0, abs=1e-6)
    assert forecast["wind_direction_deg"] == 270
    assert forecast["wind_gust_mph"] == 0.0

    client.get.assert_called_once()
    assert client.get.call_args.args[0] == api_weather_openweathermap.FORECAST_URL
    assert client.get.call_args.kwargs["params"]["cnt"] == 1
    assert client.get.call_args.kwargs["params"]["units"] == "imperial"
    assert client.get.call_args.kwargs["params"]["appid"] == "test-key"


def test_openweathermap_propagates_http_errors_without_retry_delay(
    monkeypatch: pytest.MonkeyPatch,
    mocker: pytest.MockFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that OpenWeatherMap functions propagate HTTP errors without retry delay."""
    # Arrange
    # Avoid stamina retry/backoff by calling through one wrapper level.
    # (Decorator order: stamina.retry(log_api_call(original)))
    fn_no_retry = api_weather_openweathermap.discover_openweathermap_grid.__wrapped__  # type: ignore[attr-defined]

    monkeypatch.setenv("OPENWEATHERMAP_API_KEY", "test-key")
    client = mocker.Mock(spec=httpx.Client)
    client.get.return_value = httpx_json_response(
        {"error": "nope"},
        503,
        f"{api_weather_openweathermap.CURRENT_WEATHER_URL}?lat=41.88&lon=-87.63",
    )

    # Act / Assert
    with pytest.raises(httpx.HTTPStatusError):
        fn_no_retry(client, 41.88, -87.63)
