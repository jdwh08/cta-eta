"""OpenWeatherMap API client for fallback weather data.

This module provides stateless functions to fetch weather data from OpenWeatherMap's
2.5 APIs as a fallback source when primary weather APIs (NWS, Open-Meteo) fail.

Uses Current Weather 2.5 API and 5-Day Forecast 2.5 API which have more generous
rate limits compared to One Call 3.0 API. This module should still be used sparingly
as a fallback when primary weather sources are unavailable.

All functions accept an httpx.Client parameter for dependency injection and proper
connection pooling management by the caller.

Rate limits to be considered:
- Current Weather 2.5: 60 calls/minute, 1M calls/month (free tier; I have more but ehhh)
- 5-Day Forecast 2.5: 60 calls/minute, 1M calls/month (free tier; I have more but ehhh)
- One Call 3.0: 1k/day limit
"""

from __future__ import annotations

import os
from typing import Final

import httpx
import stamina

### OWN MODULES
from cta_eta.data_collection.logging import get_logger, log_api_call

logger = get_logger(__name__)


# OpenWeatherMap 2.5 API endpoints
CURRENT_WEATHER_URL: Final[str] = "https://api.openweathermap.org/data/2.5/weather"
FORECAST_URL: Final[str] = "https://api.openweathermap.org/data/2.5/forecast"


def _get_api_key() -> str:
    """Get OpenWeatherMap API key from environment.

    Returns:
        API key string

    Raises:
        ValueError: If OPENWEATHERMAP_API_KEY environment variable not set

    """
    api_key = os.getenv("OPENWEATHERMAP_API_KEY")
    if not api_key:
        msg = "OPENWEATHERMAP_API_KEY environment variable not set"
        raise ValueError(msg)
    return api_key


@stamina.retry(on=httpx.HTTPStatusError, attempts=1)
@log_api_call(logger)
def discover_openweathermap_grid(
    client: httpx.Client, latitude: float, longitude: float
) -> str:
    """Discover OpenWeatherMap grid identifier from API response.

    Makes minimal API request to OpenWeatherMap Current Weather API and extracts
    the actual coordinates returned by the API. OpenWeatherMap accepts exact
    coordinates, so the grid ID is simply the lat,lon pair.

    Args:
        client: HTTP client for API requests
        latitude: Coordinate latitude
        longitude: Coordinate longitude

    Returns:
        OpenWeatherMap grid identifier (e.g., "41.88,-87.63")

    Raises:
        httpx.HTTPStatusError: If API request fails
        ValueError: If API key not configured

    """
    api_key = _get_api_key()

    # Make minimal request to OpenWeatherMap Current Weather API
    response = client.get(
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

    # Extract actual coordinates used by API
    actual_lat = data["coord"]["lat"]
    actual_lon = data["coord"]["lon"]

    return f"{actual_lat},{actual_lon}"


@stamina.retry(on=httpx.HTTPStatusError, attempts=1)
@log_api_call(logger)
async def discover_openweathermap_grid_async(
    client: httpx.AsyncClient, latitude: float, longitude: float
) -> str:
    """Async version of `discover_openweathermap_grid`."""
    api_key = _get_api_key()
    response = await client.get(
        CURRENT_WEATHER_URL,
        params={
            "lat": latitude,
            "lon": longitude,
            "appid": api_key,
            "units": "imperial",
        },
    )
    response.raise_for_status()
    data = response.json()
    return f"{data['coord']['lat']},{data['coord']['lon']}"


@stamina.retry(on=httpx.HTTPStatusError, attempts=3)
@log_api_call(logger)
def get_openweathermap_current(
    client: httpx.Client, grid_id: str
) -> dict[str, str | float]:
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
        - wind_gust_mph: Wind gust speed in mph (0.0 if not available)
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

    response = client.get(
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

    # Extract current weather data
    main = data["main"]
    wind = data["wind"]
    weather = data["weather"][0]

    # Convert wind speed from meters/sec to mph (API returns m/s despite units=imperial)
    # Actually, with units=imperial, wind speed is already in mph
    # But visibility is in meters regardless of units parameter
    visibility_mi = data.get("visibility", 0) / 1609.34  # meters to miles

    return {
        "latitude": data["coord"]["lat"],
        "longitude": data["coord"]["lon"],
        "timestamp": data["dt"],
        "temperature_f": main["temp"],
        "feels_like_f": main["feels_like"],
        "temp_min_f": main["temp_min"],
        "temp_max_f": main["temp_max"],
        "pressure_hpa": main["pressure"],
        "humidity_pct": main["humidity"],
        "visibility_mi": visibility_mi,
        "wind_speed_mph": wind["speed"],
        "wind_direction_deg": wind.get("deg", 0),
        "wind_gust_mph": wind.get("gust", 0.0),
        "cloud_cover_pct": data.get("clouds", {}).get("all", 0),
        "weather_main": weather["main"],
        "weather_desc": weather["description"],
    }


@stamina.retry(on=httpx.HTTPStatusError, attempts=3)
@log_api_call(logger)
async def get_openweathermap_current_async(
    client: httpx.AsyncClient, grid_id: str
) -> dict[str, str | float]:
    """Async version of `get_openweathermap_current`."""
    api_key = _get_api_key()

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

    response = await client.get(
        CURRENT_WEATHER_URL,
        params={
            "lat": lat,
            "lon": lon,
            "appid": api_key,
            "units": "imperial",
        },
    )
    response.raise_for_status()
    data = response.json()

    main = data["main"]
    wind = data["wind"]
    weather = data["weather"][0]
    visibility_mi = data.get("visibility", 0) / 1609.34

    return {
        "latitude": data["coord"]["lat"],
        "longitude": data["coord"]["lon"],
        "timestamp": data["dt"],
        "temperature_f": main["temp"],
        "feels_like_f": main["feels_like"],
        "temp_min_f": main["temp_min"],
        "temp_max_f": main["temp_max"],
        "pressure_hpa": main["pressure"],
        "humidity_pct": main["humidity"],
        "visibility_mi": visibility_mi,
        "wind_speed_mph": wind["speed"],
        "wind_direction_deg": wind.get("deg", 0),
        "wind_gust_mph": wind.get("gust", 0.0),
        "cloud_cover_pct": data.get("clouds", {}).get("all", 0),
        "weather_main": weather["main"],
        "weather_desc": weather["description"],
    }


@stamina.retry(on=httpx.HTTPStatusError, attempts=3)
@log_api_call(logger)
def get_openweathermap_forecast_hourly(
    client: httpx.Client, grid_id: str
) -> dict[str, str | float]:
    """Get hourly forecast from OpenWeatherMap 5-Day Forecast 2.5 API.

    Fetches the next hour's forecast data from the 5-day/3-hour forecast API.
    The API returns forecasts in 3-hour intervals, so we take the first period
    which represents the next 3-hour window.

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

    response = client.get(
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

    # Extract first forecast period (next 3-hour window)
    forecast = data["list"][0]
    main = forecast["main"]
    wind = forecast["wind"]
    weather = forecast["weather"][0]

    # Convert visibility from meters to miles
    visibility_mi = forecast.get("visibility", 0) / 1609.34

    # Extract city coordinates from response
    city = data["city"]

    return {
        "latitude": city["coord"]["lat"],
        "longitude": city["coord"]["lon"],
        "timestamp": forecast["dt"],
        "dt_txt": forecast["dt_txt"],
        "temperature_f": main["temp"],
        "feels_like_f": main["feels_like"],
        "temp_min_f": main["temp_min"],
        "temp_max_f": main["temp_max"],
        "pressure_hpa": main["pressure"],
        "humidity_pct": main["humidity"],
        "visibility_mi": visibility_mi,
        "wind_speed_mph": wind["speed"],
        "wind_direction_deg": wind.get("deg", 0),
        "wind_gust_mph": wind.get("gust", 0.0),
        "cloud_cover_pct": forecast.get("clouds", {}).get("all", 0),
        "prob_precip_pct": forecast.get("pop", 0.0) * 100,  # Convert 0-1 to 0-100
        "weather_main": weather["main"],
        "weather_desc": weather["description"],
    }


@stamina.retry(on=httpx.HTTPStatusError, attempts=3)
@log_api_call(logger)
async def get_openweathermap_forecast_hourly_async(
    client: httpx.AsyncClient, grid_id: str
) -> dict[str, str | float]:
    """Async version of `get_openweathermap_forecast_hourly`."""
    api_key = _get_api_key()
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

    response = await client.get(
        FORECAST_URL,
        params={
            "lat": lat,
            "lon": lon,
            "appid": api_key,
            "units": "imperial",
            "cnt": 1,
        },
    )
    response.raise_for_status()
    data = response.json()

    forecast = data["list"][0]
    main = forecast["main"]
    wind = forecast["wind"]
    weather = forecast["weather"][0]
    visibility_mi = forecast.get("visibility", 0) / 1609.34
    city = data["city"]

    return {
        "latitude": city["coord"]["lat"],
        "longitude": city["coord"]["lon"],
        "timestamp": forecast["dt"],
        "dt_txt": forecast["dt_txt"],
        "temperature_f": main["temp"],
        "feels_like_f": main["feels_like"],
        "temp_min_f": main["temp_min"],
        "temp_max_f": main["temp_max"],
        "pressure_hpa": main["pressure"],
        "humidity_pct": main["humidity"],
        "visibility_mi": visibility_mi,
        "wind_speed_mph": wind["speed"],
        "wind_direction_deg": wind.get("deg", 0),
        "wind_gust_mph": wind.get("gust", 0.0),
        "cloud_cover_pct": forecast.get("clouds", {}).get("all", 0),
        "prob_precip_pct": forecast.get("pop", 0.0) * 100,
        "weather_main": weather["main"],
        "weather_desc": weather["description"],
    }
