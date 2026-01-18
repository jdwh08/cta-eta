"""Open-Meteo API client for supplementary weather variables.

This module provides functions to fetch supplementary weather variables from
Open-Meteo that are not available in NWS hourly forecasts. These include:
- Visibility
- Snow depth
- Surface pressure
- Wind gusts
- Apparent temperature
- Rain, showers, snowfall

Rate limit calculation:
- ~50 unique weather grid points (from station deduplication)
- 2 calls per hour (every 30 minutes)
- 24 hours per day
- Total: 50 * 2 * 24 = 2,400 calls/day
- Well under Open-Meteo's 10,000/day limit
"""

from typing import Final

import httpx
import stamina
from dotenv import load_dotenv

from cta_eta.config import load_config
from cta_eta.weather_grid_cache import OpenMeteoGridCache, get_open_meteo_grid_cache

load_dotenv()

# Open-Meteo API endpoint
OPEN_METEO_URL: Final[str] = "https://api.open-meteo.com/v1/forecast"

# Module-level httpx.Client for connection pooling
client = httpx.Client()

# Module-level grid cache singleton
_open_meteo_grid_cache: OpenMeteoGridCache | None = None


def _get_grid_cache() -> OpenMeteoGridCache:
    """Get or create the Open-Meteo grid cache singleton.

    Returns:
        OpenMeteoGridCache instance for caching grid identifiers

    """
    global _open_meteo_grid_cache
    if _open_meteo_grid_cache is None:
        config = load_config()
        _open_meteo_grid_cache = get_open_meteo_grid_cache(config)
    return _open_meteo_grid_cache


@stamina.retry(on=httpx.HTTPStatusError, attempts=10)
def get_open_meteo_current(
    station_id: str, latitude: float, longitude: float
) -> dict[str, str | float]:
    """Get current supplementary weather variables from Open-Meteo.

    Fetches supplementary weather variables not provided by NWS hourly forecasts.
    Uses cached grid identifiers to reduce API calls and stay under rate limits.

    Args:
        station_id: Unique station identifier for grid caching
        latitude: Latitude coordinate
        longitude: Longitude coordinate

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
    # Get cached grid identifier (lazy discovery on first access)
    grid_cache = _get_grid_cache()
    grid_id = grid_cache.get_grid_identifier(station_id, latitude, longitude)

    # Parse grid identifier to get actual coordinates used by API
    grid_lat, grid_lon = grid_id.split(",")

    response = client.get(
        OPEN_METEO_URL,
        params={
            "latitude": float(grid_lat),
            "longitude": float(grid_lon),
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
