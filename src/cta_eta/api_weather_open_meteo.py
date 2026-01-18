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

load_dotenv()

# Open-Meteo API endpoint
OPEN_METEO_URL: Final[str] = "https://api.open-meteo.com/v1/forecast"

# Module-level httpx.Client for connection pooling
client = httpx.Client()


@stamina.retry(on=httpx.HTTPStatusError, attempts=10)
def get_open_meteo_current(latitude: float, longitude: float) -> dict[str, str | float]:
    """Get current supplementary weather variables from Open-Meteo.

    Fetches supplementary weather variables not provided by NWS hourly forecasts.

    Args:
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
    response = client.get(
        OPEN_METEO_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
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
