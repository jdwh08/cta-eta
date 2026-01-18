"""CTA Stations and Weather API Example Code."""

# Get stations and their coordinates
import csv
import os
import time
from pathlib import Path

import httpx
import stamina
from dotenv import load_dotenv

load_dotenv()

stations_url = "https://data.cityofchicago.org/api/v3/views/3tzw-cg4m/query.json"

# get coordinates for all stations
client = httpx.Client()


@stamina.retry(on=httpx.HTTPStatusError, attempts=10)
def get_stations() -> list[dict[str, str | float]]:
    """Get the coordinates for all CTA stations."""
    stations = client.get(
        stations_url,
        headers={
            "Content-Type": "application/json",
            "X-App-Token": os.getenv("CHIDATA_APP_TOK") or "",
            "X-App-Secret": os.getenv("CHIDATA_APP_SECRET") or "",
        },
    )
    stations.raise_for_status()
    stations = stations.json()

    stations_list: list[dict[str, str | float]] = [
        {
            "id": station["station_id"],
            "lines": station["lines"],
            "name": station["longname"],
            "address": station["address"],
            "latitude": station["the_geom"]["coordinates"][1],
            "longitude": station["the_geom"]["coordinates"][0],
        }
        for station in stations
    ]
    return stations_list


"""
Example output:
[
    {
        "id": "900",
        "lines": "Red, Yellow, Purple, Evanston Express",
        "name": "Howard",
        "address": "1649 W. Howard Street",
        "latitude": 42.01906322017403,
        "longitude": -87.6728924507902,
    },
    ...
]
"""

# Get weather for all stations
weather_url = "https://api.open-meteo.com/v1/forecast"


# It turns out you can do one API call where you have a big list of latitudes and longitudes (comma separated) and get the weather for all of them at once.
# It doesn't remove the API call limiit (since this gets treated as 159.2 API calls), but it would be a lot more efficient.
# We should use this if possible.


@stamina.retry(on=httpx.HTTPStatusError, attempts=10)
def get_weather(latitude: float, longitude: float) -> dict[str, str | float]:
    """Get the weather for a given latitude and longitude."""
    weather = client.get(
        weather_url,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,rain,showers,snowfall,weather_code,surface_pressure,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
            "timezone": "America/Chicago",
            "forecast_days": 1,
            "wind_speed_unit": "mph",
            "temperature_unit": "fahrenheit",
            "precipitation_unit": "inch",
        },
    )
    weather.raise_for_status()
    weather = weather.json()

    weather_info: dict[str, str | float] = {
        "latitude_weather": weather["latitude"],
        "longitude_weather": weather["longitude"],
        "timestamp": weather["current"]["time"],
        "interval": weather["current"]["interval"],
        "temperature": weather["current"]["temperature_2m"],
        "relative_humidity": weather["current"]["relative_humidity_2m"],
        "apparent_temp": weather["current"]["apparent_temperature"],
        "rain": weather["current"]["rain"],
        "showers": weather["current"]["showers"],
        "snowfall": weather["current"]["snowfall"],
        "weather_code": weather["current"]["weather_code"],
        "cloud_cover": weather["current"]["cloud_cover"],
        "surface_pressure": weather["current"]["surface_pressure"],
        "wind_speed": weather["current"]["wind_speed_10m"],
        "wind_direction": weather["current"]["wind_direction_10m"],
        "wind_gusts": weather["current"]["wind_gusts_10m"],
    }
    return weather_info


weathers: list[dict[str, str | float]] = []

stations_list = get_stations()
for station in stations_list:
    weather = get_weather(station["latitude"], station["longitude"])
    weathers.append(weather)
    time.sleep(1)

# Join weather to stations; note that they have the same position indices
stations_weather: list[dict[str, str | float]] = [
    {**station, **weather}
    for station, weather in zip(stations_list, weathers, strict=True)
]

# Write stations_weather to a CSV file
with Path("stations_weather.csv").open("w") as f:
    writer = csv.writer(f)
    writer.writerow(stations_weather[0].keys())
    for station_weather in stations_weather:
        writer.writerow(station_weather.values())


# Every X time period (1 day or longer), after cache is expired, we need to run the full pull of stations and weather.
# We then only pull the unique weather stations (~39) rather than all train stations (~146), bringing us under the RPD limit.

########################################################
# Next Hour Weather Forecast
# We'll get this from the National Weather Service to not overload open-meteo.
# NOTE(jdwh08): National Weather Service API is free without rate limit, BUT don't abuse it AND use a header
# User-Agent: (cta-eta-weather, jdwh08s@gmail.com)

# 1. Get the forecast info from Lat/Long Points:
# https://api.weather.gov/points/41.7224,-87.6244

# 2. Get the hourly forecast from the forecastHourly endpoint:
# e.g., fh_url = response.json()["properties"]["forecastHourly"]
# call this url

# e.g., forecast_hourly = response.json()["properties"]["periods"][0-...]
# should provide startTime/endTime, temperature, ["probabilityOfPrecipitation"]["value"], ["dewpoint"]["value"]
# e.g., ["relativeHumidity"]["value"], ["windSpeed"], ["windDirection"], ["shortForecast"]
# note that we should check the units for dewpoint and temperatureUnit so that they are all in fahrenheit instead of wmoUnit:degC
# windspeed should also be extracted from string ("20 mph" -> 20)


# OpenMeteo gives more detailed hourly forecasts for stuff like snow depth, surface pressure, visibility, showers, snowfall, rain, apparent temperature, wind direction, wind gusts.
# https://api.open-meteo.com/v1/forecast?latitude=41.72237598&longitude=-87.62441475&hourly=snow_depth,surface_pressure,visibility,showers,snowfall,rain,apparent_temperature,wind_gusts_10m&models=gfs_hrrr&timezone=America%2FChicago&forecast_days=3&wind_speed_unit=mph&temperature_unit=fahrenheit&precipitation_unit=inch&forecast_minutely_15=4&past_minutely_15=1
# which we should leverage and combine with NWS hourly forecasts.
# Note that due to the 10k api call limit we need to do the same rate preservation trick as before
# map stations onto weather stations and only call the weather stations. probably can get away with using the same mapping here.
# should be 24hr * ~50 (39) weather stations * 2 calls = 2400 calls, which is well under the limit.
