"""Test the NWS API client."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from cta_eta.data_collection.apis import api_weather_nws

if TYPE_CHECKING:
    from collections.abc import Callable


def test_get_auth_header_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that the get_auth_header function requires the NWS_APP_NAME and NWS_EMAIL environment variables."""
    # Arrange
    monkeypatch.delenv("NWS_APP_NAME", raising=False)
    monkeypatch.delenv("NWS_EMAIL", raising=False)

    # Act / Assert
    with pytest.raises(ValueError, match="NWS_APP_NAME and NWS_EMAIL must be set"):
        api_weather_nws._get_auth_header()


def test_get_auth_header_formats_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that the get_auth_header function formats the user agent correctly."""
    # Arrange
    monkeypatch.setenv("NWS_APP_NAME", "cta-eta")
    monkeypatch.setenv("NWS_EMAIL", "data@example.com")

    # Act
    header = api_weather_nws._get_auth_header()

    # Assert
    assert header == {"User-Agent": "(cta-eta, data@example.com)"}


def test_get_nws_forecast_url_calls_points_api(
    monkeypatch: pytest.MonkeyPatch,
    mocker: pytest.MockFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that the get_nws_forecast_url function calls the points API correctly."""
    # Arrange
    monkeypatch.setenv("NWS_APP_NAME", "cta-eta")
    monkeypatch.setenv("NWS_EMAIL", "data@example.com")

    client = mocker.Mock(spec=httpx.Client)
    latitude = 41.88
    longitude = -87.63
    forecast_hourly = "https://api.weather.gov/gridpoints/LOT/85,67/forecast/hourly"
    client.get.return_value = httpx_json_response(
        {"properties": {"forecastHourly": forecast_hourly}},
        200,
        f"{api_weather_nws.NWS_POINTS_URL}/{latitude},{longitude}",
    )

    # Act
    result = api_weather_nws.get_nws_forecast_url(client, latitude, longitude)

    # Assert
    assert result == forecast_hourly
    client.get.assert_called_once()
    called_url = client.get.call_args.args[0]
    called_headers = client.get.call_args.kwargs["headers"]
    assert called_url == f"{api_weather_nws.NWS_POINTS_URL}/{latitude},{longitude}"
    assert called_headers == {"User-Agent": "(cta-eta, data@example.com)"}


def test_discover_nws_grid_parses_grid_id(mocker: pytest.MockFixture) -> None:
    """Test that the discover_nws_grid function parses the grid ID correctly."""
    # Arrange
    client = mocker.Mock(spec=httpx.Client)
    mocker.patch.object(
        api_weather_nws,
        "get_nws_forecast_url",
        return_value="https://api.weather.gov/gridpoints/LOT/85,67/forecast/hourly",
    )

    # Act
    grid_id = api_weather_nws.discover_nws_grid(client, 41.88, -87.63)

    # Assert
    assert grid_id == "LOT/85,67"


def test_discover_nws_grid_rejects_unexpected_url(mocker: pytest.MockFixture) -> None:
    """Test that the discover_nws_grid function rejects unexpected URLs."""
    # Arrange
    client = mocker.Mock(spec=httpx.Client)
    mocker.patch.object(
        api_weather_nws, "get_nws_forecast_url", return_value="https://bad.example/x"
    )

    # Act / Assert
    with pytest.raises(ValueError, match="Unexpected NWS forecast URL format"):
        api_weather_nws.discover_nws_grid(client, 41.88, -87.63)


def test_get_nws_hourly_forecast_converts_units_and_defaults(
    monkeypatch: pytest.MonkeyPatch,
    mocker: pytest.MockFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that the get_nws_hourly_forecast function converts units and defaults correctly."""
    # Arrange
    monkeypatch.setenv("NWS_APP_NAME", "cta-eta")
    monkeypatch.setenv("NWS_EMAIL", "data@example.com")

    client = mocker.Mock(spec=httpx.Client)
    grid_id = "LOT/76,73"
    forecast_url = f"https://api.weather.gov/gridpoints/{grid_id}/forecast/hourly"

    payload = {
        "properties": {
            "periods": [
                {
                    "startTime": "2026-01-14T21:00:00-06:00",
                    "endTime": "2026-01-14T22:00:00-06:00",
                    "temperature": 0,  # Celsius -> 32F
                    "temperatureUnit": "wmoUnit:degC",
                    "probabilityOfPrecipitation": {"value": None},  # -> 0.0
                    "dewpoint": {"value": 0.0, "unitCode": "wmoUnit:degC"},  # -> 32F
                    "relativeHumidity": {"value": None},  # -> 0.0
                    "windSpeed": "20 mph",
                    "windDirection": "NW",
                    "shortForecast": "Clear",
                }
            ]
        }
    }
    client.get.return_value = httpx_json_response(payload, 200, forecast_url)

    # Act
    weather = api_weather_nws.get_nws_hourly_forecast(client, grid_id)

    # Assert
    assert weather["start_time"] == "2026-01-14T21:00:00-06:00"
    assert weather["end_time"] == "2026-01-14T22:00:00-06:00"
    assert weather["temperature_f"] == 32  # noqa: PLR2004
    assert weather["dewpoint_f"] == 32.0  # noqa: PLR2004
    assert weather["prob_precip_pct"] == 0.0
    assert weather["humidity_pct"] == 0.0
    assert weather["wind_speed_mph"] == 20.0  # noqa: PLR2004
    assert weather["wind_direction"] == "NW"
    assert weather["forecast_desc"] == "Clear"
    client.get.assert_called_once()
    assert client.get.call_args.args[0] == forecast_url


def test_get_nws_hourly_forecast_handles_non_numeric_wind_speed(
    monkeypatch: pytest.MonkeyPatch,
    mocker: pytest.MockFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that the get_nws_hourly_forecast function handles non-numeric wind speeds correctly."""
    # Arrange
    monkeypatch.setenv("NWS_APP_NAME", "cta-eta")
    monkeypatch.setenv("NWS_EMAIL", "data@example.com")

    client = mocker.Mock(spec=httpx.Client)
    grid_id = "LOT/76,73"
    forecast_url = f"https://api.weather.gov/gridpoints/{grid_id}/forecast/hourly"

    payload = {
        "properties": {
            "periods": [
                {
                    "startTime": "2026-01-14T21:00:00-06:00",
                    "endTime": "2026-01-14T22:00:00-06:00",
                    "temperature": 10,
                    "temperatureUnit": "wmoUnit:degF",
                    "probabilityOfPrecipitation": {"value": 50},
                    "dewpoint": {"value": 10.0, "unitCode": "wmoUnit:degF"},
                    "relativeHumidity": {"value": 80},
                    "windSpeed": "Calm",  # no digits -> 0.0
                    "windDirection": "N",
                    "shortForecast": "Calm",
                }
            ]
        }
    }
    client.get.return_value = httpx_json_response(payload, 200, forecast_url)

    # Act
    weather = api_weather_nws.get_nws_hourly_forecast(client, grid_id)

    # Assert
    assert weather["wind_speed_mph"] == 0.0


def test_get_nws_forecast_url_propagates_http_errors_without_retry_delay(
    monkeypatch: pytest.MonkeyPatch,
    mocker: pytest.MockFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that the get_nws_forecast_url function propagates HTTP errors without retry delay."""
    # Arrange
    # Avoid stamina retry/backoff by calling through one wrapper level.
    # (Decorator order: stamina.retry(log_api_call(original)))
    fn_no_retry = api_weather_nws.get_nws_forecast_url.__wrapped__  # type: ignore[attr-defined]

    monkeypatch.setenv("NWS_APP_NAME", "cta-eta")
    monkeypatch.setenv("NWS_EMAIL", "data@example.com")

    client = mocker.Mock(spec=httpx.Client)
    latitude = 41.88
    longitude = -87.63
    url = f"{api_weather_nws.NWS_POINTS_URL}/{latitude},{longitude}"
    client.get.return_value = httpx_json_response({"error": "nope"}, 503, url)

    # Act / Assert
    with pytest.raises(httpx.HTTPStatusError):
        fn_no_retry(client, latitude, longitude)
