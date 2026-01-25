"""Open-Meteo API client for supplementary weather variables.

This module provides stateless functions to fetch supplementary weather variables from
Open-Meteo that are not available in NWS hourly forecasts. These include:
- Visibility
- Snow depth
- Surface pressure
- Wind gusts
- Apparent temperature
- Rain, showers, snowfall

API Documentation: https://open-meteo.com/en/docs
Rate Limit: 10,000 calls/day (free tier)

Rate limit calculation:
- ~50 unique weather grid points (from station deduplication)
- 2 calls per hour (every 30 minutes)
- 24 hours per day
- Total: 50 * 2 * 24 = 2,400 calls/day
- Well under Open-Meteo's 10,000/day limit

All functions accept an httpx.AsyncClient parameter for dependency injection and proper
connection pooling management by the caller.
"""

from __future__ import annotations

from typing import Final

import httpx
import stamina

### OWN MODULES
from cta_eta.data_collection.config import load_config
from cta_eta.data_collection.logging import get_logger, log_api_call
from cta_eta.data_collection.utils import safe_get_nested, validate_lat_lon

logger = get_logger(__name__)

# Open-Meteo API endpoint
OPEN_METEO_URL: Final[str] = "https://api.open-meteo.com/v1/forecast"


config = load_config()
retry_config = config.get("retry", {})
_MAX_RETRY_ATTEMPTS: Final[int] = int(retry_config.get("max_retry_attempts", 10))

# NOTE(jdwh08): Keep discovery retries bounded:
# ? Discovery is a cold-start/cache-miss path where we
# ? may fan out across many stations. A few transient failures should not stall the
# ? whole cycle for minutes.
_DISCOVERY_ATTEMPTS: Final[int] = min(3, _MAX_RETRY_ATTEMPTS)
_CURRENT_ATTEMPTS: Final[int] = min(5, _MAX_RETRY_ATTEMPTS)

_RETRY_ON: Final[tuple[type[Exception], ...]] = (
    httpx.HTTPStatusError,
    httpx.RequestError,
)


def _parse_discover_grid_response(data: dict[str, object]) -> str:
    """Extract grid ID (lat,lon) from Open-Meteo Current Weather response.

    Caller is responsible for catching KeyError, TypeError, ValueError and
    logging or re-raising with context.
    """
    # Extract actual coordinates used by API
    actual_lat = safe_get_nested(data, "latitude", api_name="Open-Meteo")
    actual_lon = safe_get_nested(data, "longitude", api_name="Open-Meteo")

    if not isinstance(actual_lat, (int, float)) or not isinstance(
        actual_lon, (int, float)
    ):
        msg = (
            "Open-Meteo API response 'latitude' or 'longitude' is not numeric. "
            f"Got types: {type(actual_lat).__name__}, {type(actual_lon).__name__}"
        )
        raise TypeError(msg)
    return f"{actual_lat},{actual_lon}"


@stamina.retry(
    on=_RETRY_ON,
    attempts=_DISCOVERY_ATTEMPTS,
)
@log_api_call(logger)
async def discover_open_meteo_grid(
    client: httpx.AsyncClient, latitude: float, longitude: float
) -> str:
    """Discover Open-Meteo grid identifier from API response.

    Makes minimal API request to Open-Meteo and extracts the actual
    coordinates used by the API (they round/snap to their grid).

    Args:
        client: HTTP client for API requests
        latitude: Coordinate latitude (must be between -90 and 90)
        longitude: Coordinate longitude (must be between -180 and 180)

    Returns:
        Open-Meteo grid identifier (e.g., "41.88,-87.63")

    Raises:
        httpx.HTTPStatusError: If API request fails after retries
        ValueError: If latitude or longitude is out of valid range

    """
    validate_lat_lon(latitude, longitude)
    response = await client.get(
        OPEN_METEO_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m",  # Minimal parameter
            "timezone": "America/Chicago",
        },
    )
    response.raise_for_status()
    data = response.json()

    try:
        return _parse_discover_grid_response(data)
    except (KeyError, TypeError, ValueError):
        logger.exception("Failed to parse Open-Meteo grid discovery response")
        raise


def _parse_current_weather_response(
    data: dict[str, object],
) -> dict[str, str | float | None]:
    """Extract normalized current weather from Open-Meteo Current Weather response.

    Caller is responsible for catching KeyError, TypeError, ValueError and
    logging or re-raising with context.
    """
    # Extract current weather data
    latitude = safe_get_nested(data, "latitude", api_name="Open-Meteo")
    longitude = safe_get_nested(data, "longitude", api_name="Open-Meteo")
    current = safe_get_nested(data, "current", api_name="Open-Meteo")

    if not isinstance(current, dict):
        msg = "Open-Meteo API response 'current' is not a dict"
        raise TypeError(msg)

    if not isinstance(latitude, (int, float)) or not isinstance(
        longitude, (int, float)
    ):
        msg = (
            "Open-Meteo API response 'latitude' or 'longitude' is not numeric. "
            f"Got types: {type(latitude).__name__}, {type(longitude).__name__}"
        )
        raise TypeError(msg)

    timestamp = current.get("time")  # ty:ignore[invalid-argument-type]
    if timestamp is None:
        msg = "Open-Meteo API response missing 'current.time' field"
        raise ValueError(msg)

    # Convert visibility from feet to miles (API returns in feet)
    visibility_ft = current.get("visibility")  # ty:ignore[invalid-argument-type]
    visibility_mi = (
        float(visibility_ft) / 5280.0
        if visibility_ft is not None and isinstance(visibility_ft, (int, float))
        else None
    )

    output = {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "timestamp": str(timestamp),
        "visibility_mi": visibility_mi,
        "snow_depth_in": float(current.get("snow_depth"))  # ty:ignore[invalid-argument-type]
        if current.get("snow_depth") is not None  # ty:ignore[invalid-argument-type]
        else None,
        "surface_pressure_hpa": float(current.get("surface_pressure"))  # ty:ignore[invalid-argument-type]
        if current.get("surface_pressure") is not None  # ty:ignore[invalid-argument-type]
        else None,
        "wind_gusts_mph": float(current.get("wind_gusts_10m"))  # ty:ignore[invalid-argument-type]
        if current.get("wind_gusts_10m") is not None  # ty:ignore[invalid-argument-type]
        else None,
        "apparent_temp_f": float(current.get("apparent_temperature"))  # ty:ignore[invalid-argument-type]
        if current.get("apparent_temperature") is not None  # ty:ignore[invalid-argument-type]
        else None,
        "rain_in": float(current.get("rain"))  # ty:ignore[invalid-argument-type]
        if current.get("rain") is not None  # ty:ignore[invalid-argument-type]
        else None,
        "showers_in": float(current.get("showers"))  # ty:ignore[invalid-argument-type]
        if current.get("showers") is not None  # ty:ignore[invalid-argument-type]
        else None,
        "snowfall_in": float(current.get("snowfall"))  # ty:ignore[invalid-argument-type]
        if current.get("snowfall") is not None  # ty:ignore[invalid-argument-type]
        else None,
    }
    return output


@stamina.retry(
    on=_RETRY_ON,
    attempts=_CURRENT_ATTEMPTS,
)
@log_api_call(logger)
async def get_open_meteo_current(
    client: httpx.AsyncClient, grid_id: str
) -> dict[str, str | float | None]:
    """Get current supplementary weather variables from Open-Meteo for a known grid.

    Fetches supplementary weather variables not provided by NWS hourly forecasts.

    Args:
        client: HTTP client for API requests
        grid_id: Open-Meteo grid identifier (e.g., "41.88,-87.63")

    Returns:
        Dictionary with supplementary weather data:
        - latitude: Latitude of weather grid point
        - longitude: Longitude of weather grid point
        - timestamp: ISO timestamp of observation
        - visibility_mi: Visibility in miles
        - snow_depth_in: Snow depth in inches
        - surface_pressure_hpa: Surface pressure in hPa
        - wind_gusts_mph: Wind gusts in miles per hour
        - apparent_temp_f: Apparent/feels-like temperature in Fahrenheit
        - rain_in: Rain in inches
        - showers_in: Showers in inches
        - snowfall_in: Snowfall in inches

    Raises:
        httpx.HTTPStatusError: If API request fails after retries

    """
    if grid_id.count(",") != 1:
        msg = f"Invalid grid ID: {grid_id}"
        raise ValueError(msg)

    # Parse grid identifier to get actual coordinates used by API
    grid_lat, grid_lon = grid_id.split(",")
    try:
        lat = float(grid_lat)
        lon = float(grid_lon)
    except ValueError as e:
        msg = f"Invalid grid ID: {grid_id}"
        raise ValueError(msg) from e

    validate_lat_lon(lat, lon)

    response = await client.get(
        OPEN_METEO_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "visibility,snow_depth,surface_pressure,wind_gusts_10m,apparent_temperature,rain,showers,snowfall",
            "timezone": "America/Chicago",
            "forecast_days": 2,  # need to roll over to next day if current day is ending
            "wind_speed_unit": "mph",
            "temperature_unit": "fahrenheit",
            "precipitation_unit": "inch",
        },
    )
    response.raise_for_status()
    data = response.json()

    try:
        return _parse_current_weather_response(data)
    except (KeyError, TypeError, ValueError) as e:
        msg = f"Failed to parse Open-Meteo current weather response: {e}"
        logger.exception(msg)
        msg = "Open-Meteo API response structure unexpected."
        raise ValueError(msg) from e
