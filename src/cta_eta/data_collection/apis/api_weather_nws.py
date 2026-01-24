"""National Weather Service (NWS) API client for hourly weather forecasts.

This module provides stateless async functions to fetch hourly weather forecasts from the
National Weather Service API at weather.gov.

API Documentation: https://www.weather.gov/documentation/services-web-api
Rate Limit: No official rate limit, but requires proper User-Agent header per API policy.

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
from cta_eta.data_collection.config import get_config_section, load_config
from cta_eta.data_collection.logging import get_logger, log_api_call
from cta_eta.data_collection.utils import (
    convert_celsius_to_fahrenheit,
    safe_get_nested,
    validate_lat_lon,
)

logger = get_logger(__name__)

# NWS API endpoints
NWS_POINTS_URL: Final[str] = "https://api.weather.gov/points"

config = load_config()
retry_config = get_config_section("retry", config=config)
MAX_RETRY_ATTEMPTS: Final[int] = int(retry_config.get("max_retry_attempts", 10))


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


@stamina.retry(on=httpx.HTTPStatusError, attempts=MAX_RETRY_ATTEMPTS)
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
    validate_lat_lon(latitude, longitude)
    response = await client.get(
        f"{NWS_POINTS_URL}/{latitude},{longitude}",
        headers=_get_auth_header(),
        # NWS may redirect overly-precise lat/lon to a canonical points URL.
        # Without following redirects, we'd try to parse the 301 response as JSON.
        follow_redirects=True,
    )
    response.raise_for_status()
    data = response.json()
    try:
        return str(
            safe_get_nested(data, "properties", "forecastHourly", api_name="NWS")
        )
    except ValueError:
        msg = "Failed to parse NWS forecast URL response."
        logger.exception(msg)
        raise


@stamina.retry(on=httpx.HTTPStatusError, attempts=MAX_RETRY_ATTEMPTS)
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


def _parse_hourly_forecast_response(
    data: dict[str, object],
) -> dict[str, str | float | None]:
    """Extract normalized hourly forecast from NWS API response.

    Caller is responsible for catching KeyError, TypeError, ValueError and
    logging or re-raising with context.
    """
    # Extract first period (current/next hour)
    properties = safe_get_nested(data, "properties", api_name="NWS")
    if not isinstance(properties, dict):
        msg = "NWS API response parsing error: 'properties' is not a dict"
        raise TypeError(msg)

    periods = properties.get("periods")  # ty:ignore[invalid-argument-type]
    if not periods or not isinstance(periods, list) or len(periods) == 0:
        msg = "NWS API response missing or empty 'periods' array"
        raise ValueError(msg)

    period = periods[0]
    if not isinstance(period, dict):
        msg = "NWS API response parsing error: period is not a dict"
        raise TypeError(msg)

    # Parse temperature (convert to Fahrenheit if needed)
    temperature = period.get("temperature")
    if temperature is None:
        msg = "NWS API response missing 'temperature' field in period"
        raise ValueError(msg)
    if not isinstance(temperature, (int, float)):
        msg = f"NWS API response 'temperature' is not numeric: {type(temperature).__name__}"
        raise TypeError(msg)

    temp_unit = period.get("temperatureUnit", "")
    if temp_unit == "wmoUnit:degC":
        temperature = convert_celsius_to_fahrenheit(float(temperature))
    else:
        temperature = float(temperature)

    # Parse dewpoint (convert to Fahrenheit if needed)
    dewpoint_obj = period.get("dewpoint")
    if not isinstance(dewpoint_obj, dict):
        msg = "NWS API response missing or invalid 'dewpoint' field in period"
        raise TypeError(msg)

    dewpoint = dewpoint_obj.get("value")
    if dewpoint is None:
        msg = "NWS API response missing 'dewpoint.value' field"
        raise ValueError(msg)
    if not isinstance(dewpoint, (int, float)):
        msg = f"NWS API response 'dewpoint.value' is not numeric: {type(dewpoint).__name__}"
        raise TypeError(msg)

    dewpoint_unit = dewpoint_obj.get("unitCode", "")
    if dewpoint_unit == "wmoUnit:degC":
        dewpoint = convert_celsius_to_fahrenheit(float(dewpoint))
    else:
        dewpoint = float(dewpoint)

    # Parse wind speed (extract numeric value from string like "20 mph")
    wind_speed_str = period.get("windSpeed", "")
    if not isinstance(wind_speed_str, str):
        wind_speed_str = str(wind_speed_str) if wind_speed_str is not None else ""
    wind_speed_match = re.match(r"(\d+)", wind_speed_str)
    wind_speed_mph = float(wind_speed_match.group(1)) if wind_speed_match else None

    # Build normalized response with safe access for optional fields
    prob_precip_obj = period.get("probabilityOfPrecipitation")
    prob_precip_value = (
        prob_precip_obj.get("value") if isinstance(prob_precip_obj, dict) else None
    )

    humidity_obj = period.get("relativeHumidity")
    humidity_value = (
        humidity_obj.get("value") if isinstance(humidity_obj, dict) else None
    )

    output = {
        "start_time": period.get("startTime"),
        "end_time": period.get("endTime"),
        "temperature_f": temperature,
        "prob_precip_pct": float(prob_precip_value)
        if prob_precip_value is not None
        else None,
        "dewpoint_f": dewpoint,
        "humidity_pct": float(humidity_value) if humidity_value is not None else None,
        "wind_speed_mph": wind_speed_mph,
        "wind_direction": period.get("windDirection"),
        "forecast_desc": period.get("shortForecast"),
    }
    return output


# TODO(jdwh08): make stamina obey settings from config.toml
@stamina.retry(on=httpx.HTTPStatusError, attempts=MAX_RETRY_ATTEMPTS)
@log_api_call(logger)
async def get_nws_hourly_forecast(
    client: httpx.AsyncClient, grid_id: str
) -> dict[str, str | int | float | None]:
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

    try:
        return _parse_hourly_forecast_response(data)
    except (KeyError, TypeError, ValueError) as e:
        msg = "Failed to parse NWS hourly forecast response, unexpected response structure."
        logger.exception(msg)
        raise ValueError(msg) from e
