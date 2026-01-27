"""OpenWeatherMap API client for fallback weather data.

This module provides stateless functions to fetch weather data from OpenWeatherMap's
2.5 APIs as a fallback source when primary weather APIs (NWS, Open-Meteo) fail.

API Documentation: https://openweathermap.org/api
Rate Limits (free tier):
- Current Weather 2.5: 60 calls/minute, 1M calls/month
- 3hr 5-Day Forecast 2.5: 60 calls/minute, 1M calls/month
- One Call 3.0: 1,000 calls/day

Uses Current Weather 2.5 API and 5-Day Forecast 2.5 API which have more generous
rate limits compared to One Call 3.0 API. This module should still be used sparingly
as a fallback when primary weather sources are unavailable.

All functions accept an httpx.AsyncClient parameter for dependency injection and proper
connection pooling management by the caller.
"""

from __future__ import annotations

import os
from typing import Final

import httpx
import stamina

### OWN MODULES
from cta_eta.data_collection.config import get_config_section, load_config
from cta_eta.data_collection.exceptions import (
    APIResponseError,
    ConfigurationError,
)
from cta_eta.data_collection.logging import get_logger, log_api_call
from cta_eta.data_collection.utils import safe_get_nested, validate_lat_lon

logger = get_logger(__name__)

config = load_config()
retry_config = get_config_section("retry", config=config)
MAX_RETRY_ATTEMPTS: Final[int] = int(retry_config.get("max_retry_attempts", 10))


# OpenWeatherMap 2.5 API endpoints
CURRENT_WEATHER_URL: Final[str] = "https://api.openweathermap.org/data/2.5/weather"
FORECAST_URL: Final[str] = "https://api.openweathermap.org/data/2.5/forecast"


def _get_api_key() -> str:
    """Get OpenWeatherMap API key from environment.

    Returns:
        API key string

    Raises:
        ConfigurationError: If OPENWEATHERMAP_API_KEY environment variable not set

    """
    api_key = os.getenv("OPENWEATHERMAP_API_KEY")
    if not api_key:
        msg = "OPENWEATHERMAP_API_KEY environment variable not set"
        raise ConfigurationError(msg)
    return api_key


def _parse_discover_grid_response(data: dict[str, object]) -> str:
    """Extract grid ID (lat,lon) from OpenWeatherMap Current Weather response.

    Caller is responsible for catching KeyError, TypeError, ValueError and
    logging or re-raising with context.
    """
    coord = safe_get_nested(data, "coord", api_name="OpenWeatherMap")
    if not isinstance(coord, dict):
        msg = "OpenWeatherMap API response 'coord' is not a dict"
        raise APIResponseError(msg)

    actual_lat = coord.get("lat")  # ty:ignore[invalid-argument-type]
    actual_lon = coord.get("lon")  # ty:ignore[invalid-argument-type]

    if actual_lat is None or actual_lon is None:
        msg = "OpenWeatherMap API response missing 'coord.lat' or 'coord.lon'"
        raise APIResponseError(msg)

    if not isinstance(actual_lat, (int, float)) or not isinstance(
        actual_lon, (int, float)
    ):
        msg = (
            "OpenWeatherMap API response 'coord.lat' or 'coord.lon' is not numeric. "
            f"Got types: {type(actual_lat).__name__}, {type(actual_lon).__name__}"
        )
        raise APIResponseError(msg)

    return f"{actual_lat},{actual_lon}"


@stamina.retry(on=httpx.HTTPStatusError, attempts=MAX_RETRY_ATTEMPTS)
@log_api_call(logger)
async def discover_openweathermap_grid(
    client: httpx.AsyncClient, latitude: float, longitude: float
) -> str:
    """Discover OpenWeatherMap grid identifier from API response.

    Makes minimal API request to OpenWeatherMap Current Weather API and extracts
    the actual coordinates returned by the API. OpenWeatherMap accepts exact
    coordinates, so the grid ID is simply the lat,lon pair.

    Args:
        client: HTTP client for API requests
        latitude: Coordinate latitude (must be between -90 and 90)
        longitude: Coordinate longitude (must be between -180 and 180)

    Returns:
        OpenWeatherMap grid identifier (e.g., "41.88,-87.63")

    Raises:
        httpx.HTTPStatusError: If API request fails
        ValueError: If API key not configured or coordinates out of range

    """
    validate_lat_lon(latitude, longitude)
    api_key = _get_api_key()

    # Make minimal request to OpenWeatherMap Current Weather API
    response = await client.get(
        CURRENT_WEATHER_URL,
        params={
            "lat": latitude,
            "lon": longitude,
            "appid": api_key,
            "units": "imperial",  # Use imperial to match other modules
        },
    )
    response.raise_for_status()
    data = response.json()

    try:
        return _parse_discover_grid_response(data)
    except (KeyError, TypeError, ValueError):
        logger.exception("Failed to parse OpenWeatherMap grid discovery response")
        raise


def _parse_current_weather_response(
    data: dict[str, object],
) -> dict[str, str | int | float | None]:
    """Extract normalized current weather from OpenWeatherMap Current Weather response.

    Caller is responsible for catching KeyError, TypeError, IndexError, ValueError
    and logging or re-raising with context.
    """
    coord = safe_get_nested(data, "coord", api_name="OpenWeatherMap")
    main = safe_get_nested(data, "main", api_name="OpenWeatherMap")
    wind = safe_get_nested(data, "wind", api_name="OpenWeatherMap")
    weather_array = safe_get_nested(data, "weather", api_name="OpenWeatherMap")

    if not isinstance(coord, dict):
        msg = "OpenWeatherMap API response 'coord' is not a dict"
        raise APIResponseError(msg)
    if not isinstance(main, dict):
        msg = "OpenWeatherMap API response 'main' is not a dict"
        raise APIResponseError(msg)
    if not isinstance(wind, dict):
        msg = "OpenWeatherMap API response 'wind' is not a dict"
        raise APIResponseError(msg)
    if not isinstance(weather_array, list) or len(weather_array) == 0:
        msg = "OpenWeatherMap API response 'weather' is missing or empty"
        raise APIResponseError(msg)

    weather = weather_array[0]
    if not isinstance(weather, dict):
        msg = "OpenWeatherMap API response 'weather[0]' is not a dict"
        raise APIResponseError(msg)

    latitude = coord.get("lat")  # ty:ignore[invalid-argument-type]
    longitude = coord.get("lon")  # ty:ignore[invalid-argument-type]
    if latitude is None or longitude is None:
        msg = "OpenWeatherMap API response missing 'coord.lat' or 'coord.lon'"
        raise APIResponseError(msg)
    if not isinstance(latitude, (int, float)) or not isinstance(
        longitude, (int, float)
    ):
        msg = (
            "OpenWeatherMap API response 'coord.lat' or 'coord.lon' is not numeric. "
            f"Got types: {type(latitude).__name__}, {type(longitude).__name__}"
        )
        raise APIResponseError(msg)

    timestamp = data.get("dt")
    if timestamp is None:
        msg = "OpenWeatherMap API response missing 'dt' field"
        raise APIResponseError(msg)

    visibility_m = data.get("visibility")
    visibility_mi = (
        float(visibility_m) / 1609.34
        if visibility_m is not None and isinstance(visibility_m, (int, float))
        else None
    )

    clouds = data.get("clouds")
    cloud_cover_pct = (
        int(clouds["all"])  # ty:ignore[invalid-argument-type]
        if isinstance(clouds, dict) and clouds.get("all") is not None  # ty:ignore[invalid-argument-type]
        else None
    )

    return {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "timestamp": int(timestamp) if isinstance(timestamp, (int, float)) else None,
        "temperature_f": float(v) if (v := main.get("temp")) is not None else None,  # ty:ignore[invalid-argument-type]
        "feels_like_f": float(v) if (v := main.get("feels_like")) is not None else None,  # ty:ignore[invalid-argument-type]
        "temp_min_f": float(v) if (v := main.get("temp_min")) is not None else None,  # ty:ignore[invalid-argument-type]
        "temp_max_f": float(v) if (v := main.get("temp_max")) is not None else None,  # ty:ignore[invalid-argument-type]
        "pressure_hpa": float(v) if (v := main.get("pressure")) is not None else None,  # ty:ignore[invalid-argument-type]
        "humidity_pct": int(v) if (v := main.get("humidity")) is not None else None,  # ty:ignore[invalid-argument-type]
        "visibility_mi": visibility_mi,
        "wind_speed_mph": float(v) if (v := wind.get("speed")) is not None else None,  # ty:ignore[invalid-argument-type]
        "wind_direction_deg": int(v) if (v := wind.get("deg")) is not None else None,  # ty:ignore[invalid-argument-type]
        "wind_gust_mph": float(v) if (v := wind.get("gust")) is not None else None,  # ty:ignore[invalid-argument-type]
        "cloud_cover_pct": cloud_cover_pct,
        "weather_main": weather.get("main") or None,  # ty:ignore[invalid-argument-type]
        "weather_desc": weather.get("description") or None,  # ty:ignore[invalid-argument-type]
    }


@stamina.retry(on=httpx.HTTPStatusError, attempts=MAX_RETRY_ATTEMPTS)
@log_api_call(logger)
async def get_openweathermap_current(
    client: httpx.AsyncClient, grid_id: str
) -> dict[str, str | int | float | None]:
    """Get current weather data from OpenWeatherMap Current Weather 2.5 API.

    Fetches current weather conditions for a known grid location. This API has
    much more generous rate limits than One Call 3.0 API.

    Args:
        client: HTTP client for API requests
        grid_id: OpenWeatherMap grid identifier (e.g., "41.88,-87.63")

    Returns:
        Dictionary with normalized weather data:
        - latitude: Latitude of weather location
        - longitude: Longitude of weather location
        - timestamp: Unix timestamp (UTC) of observation
        - temperature_f: Temperature in Fahrenheit
        - feels_like_f: Apparent temperature in Fahrenheit
        - temp_min_f: Minimum temperature in Fahrenheit
        - temp_max_f: Maximum temperature in Fahrenheit
        - pressure_hpa: Atmospheric pressure in hPa
        - humidity_pct: Relative humidity percentage (0-100)
        - visibility_mi: Visibility in miles
        - wind_speed_mph: Wind speed in miles per hour
        - wind_direction_deg: Wind direction in degrees
        - wind_gust_mph: Wind gust speed in mph (None if not available)
        - cloud_cover_pct: Cloud coverage percentage (0-100)
        - weather_main: Main weather condition (e.g., "Clouds", "Rain")
        - weather_desc: Detailed weather description

    Raises:
        httpx.HTTPStatusError: If API request fails after retries
        ValueError: If API key not configured

    """
    api_key = _get_api_key()

    # Parse grid identifier to get coordinates
    if grid_id.count(",") != 1:
        msg = f"Invalid grid ID: {grid_id}"
        raise ValueError(msg)
    grid_lat, grid_lon = grid_id.split(",")
    try:
        lat = float(grid_lat)
        lon = float(grid_lon)
    except ValueError as e:
        msg = f"Invalid grid ID: {grid_id}"
        raise ValueError(msg) from e

    validate_lat_lon(lat, lon)

    response = await client.get(
        CURRENT_WEATHER_URL,
        params={
            "lat": lat,
            "lon": lon,
            "appid": api_key,
            "units": "imperial",  # Fahrenheit, mph
        },
    )
    response.raise_for_status()
    data = response.json()

    try:
        return _parse_current_weather_response(data)
    except (KeyError, TypeError, IndexError, ValueError, APIResponseError) as e:
        logger.exception("Failed to parse OpenWeatherMap current weather response")
        if isinstance(e, APIResponseError):
            raise
        msg = f"OpenWeatherMap API response structure unexpected: {e}"
        raise APIResponseError(msg) from e


def _coords_from_city_dict(city: dict[str, object]) -> tuple[float, float]:
    """Extract validated (lat, lon) from OpenWeatherMap 'city' object. Raises APIResponseError on invalid structure."""
    coord = city.get("coord")
    if not isinstance(coord, dict):
        msg = "OpenWeatherMap API response 'city.coord' is not a dict"
        raise APIResponseError(msg)
    lat = coord.get("lat")  # ty:ignore[invalid-argument-type]
    lon = coord.get("lon")  # ty:ignore[invalid-argument-type]
    if lat is None or lon is None:
        msg = "OpenWeatherMap API response missing 'city.coord.lat' or 'city.coord.lon'"
        raise APIResponseError(msg)
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        msg = (
            "OpenWeatherMap API response 'city.coord.lat' or 'city.coord.lon' is not numeric. "
            f"Got types: {type(lat).__name__}, {type(lon).__name__}"
        )
        raise APIResponseError(msg)
    return float(lat), float(lon)


def _parse_forecast_response(
    data: dict[str, object],
) -> dict[str, str | int | float | None]:
    """Extract normalized forecast from OpenWeatherMap 5-Day Forecast response.

    Caller is responsible for catching KeyError, TypeError, IndexError, ValueError
    and logging or re-raising with context.
    """
    forecast_list = safe_get_nested(data, "list", api_name="OpenWeatherMap")
    city = safe_get_nested(data, "city", api_name="OpenWeatherMap")

    if not isinstance(forecast_list, list) or len(forecast_list) == 0:
        msg = "OpenWeatherMap API response 'list' is missing or empty"
        raise APIResponseError(msg)

    forecast = forecast_list[0]
    if not isinstance(forecast, dict):
        msg = "OpenWeatherMap API response 'list[0]' is not a dict"
        raise APIResponseError(msg)

    main = forecast.get("main")  # ty:ignore[invalid-argument-type]
    wind = forecast.get("wind")  # ty:ignore[invalid-argument-type]
    weather_array = forecast.get("weather")  # ty:ignore[invalid-argument-type]

    if not isinstance(main, dict):
        msg = "OpenWeatherMap API response 'list[0].main' is not a dict"
        raise APIResponseError(msg)
    if not isinstance(wind, dict):
        msg = "OpenWeatherMap API response 'list[0].wind' is not a dict"
        raise APIResponseError(msg)
    if not isinstance(weather_array, list) or len(weather_array) == 0:
        msg = "OpenWeatherMap API response 'list[0].weather' is missing or empty"
        raise APIResponseError(msg)

    weather = weather_array[0]
    if not isinstance(weather, dict):
        msg = "OpenWeatherMap API response 'list[0].weather[0]' is not a dict"
        raise APIResponseError(msg)

    if not isinstance(city, dict):
        msg = "OpenWeatherMap API response 'city' is not a dict"
        raise APIResponseError(msg)

    # NOTE(jdwh08): technically we never validated city keys are strings, but ehhh
    latitude, longitude = _coords_from_city_dict(city)

    visibility_m = forecast.get("visibility")  # ty:ignore[invalid-argument-type]
    visibility_mi = (
        float(visibility_m) / 1609.34
        if visibility_m is not None and isinstance(visibility_m, (int, float))
        else None
    )

    clouds_f = forecast.get("clouds")  # ty:ignore[invalid-argument-type]
    cloud_cover_pct = (
        int(clouds_f["all"])
        if isinstance(clouds_f, dict) and clouds_f.get("all") is not None
        else None
    )

    pop = forecast.get("pop")  # ty:ignore[invalid-argument-type]
    prob_precip_pct = (
        float(pop) * 100 if pop is not None and isinstance(pop, (int, float)) else None
    )

    return {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "timestamp": int(v) if (v := forecast.get("dt")) is not None else None,  # ty:ignore[invalid-argument-type]
        "dt_txt": forecast.get("dt_txt") or None,  # ty:ignore[invalid-argument-type]
        "temperature_f": float(v) if (v := main.get("temp")) is not None else None,
        "feels_like_f": float(v) if (v := main.get("feels_like")) is not None else None,
        "temp_min_f": float(v) if (v := main.get("temp_min")) is not None else None,
        "temp_max_f": float(v) if (v := main.get("temp_max")) is not None else None,
        "pressure_hpa": float(v) if (v := main.get("pressure")) is not None else None,
        "humidity_pct": int(v) if (v := main.get("humidity")) is not None else None,
        "visibility_mi": visibility_mi,
        "wind_speed_mph": float(v) if (v := wind.get("speed")) is not None else None,
        "wind_direction_deg": int(v) if (v := wind.get("deg")) is not None else None,
        "wind_gust_mph": float(v) if (v := wind.get("gust")) is not None else None,
        "cloud_cover_pct": cloud_cover_pct,
        "prob_precip_pct": prob_precip_pct,
        "weather_main": weather.get("main") or None,
        "weather_desc": weather.get("description") or None,
    }


@stamina.retry(on=httpx.HTTPStatusError, attempts=MAX_RETRY_ATTEMPTS)
@log_api_call(logger)
async def get_openweathermap_forecast_hourly(
    client: httpx.AsyncClient, grid_id: str
) -> dict[str, str | int | float | None]:
    """Get hourly forecast from OpenWeatherMap Hourly Forecast2.5 API.

    Fetches the next hour's forecast data from the API.

    Args:
        client: HTTP client for API requests
        grid_id: OpenWeatherMap grid identifier (e.g., "41.88,-87.63")

    Returns:
        Dictionary with normalized forecast data:
        - latitude: Latitude of weather location
        - longitude: Longitude of weather location
        - timestamp: Unix timestamp (UTC) for forecast period
        - dt_txt: Human-readable timestamp string
        - temperature_f: Temperature in Fahrenheit
        - feels_like_f: Apparent temperature in Fahrenheit
        - temp_min_f: Minimum temperature in Fahrenheit
        - temp_max_f: Maximum temperature in Fahrenheit
        - pressure_hpa: Atmospheric pressure in hPa
        - humidity_pct: Relative humidity percentage (0-100)
        - visibility_mi: Visibility in miles
        - wind_speed_mph: Wind speed in miles per hour
        - wind_direction_deg: Wind direction in degrees
        - wind_gust_mph: Wind gust speed in mph
        - cloud_cover_pct: Cloud coverage percentage (0-100)
        - prob_precip_pct: Probability of precipitation (0-100)
        - weather_main: Main weather condition
        - weather_desc: Detailed weather description

    Raises:
        httpx.HTTPStatusError: If API request fails after retries
        ValueError: If API key not configured

    """
    api_key = _get_api_key()

    # Parse grid identifier to get coordinates
    if grid_id.count(",") != 1:
        msg = f"Invalid grid ID: {grid_id}"
        raise ValueError(msg)
    grid_lat, grid_lon = grid_id.split(",")
    try:
        lat = float(grid_lat)
        lon = float(grid_lon)
    except ValueError as e:
        msg = f"Invalid grid ID: {grid_id}"
        raise ValueError(msg) from e

    validate_lat_lon(lat, lon)

    response = await client.get(
        FORECAST_URL,
        params={
            "lat": lat,
            "lon": lon,
            "appid": api_key,
            "units": "imperial",  # Fahrenheit, mph
            "cnt": 1,  # Only get first forecast period to minimize data transfer
        },
    )
    response.raise_for_status()
    data = response.json()

    try:
        return _parse_forecast_response(data)
    except (KeyError, TypeError, IndexError, ValueError, APIResponseError) as e:
        logger.exception("Failed to parse OpenWeatherMap forecast response")
        if isinstance(e, APIResponseError):
            raise
        msg = f"OpenWeatherMap API response structure unexpected: {e}"
        raise APIResponseError(msg) from e
