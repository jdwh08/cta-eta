"""Test the NWS API client."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from cta_eta.data_collection.apis import api_weather_nws

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture


pytestmark = pytest.mark.asyncio


@pytest.fixture
def nws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required NWS env vars for the duration of the test."""
    monkeypatch.setenv("NWS_APP_NAME", "cta-eta")
    monkeypatch.setenv("NWS_EMAIL", "data@example.com")


async def test_get_auth_header_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that the get_auth_header function requires the NWS_APP_NAME and NWS_EMAIL environment variables."""
    # Arrange
    monkeypatch.delenv("NWS_APP_NAME", raising=False)
    monkeypatch.delenv("NWS_EMAIL", raising=False)

    # Act / Assert
    with pytest.raises(ValueError, match="NWS_APP_NAME and NWS_EMAIL must be set"):
        api_weather_nws._get_auth_header()


async def test_get_auth_header_formats_user_agent(
    nws_env: None,  # noqa: ARG001
) -> None:
    """Test that the get_auth_header function formats the user agent correctly."""
    # Act
    header = api_weather_nws._get_auth_header()

    # Assert
    assert header == {"User-Agent": "(cta-eta, data@example.com)"}


async def test_get_nws_forecast_url_calls_points_api(
    nws_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that the get_nws_forecast_url function calls the points API correctly."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    latitude = 41.88
    longitude = -87.63
    forecast_hourly = "https://api.weather.gov/gridpoints/LOT/85,67/forecast/hourly"
    client.get.return_value = httpx_json_response(
        {"properties": {"forecastHourly": forecast_hourly}},
        200,
        f"{api_weather_nws.NWS_POINTS_URL}/{latitude},{longitude}",
    )

    # Act
    result = await api_weather_nws.get_nws_forecast_url(client, latitude, longitude)

    # Assert
    assert result == forecast_hourly
    client.get.assert_awaited_once()
    called_url = client.get.call_args.args[0]
    called_headers = client.get.call_args.kwargs["headers"]
    called_follow_redirects = client.get.call_args.kwargs["follow_redirects"]
    assert called_url == f"{api_weather_nws.NWS_POINTS_URL}/{latitude},{longitude}"
    assert called_headers == {"User-Agent": "(cta-eta, data@example.com)"}
    assert called_follow_redirects is True


async def test_get_nws_forecast_url_follows_points_redirect(
    nws_env: None,  # noqa: ARG001
) -> None:
    """Test that the points API 301 redirect is followed automatically."""
    # Arrange
    latitude = 41.8800000001
    longitude = -87.6300000001
    url = f"{api_weather_nws.NWS_POINTS_URL}/{latitude},{longitude}"
    redirected_url = f"{api_weather_nws.NWS_POINTS_URL}/41.88,-87.63"
    forecast_hourly = "https://api.weather.gov/gridpoints/LOT/85,67/forecast/hourly"

    seen_urls: list[str] = []
    seen_user_agents: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        seen_user_agents.append(request.headers.get("User-Agent"))

        if str(request.url) == url:
            return httpx.Response(
                status_code=301,
                headers={"Location": redirected_url},
                request=request,
            )

        if str(request.url) == redirected_url:
            return httpx.Response(
                status_code=200,
                json={"properties": {"forecastHourly": forecast_hourly}},
                request=request,
            )

        return httpx.Response(status_code=404, request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await api_weather_nws.get_nws_forecast_url(client, latitude, longitude)

    assert result == forecast_hourly
    assert seen_urls == [url, redirected_url]
    # NWS policy requires a User-Agent header; ensure it survives redirect.
    assert seen_user_agents == [
        "(cta-eta, data@example.com)",
        "(cta-eta, data@example.com)",
    ]


async def test_discover_nws_grid_parses_grid_id(
    mocker: MockerFixture,
) -> None:
    """Test that the discover_nws_grid function parses the grid ID correctly."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    mocker.patch.object(
        api_weather_nws,
        "get_nws_forecast_url",
        new=mocker.AsyncMock(
            return_value="https://api.weather.gov/gridpoints/LOT/85,67/forecast/hourly"
        ),
    )

    # Act
    grid_id = await api_weather_nws.discover_nws_grid(client, 41.88, -87.63)

    # Assert
    assert grid_id == "LOT/85,67"


async def test_discover_nws_grid_rejects_unexpected_url(
    mocker: MockerFixture,
) -> None:
    """Test that the discover_nws_grid function rejects unexpected URLs."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    mocker.patch.object(
        api_weather_nws,
        "get_nws_forecast_url",
        new=mocker.AsyncMock(return_value="https://bad.example/x"),
    )

    # Act / Assert
    with pytest.raises(ValueError, match="Unexpected NWS forecast URL format"):
        await api_weather_nws.discover_nws_grid(client, 41.88, -87.63)


async def test_get_nws_hourly_forecast_converts_units_and_defaults(
    nws_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that the get_nws_hourly_forecast function converts units and defaults correctly."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
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
    weather = await api_weather_nws.get_nws_hourly_forecast(client, grid_id)

    # Assert
    assert weather["start_time"] == "2026-01-14T21:00:00-06:00"
    assert weather["end_time"] == "2026-01-14T22:00:00-06:00"
    assert weather["temperature_f"] == 32
    assert weather["dewpoint_f"] == 32.0
    assert weather["prob_precip_pct"] == 0.0
    assert weather["humidity_pct"] == 0.0
    assert weather["wind_speed_mph"] == 20.0
    assert weather["wind_direction"] == "NW"
    assert weather["forecast_desc"] == "Clear"
    client.get.assert_awaited_once()
    assert client.get.call_args.args[0] == forecast_url


async def test_get_nws_hourly_forecast_handles_non_numeric_wind_speed(
    nws_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that the get_nws_hourly_forecast function handles non-numeric wind speeds correctly."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
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
    weather = await api_weather_nws.get_nws_hourly_forecast(client, grid_id)

    # Assert
    assert weather["wind_speed_mph"] == 0.0


async def test_get_nws_hourly_forecast_keeps_fahrenheit_units(
    nws_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that Fahrenheit inputs remain unchanged (no extra conversion)."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
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
                    "probabilityOfPrecipitation": {"value": 0},
                    "dewpoint": {"value": 5.0, "unitCode": "wmoUnit:degF"},
                    "relativeHumidity": {"value": 42},
                    "windSpeed": "5 mph",
                    "windDirection": "E",
                    "shortForecast": "Clear",
                }
            ]
        }
    }
    client.get.return_value = httpx_json_response(payload, 200, forecast_url)

    # Act
    weather = await api_weather_nws.get_nws_hourly_forecast(client, grid_id)

    # Assert
    assert weather["temperature_f"] == 10
    assert weather["dewpoint_f"] == 5.0
    assert weather["prob_precip_pct"] == 0
    assert weather["humidity_pct"] == 42


async def test_get_nws_hourly_forecast_parses_wind_speed_range_prefix(
    nws_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test windSpeed strings like '5 to 10 mph' use the leading numeric value."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
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
                    "windSpeed": "5 to 10 mph",
                    "windDirection": "N",
                    "shortForecast": "Breezy",
                }
            ]
        }
    }
    client.get.return_value = httpx_json_response(payload, 200, forecast_url)

    # Act
    weather = await api_weather_nws.get_nws_hourly_forecast(client, grid_id)

    # Assert
    assert weather["wind_speed_mph"] == 5.0


async def test_get_nws_forecast_url_propagates_http_errors_without_retry_delay(
    nws_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that the get_nws_forecast_url function propagates HTTP errors without retry delay."""
    # Arrange
    # Avoid stamina retry/backoff by calling through one wrapper level.
    # (Decorator order: stamina.retry(log_api_call(original)))
    fn_no_retry = getattr(
        api_weather_nws.get_nws_forecast_url,
        "__wrapped__",
        api_weather_nws.get_nws_forecast_url,
    )

    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    latitude = 41.88
    longitude = -87.63
    url = f"{api_weather_nws.NWS_POINTS_URL}/{latitude},{longitude}"
    client.get.return_value = httpx_json_response({"error": "nope"}, 503, url)

    # Act / Assert
    with pytest.raises(httpx.HTTPStatusError):
        await fn_no_retry(client, latitude, longitude)

    client.get.assert_awaited_once()
    assert client.get.call_args.kwargs["follow_redirects"] is True


async def test_get_nws_hourly_forecast_propagates_http_errors_without_retry_delay(
    nws_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict, int, str], httpx.Response],
) -> None:
    """Test that hourly forecast propagates HTTP errors without retry delay."""
    # Arrange
    fn_no_retry = getattr(
        api_weather_nws.get_nws_hourly_forecast,
        "__wrapped__",
        api_weather_nws.get_nws_hourly_forecast,
    )

    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    grid_id = "LOT/76,73"
    forecast_url = f"https://api.weather.gov/gridpoints/{grid_id}/forecast/hourly"
    client.get.return_value = httpx_json_response({"error": "nope"}, 503, forecast_url)

    # Act / Assert
    with pytest.raises(httpx.HTTPStatusError):
        await fn_no_retry(client, grid_id)

    client.get.assert_awaited_once()
