"""OpenWeatherMap API client for fallback weather data.

This module provides weather data from OpenWeatherMap's APIs
as a fallback source when primary weather APIs (NWS, Open-Meteo) fail.

CAUTION: Our OpenWeatherMap key has a very restrictive 1,000 calls/day limit
for the One Call 3.0 API.
Use 2.5 API for current weather and 2.5 Forecast API for forecast as much as possible.
This module should ONLY be called when primary weather sources are unavailable.
Do not use for routine weather collection.
"""

# TODO(jdwh08): Change this from only One Call 3.0 API to Current Weather / Forecast 2.5 API.

import os
from typing import Final

import httpx
import stamina
from dotenv import load_dotenv

load_dotenv()

# Module-level client for connection pooling
client = httpx.Client()

# CURRENT WEATHER API:
# Use to get the current weather information.

# https://api.openweathermap.org/data/2.5/weather?lat=42.04473&lon=-87.68254&units=imperial&appid=...
# NOTE: Units must be converted.
# - Temprature fields are in Fahrenheit.
# - Timestamps are Unix UTC (change to America/Chicago datetime)
# - Wind speeds are meters/second (convert to mph)
# - Visibility is meters (convert to miles)
# - Precipitation fields are in mm (convert to inches)
# - Cloud coverage is in %
# - Humidity is in %
# Example Output:
"""
{
  "coord": {
    "lon": -87.6825,
    "lat": 42.0447
  },
  "weather": [
    {
      "id": 803,
      "main": "Clouds",
      "description": "broken clouds",
      "icon": "04d"
    }
  ],
  "base": "stations",
  "main": {
    "temp": 18.18,
    "feels_like": 6.37,
    "temp_min": 15.93,
    "temp_max": 19.98,
    "pressure": 1014,
    "humidity": 66,
    "sea_level": 1014,
    "grnd_level": 990
  },
  "visibility": 10000,
  "wind": {
    "speed": 10.36,
    "deg": 220
  },
  "clouds": {
    "all": 75
  },
  "dt": 1768772329,
  "sys": {
    "type": 2,
    "id": 2095520,
    "country": "US",
    "sunrise": 1768742085,
    "sunset": 1768776430
  },
  "timezone": -21600,
  "id": 4891382,
  "name": "Evanston",
  "cod": 200
}
"""

# FORECAST API:
# https://api.openweathermap.org/data/2.5/forecast/hourly?lat=42.04473&lon=-87.68254&units=imperial&appid=...
# Use to get the hourly forecast for the next 48 hours; focus particularly on the next hour.
# NOTE: Units must be converted.
# - Temprature fields are in Fahrenheit.
# - Timestamps are Unix UTC (change to America/Chicago datetime)
# - Wind speeds are meters/second (convert to mph)
# - Visibility is meters (convert to miles)

# Example Output:
"""
{
  "cod": "200",
  "message": 0,
  "cnt": 96,
  "list": [
    {
      "dt": 1768773600,
      "main": {
        "temp": 18.54,
        "feels_like": 6.21,
        "temp_min": 18.54,
        "temp_max": 20.23,
        "pressure": 1014,
        "sea_level": 1014,
        "grnd_level": 990,
        "humidity": 62,
        "temp_kf": -0.94
      },
      "weather": [
        {
          "id": 803,
          "main": "Clouds",
          "description": "broken clouds",
          "icon": "04d"
        }
      ],
      "clouds": {
        "all": 80
      },
      "wind": {
        "speed": 11.36,
        "deg": 238,
        "gust": 18.16
      },
      "visibility": 10000,
      "pop": 0,
      "sys": {
        "pod": "d"
      },
      "dt_txt": "2026-01-18 22:00:00"
    },
    ...
"""


# ONECALL API:
ONECALL_URL: Final[str] = "https://api.openweathermap.org/data/3.0/onecall"

# NOTE: HIGH IMPORTANCE:
# WE ARE RATE LIMITING THIS VERY TIGHTLY; ONLY 1000 CALLS PER DAY.
# USE SPARINGLY AS FALLBACK ONLY.
# THIS SHOULD ONLY BE USED IF OTHER WEATHER SOURCES ARE UNAVAILABLE
# AND SHOULD ONLY BE USED TO GET PRECIPITATION DATA.


@stamina.retry(on=httpx.HTTPStatusError, attempts=10)
def get_openweathermap_current(
    latitude: float, longitude: float
) -> dict[str, str | float]:
    """Get current weather data from OpenWeatherMap One Call 3.0 API.

    CAUTION: 1k/day limit. Use sparingly as fallback only.

    Args:
        latitude: Latitude coordinate
        longitude: Longitude coordinate

    Returns:
        Normalized weather data dictionary with keys:
        - timestamp: Unix timestamp of weather observation
        - temperature_f: Temperature in Fahrenheit
        - feels_like_f: Apparent temperature in Fahrenheit
        - pressure_hpa: Atmospheric pressure in hPa
        - humidity_pct: Relative humidity percentage
        - dewpoint_f: Dew point temperature in Fahrenheit
        - cloud_cover_pct: Cloud coverage percentage
        - visibility_m: Visibility in meters
        - wind_speed_mph: Wind speed in mph
        - wind_direction_deg: Wind direction in degrees
        - weather_desc: Weather condition description

    Raises:
        ValueError: If OPENWEATHERMAP_API_KEY environment variable not set
        httpx.HTTPStatusError: For HTTP errors (after 10 retry attempts)

    """
    api_key = os.getenv("OPENWEATHERMAP_API_KEY")
    if not api_key:
        msg = "OPENWEATHERMAP_API_KEY environment variable not set"
        raise ValueError(msg)

    response = client.get(
        ONECALL_URL,
        params={
            "lat": latitude,
            "lon": longitude,
            "appid": api_key,
            "exclude": "minutely,hourly,daily,alerts",
            "units": "imperial",
        },
    )
    response.raise_for_status()

    data = response.json()
    current = data["current"]

    return {
        "timestamp": current["dt"],
        "temperature_f": current["temp"],
        "feels_like_f": current["feels_like"],
        "pressure_hpa": current["pressure"],
        "humidity_pct": current["humidity"],
        "dewpoint_f": current["dew_point"],
        "cloud_cover_pct": current["clouds"],
        "visibility_m": current["visibility"],
        "wind_speed_mph": current["wind_speed"],
        "wind_direction_deg": current["wind_deg"],
        "weather_desc": current["weather"][0]["description"],
    }
