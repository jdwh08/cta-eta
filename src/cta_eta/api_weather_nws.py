"""National Weather Service (NWS) API client for hourly weather forecasts.

This module provides functions to fetch hourly weather forecasts from the
National Weather Service API at weather.gov. NWS has no rate limit but requires
a User-Agent header per their API policy.

Per NWS API policy, all requests must include:
User-Agent: (<app name>, <email address>)
"""

import os
import re
from typing import Final

import httpx
import stamina
from dotenv import load_dotenv

load_dotenv()

# NWS API endpoints
NWS_POINTS_URL: Final[str] = "https://api.weather.gov/points"
USER_AGENT: Final[str] = f"({os.getenv('APP_NAME')}, {os.getenv('EMAIL_ADDRESS')})"

# Module-level httpx.Client for connection pooling
client = httpx.Client(headers={"User-Agent": USER_AGENT})


@stamina.retry(on=httpx.HTTPStatusError, attempts=10)
def get_nws_forecast_url(latitude: float, longitude: float) -> str:
    """Get the forecastHourly URL for a given location from NWS.

    Args:
        latitude: Latitude coordinate
        longitude: Longitude coordinate

    Returns:
        URL string for hourly forecast endpoint

    Raises:
        httpx.HTTPStatusError: If API request fails after retries

    """
    response = client.get(f"{NWS_POINTS_URL}/{latitude},{longitude}")
    response.raise_for_status()
    data = response.json()
    return data["properties"]["forecastHourly"]


@stamina.retry(on=httpx.HTTPStatusError, attempts=10)
def get_nws_hourly_forecast(
    latitude: float, longitude: float
) -> dict[str, str | float]:
    """Get current hourly weather forecast from NWS.

    Fetches the first hourly forecast period from NWS, which represents the
    current/next hour's weather conditions.

    Args:
        latitude: Latitude coordinate
        longitude: Longitude coordinate

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
    # Get the forecastHourly URL for this location
    forecast_url = get_nws_forecast_url(latitude, longitude)

    # Fetch hourly forecast data
    response = client.get(forecast_url)
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
