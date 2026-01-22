"""Open-Meteo API client for supplementary weather variables.

This module provides stateless functions to fetch supplementary weather variables from
Open-Meteo that are not available in NWS hourly forecasts. These include:
- Visibility
- Snow depth
- Surface pressure
- Wind gusts
- Apparent temperature
- Rain, showers, snowfall

All functions accept an httpx.Client parameter for dependency injection and proper
connection pooling management by the caller.

Rate limit calculation:
- ~50 unique weather grid points (from station deduplication)
- 2 calls per hour (every 30 minutes)
- 24 hours per day
- Total: 50 * 2 * 24 = 2,400 calls/day
- Well under Open-Meteo's 10,000/day limit
"""

from __future__ import annotations

from typing import Final

import httpx
import stamina

### OWN MODULES
from cta_eta.data_collection.config import load_config
from cta_eta.data_collection.logging import get_logger, log_api_call

logger = get_logger(__name__)

# Open-Meteo API endpoint
OPEN_METEO_URL: Final[str] = "https://api.open-meteo.com/v1/forecast"

config = load_config()
retry_config = config.get("retry", {})
_MAX_RETRY_ATTEMPTS: Final[int] = int(retry_config.get("max_retry_attempts", 10))

# Keep discovery retries bounded: discovery is a cold-start/cache-miss path where we
# may fan out across many stations. A few transient failures should not stall the
# whole cycle for minutes.
_DISCOVERY_ATTEMPTS: Final[int] = min(3, _MAX_RETRY_ATTEMPTS)
_CURRENT_ATTEMPTS: Final[int] = min(5, _MAX_RETRY_ATTEMPTS)

_RETRY_ON: Final[tuple[type[Exception], ...]] = (
    httpx.HTTPStatusError,
    httpx.RequestError,
)


@stamina.retry(
    on=_RETRY_ON,
    attempts=_DISCOVERY_ATTEMPTS,
    timeout=20.0,
    wait_initial=0.2,
    wait_max=2.0,
    wait_jitter=0.5,
)
@log_api_call(logger)
def discover_open_meteo_grid(
    client: httpx.Client, latitude: float, longitude: float
) -> str:
    """Discover Open-Meteo grid identifier from API response.

    Makes minimal API request to Open-Meteo and extracts the actual
    coordinates used by the API (they round/snap to their grid).

    Args:
        client: HTTP client for API requests
        latitude: Coordinate latitude
        longitude: Coordinate longitude

    Returns:
        Open-Meteo grid identifier (e.g., "41.88,-87.63")

    Raises:
        httpx.HTTPStatusError: If API request fails after retries

    """
    # Make minimal request to Open-Meteo API
    response = client.get(
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

    # Extract actual coordinates used by API
    actual_lat = data["latitude"]
    actual_lon = data["longitude"]

    return f"{actual_lat},{actual_lon}"


@stamina.retry(
    on=_RETRY_ON,
    attempts=_DISCOVERY_ATTEMPTS,
    timeout=20.0,
    wait_initial=0.2,
    wait_max=2.0,
    wait_jitter=0.5,
)
@log_api_call(logger)
async def discover_open_meteo_grid_async(
    client: httpx.AsyncClient, latitude: float, longitude: float
) -> str:
    """Async version of `discover_open_meteo_grid`."""
    response = await client.get(
        OPEN_METEO_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m",
            "timezone": "America/Chicago",
        },
    )
    response.raise_for_status()
    data = response.json()
    return f"{data['latitude']},{data['longitude']}"


@stamina.retry(
    on=_RETRY_ON,
    attempts=_CURRENT_ATTEMPTS,
    timeout=45.0,
    wait_initial=0.2,
    wait_max=5.0,
    wait_jitter=1.0,
)
@log_api_call(logger)
def get_open_meteo_current(
    client: httpx.Client, grid_id: str
) -> dict[str, str | float]:
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

    response = client.get(
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

    # Extract current weather data
    current = data["current"]

    return {
        "latitude": data["latitude"],
        "longitude": data["longitude"],
        "timestamp": current["time"],
        "visibility_mi": current.get("visibility", 0.0)
        / 5280.0,  # Convert feet to miles
        "snow_depth_in": current.get("snow_depth", 0.0),
        "surface_pressure_hpa": current.get("surface_pressure", 0.0),
        "wind_gusts_mph": current.get("wind_gusts_10m", 0.0),
        "apparent_temp_f": current.get("apparent_temperature", 0.0),
        "rain_in": current.get("rain", 0.0),
        "showers_in": current.get("showers", 0.0),
        "snowfall_in": current.get("snowfall", 0.0),
    }


@stamina.retry(
    on=_RETRY_ON,
    attempts=_CURRENT_ATTEMPTS,
    timeout=45.0,
    wait_initial=0.2,
    wait_max=5.0,
    wait_jitter=1.0,
)
@log_api_call(logger)
async def get_open_meteo_current_async(
    client: httpx.AsyncClient, grid_id: str
) -> dict[str, str | float]:
    """Async version of `get_open_meteo_current`."""
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
        OPEN_METEO_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "visibility,snow_depth,surface_pressure,wind_gusts_10m,apparent_temperature,rain,showers,snowfall",
            "timezone": "America/Chicago",
            "forecast_days": 2,
            "wind_speed_unit": "mph",
            "temperature_unit": "fahrenheit",
            "precipitation_unit": "inch",
        },
    )
    response.raise_for_status()
    data = response.json()
    current = data["current"]

    return {
        "latitude": data["latitude"],
        "longitude": data["longitude"],
        "timestamp": current["time"],
        "visibility_mi": current.get("visibility", 0.0) / 5280.0,
        "snow_depth_in": current.get("snow_depth", 0.0),
        "surface_pressure_hpa": current.get("surface_pressure", 0.0),
        "wind_gusts_mph": current.get("wind_gusts_10m", 0.0),
        "apparent_temp_f": current.get("apparent_temperature", 0.0),
        "rain_in": current.get("rain", 0.0),
        "showers_in": current.get("showers", 0.0),
        "snowfall_in": current.get("snowfall", 0.0),
    }
