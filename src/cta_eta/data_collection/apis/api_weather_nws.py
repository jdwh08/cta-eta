"""National Weather Service (NWS) API client for hourly weather forecasts.

This module provides stateless async functions to fetch hourly weather forecasts from the
National Weather Service API at weather.gov. NWS has no rate limit but requires
a User-Agent header per their API policy.

Per NWS API policy, all requests must include:
User-Agent: (<app name>, <email address>)

All functions accept an httpx.AsyncClient parameter for dependency injection and proper
connection pooling management by the caller.
"""

from __future__ import annotations

import os
import re
from typing import Final

import httpx
import stamina

### OWN MODULES
from cta_eta.data_collection.logging import get_logger, log_api_call

logger = get_logger(__name__)

# NWS API endpoints
NWS_POINTS_URL: Final[str] = "https://api.weather.gov/points"


def _get_auth_header() -> dict[str, str]:
    """Get the auth header for the NWS api.

    Returns:
        Dictionary with the auth header

    Raises:
        ValueError: If NWS_APP_NAME or NWS_EMAIL is not set

    """
    app_name = os.getenv("NWS_APP_NAME")
    email = os.getenv("NWS_EMAIL")
    if not app_name or not email:
        msg = "NWS_APP_NAME and NWS_EMAIL must be set in environment variables"
        raise ValueError(msg)
    return {"User-Agent": f"({app_name}, {email})"}


@stamina.retry(on=httpx.HTTPStatusError, attempts=10)
@log_api_call(logger)
async def get_nws_forecast_url(
    client: httpx.AsyncClient, latitude: float, longitude: float
) -> str:
    """Get the forecastHourly URL for a given location from NWS.

    Args:
        client: HTTP client with proper User-Agent header configured
        latitude: Latitude coordinate
        longitude: Longitude coordinate

    Returns:
        URL string for hourly forecast endpoint

    Raises:
        httpx.HTTPStatusError: If API request fails after retries

    """
    response = await client.get(
        f"{NWS_POINTS_URL}/{latitude},{longitude}",
        headers=_get_auth_header(),
        # NWS may redirect overly-precise lat/lon to a canonical points URL.
        # Without following redirects, we'd try to parse the 301 response as JSON.
        follow_redirects=True,
    )
    response.raise_for_status()
    data = response.json()
    return data["properties"]["forecastHourly"]


@stamina.retry(on=httpx.HTTPStatusError, attempts=10)
@log_api_call(logger)
async def discover_nws_grid(
    client: httpx.AsyncClient, latitude: float, longitude: float
) -> str:
    """Discover NWS grid identifier from points API.

    Calls NWS points API to get forecast URL, then extracts gridpoint
    information in format "OFFICE/GRID_X,GRID_Y".

    Args:
        client: HTTP client with proper User-Agent header configured
        latitude: Coordinate latitude
        longitude: Coordinate longitude

    Returns:
        NWS grid identifier (e.g., "LOT/85,67")

    Raises:
        httpx.HTTPStatusError: If API request fails after retries
        ValueError: If forecast URL format is unexpected

    """
    # Get forecast URL from NWS points API
    forecast_url = await get_nws_forecast_url(client, latitude, longitude)

    # Extract gridpoint from URL: /gridpoints/LOT/85,67/forecast/hourly
    match = re.search(r"/gridpoints/([A-Z]+)/(\d+),(\d+)/", forecast_url)
    if not match:
        msg = f"Unexpected NWS forecast URL format: {forecast_url}"
        raise ValueError(msg)

    office = match.group(1)
    grid_x = match.group(2)
    grid_y = match.group(3)

    return f"{office}/{grid_x},{grid_y}"


@stamina.retry(on=httpx.HTTPStatusError, attempts=10)
@log_api_call(logger)
async def get_nws_hourly_forecast(
    client: httpx.AsyncClient, grid_id: str
) -> dict[str, str | float]:
    """Get current hourly weather forecast from NWS for a known grid.

    Fetches the first hourly forecast period from NWS, which represents the
    current/next hour's weather conditions.

    Args:
        client: HTTP client with proper User-Agent header configured
        grid_id: NWS grid identifier (e.g., "LOT/76,73")

    Returns:
        Dictionary with normalized weather data:
        - start_time: ISO timestamp for period start
        - end_time: ISO timestamp for period end
        - temperature_f: Temperature in Fahrenheit
        - prob_precip_pct: Probability of precipitation as percentage (0-100)
        - dewpoint_f: Dewpoint in Fahrenheit
        - humidity_pct: Relative humidity as percentage (0-100)
        - wind_speed_mph: Wind speed in miles per hour
        - wind_direction: Wind direction (e.g., "N", "NE", "S")
        - forecast_desc: Short forecast description

    Raises:
        httpx.HTTPStatusError: If API request fails after retries

    """
    # Construct forecast URL from grid identifier (e.g., "LOT/76,73")
    forecast_url = f"https://api.weather.gov/gridpoints/{grid_id}/forecast/hourly"

    # Fetch hourly forecast data
    response = await client.get(forecast_url, headers=_get_auth_header())
    response.raise_for_status()
    data = response.json()

    # Extract first period (current/next hour)
    period = data["properties"]["periods"][0]

    # Parse temperature (convert to Fahrenheit if needed)
    temperature = period["temperature"]
    temp_unit = period["temperatureUnit"]
    if temp_unit == "wmoUnit:degC":
        temperature = temperature * 9 / 5 + 32

    # Parse dewpoint (convert to Fahrenheit if needed)
    dewpoint = period["dewpoint"]["value"]
    dewpoint_unit = period["dewpoint"]["unitCode"]
    if dewpoint_unit == "wmoUnit:degC":
        dewpoint = dewpoint * 9 / 5 + 32

    # Parse wind speed (extract numeric value from string like "20 mph")
    wind_speed_str = period["windSpeed"]
    wind_speed_match = re.match(r"(\d+)", wind_speed_str)
    wind_speed = float(wind_speed_match.group(1)) if wind_speed_match else 0.0

    # Build normalized response
    return {
        "start_time": period["startTime"],
        "end_time": period["endTime"],
        "temperature_f": temperature,
        "prob_precip_pct": period["probabilityOfPrecipitation"]["value"] or 0.0,
        "dewpoint_f": dewpoint,
        "humidity_pct": period["relativeHumidity"]["value"] or 0.0,
        "wind_speed_mph": wind_speed,
        "wind_direction": period["windDirection"],
        "forecast_desc": period["shortForecast"],
    }
