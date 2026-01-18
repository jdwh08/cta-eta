"""Station-to-weather grid mapping infrastructure.

This module provides cached mapping between CTA stations and unique weather grid
points to minimize API calls and stay within rate limits.

The key insight: ~300 CTA stations can be reduced to ~50 unique weather grid
points by rounding coordinates to 2 decimal places (~1.1km precision at Chicago's
latitude). This allows us to call weather APIs once per grid point instead of
once per station.

Coordinate rounding rationale:
- 2 decimal places = ~1.1km precision at Chicago's latitude
- Balances granularity (weather varies slowly) with API call reduction
- Too fine (3+ decimals) defeats deduplication purpose
- Too coarse (1 decimal = ~11km) loses spatial resolution
"""

import logging
from pathlib import Path

from dotenv import load_dotenv
from src.cta_eta.api_stations import get_stations
from src.cta_eta.cache import CachedData
from src.cta_eta.config import load_config

load_dotenv()

logger = logging.getLogger(__name__)

# Type aliases for clarity
WeatherGridPoint = dict[str, float]  # {"latitude": float, "longitude": float}
StationWeatherMapping = dict[str, list[str]]  # {"lat,lon": ["station_id1", ...]}


def _fetch_unique_weather_coordinates() -> list[WeatherGridPoint]:
    """Fetch unique weather grid points from CTA stations.

    Private helper function (not cached itself, used by CachedData).

    Returns:
        List of unique weather grid points with 2 decimal precision

    """
    stations = get_stations()

    # Extract unique (latitude, longitude) pairs rounded to 2 decimals
    unique_coords = {
        (round(station["latitude"], 2), round(station["longitude"], 2))
        for station in stations
    }

    # Convert to list of dicts
    grid_points = [
        {"latitude": lat, "longitude": lon} for lat, lon in sorted(unique_coords)
    ]

    logger.info(
        f"Found {len(grid_points)} unique weather grid points "
        f"from {len(stations)} CTA stations"
    )

    return grid_points


def _fetch_station_to_grid_mapping() -> StationWeatherMapping:
    """Fetch mapping from weather grid points to station IDs.

    Private helper function (not cached itself, used by CachedData).

    Returns:
        Dict mapping "lat,lon" strings to lists of station IDs

    """
    stations = get_stations()

    # Group stations by rounded coordinates
    mapping: dict[str, list[str]] = {}
    for station in stations:
        lat = round(station["latitude"], 2)
        lon = round(station["longitude"], 2)
        key = f"{lat},{lon}"

        if key not in mapping:
            mapping[key] = []
        mapping[key].append(station["id"])

    logger.info(f"Mapped {len(stations)} stations to {len(mapping)} unique grid points")

    return mapping


def get_weather_grid_points() -> list[WeatherGridPoint]:
    """Get unique weather grid points with caching.

    Public function using CachedData with 7-day TTL. Only calls Chicago Data
    Portal API when cache expires.

    Returns:
        List of unique weather grid points (reduced from ~300 stations to ~50)

    """
    config = load_config()

    cached_data = CachedData(
        cache_file=Path(config["cache"]["directory"]) / "weather_grid_points.json",
        ttl=int(config["cache"]["weather_mapping_ttl"]),
        fetch_fn=_fetch_unique_weather_coordinates,
    )

    return cached_data.get()


def get_station_to_grid_mapping() -> StationWeatherMapping:
    """Get station-to-grid mapping with caching.

    Public function using CachedData with 7-day TTL. Maps each weather grid
    point to the list of station IDs that share that coordinate.

    Returns:
        Dict mapping "lat,lon" strings to lists of station IDs

    """
    config = load_config()

    cached_data = CachedData(
        cache_file=Path(config["cache"]["directory"]) / "station_weather_mapping.json",
        ttl=int(config["cache"]["weather_mapping_ttl"]),
        fetch_fn=_fetch_station_to_grid_mapping,
    )

    return cached_data.get()
