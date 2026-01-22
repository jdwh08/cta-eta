"""Sync tests for the NWS API client.

These exist primarily to cover the non-async code paths in `api_weather_nws`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from cta_eta.data_collection.apis import api_weather_nws

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture


@pytest.fixture
def nws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required NWS env vars for the duration of the test."""
    monkeypatch.setenv("NWS_APP_NAME", "cta-eta")
    monkeypatch.setenv("NWS_EMAIL", "data@example.com")


def test_get_nws_forecast_url_calls_points_api_sync(
    nws_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that `get_nws_forecast_url` calls points API correctly."""
    # Arrange
    client = mocker.Mock(spec=httpx.Client)
    latitude = 41.88
    longitude = -87.63
    forecast_hourly = "https://api.weather.gov/gridpoints/LOT/85,67/forecast/hourly"
    url = f"{api_weather_nws.NWS_POINTS_URL}/{latitude},{longitude}"
    client.get.return_value = httpx_json_response(
        {"properties": {"forecastHourly": forecast_hourly}}, 200, url
    )

    # Act
    result = api_weather_nws.get_nws_forecast_url(client, latitude, longitude)

    # Assert
    assert result == forecast_hourly
    client.get.assert_called_once()
    assert client.get.call_args.args[0] == url
    assert client.get.call_args.kwargs["follow_redirects"] is True
    assert client.get.call_args.kwargs["headers"] == {
        "User-Agent": "(cta-eta, data@example.com)"
    }


def test_discover_nws_grid_parses_grid_id_sync(mocker: MockerFixture) -> None:
    """Test that `discover_nws_grid` parses grid ID from a forecastHourly URL."""
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


def test_get_nws_hourly_forecast_converts_units_and_defaults_sync(
    nws_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that `get_nws_hourly_forecast` converts units and defaults missing values."""
    # Arrange
    client = mocker.Mock(spec=httpx.Client)
    grid_id = "LOT/76,73"
    forecast_url = f"https://api.weather.gov/gridpoints/{grid_id}/forecast/hourly"
    payload = {
        "properties": {
            "periods": [
                {
                    "startTime": "2026-01-14T21:00:00-06:00",
                    "endTime": "2026-01-14T22:00:00-06:00",
                    "temperature": 0,  # C -> 32F
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
    assert weather["temperature_f"] == 32  # noqa: PLR2004
    assert weather["dewpoint_f"] == 32.0  # noqa: PLR2004
    assert weather["prob_precip_pct"] == 0.0
    assert weather["humidity_pct"] == 0.0
    assert weather["wind_speed_mph"] == 20.0  # noqa: PLR2004


def test_get_nws_forecast_url_propagates_http_errors_without_retry_delay_sync(
    nws_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that sync points lookup propagates HTTPStatusError without retry delay."""
    # Arrange
    fn_no_retry = getattr(
        api_weather_nws.get_nws_forecast_url,
        "__wrapped__",
        api_weather_nws.get_nws_forecast_url,
    )
    client = mocker.Mock(spec=httpx.Client)
    latitude = 41.88
    longitude = -87.63
    url = f"{api_weather_nws.NWS_POINTS_URL}/{latitude},{longitude}"
    client.get.return_value = httpx_json_response({"error": "nope"}, 503, url)

    # Act / Assert
    with pytest.raises(httpx.HTTPStatusError):
        fn_no_retry(client, latitude, longitude)
