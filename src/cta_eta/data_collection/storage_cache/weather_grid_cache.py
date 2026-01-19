"""Lazy discovery weather grid caching for API-native grid mappings.

This module provides grid caching that learns API-specific grid identifiers from
actual API responses, rather than pre-computing grid points. This approach:
- Lets NWS and Open-Meteo APIs naturally determine their own grid systems
- Caches API-specific grid identifiers for instant lookups during polling
- Enables zero-waste weather polling by reducing ~300 stations to ~50 grid points
- Persists mappings across daemon restarts using CachedData infrastructure

Grid identifier formats:
- NWS: "LOT/85,67" (office/grid_x,grid_y)
- Open-Meteo: "41.88,-87.63" (rounded lat,lon coordinates)
"""

import json
import logging
import time
from pathlib import Path

import httpx
import stamina

from cta_eta.data_collection.apis.api_weather_nws import discover_nws_grid
from cta_eta.data_collection.apis.api_weather_open_meteo import discover_open_meteo_grid
from cta_eta.data_collection.storage_cache.cache import CachedData

logger = logging.getLogger(__name__)


class WeatherGridCache:
    """Base class for API-specific weather grid caching with lazy discovery.

    Provides generic interface for caching station_id → grid_identifier mappings.
    Grid identifiers are discovered lazily from API responses as stations are
    encountered during polling.

    Attributes:
        _cache: CachedData instance managing dict[str, str] mapping
        _cache_file: Path to JSON cache file
        _ttl: Time-to-live in seconds before refresh needed

    """

    def __init__(self, cache_file: Path, ttl: int) -> None:
        """Initialize grid cache with file path and TTL.

        Args:
            cache_file: Path to JSON cache file for persistence
            ttl: Time-to-live in seconds before refresh needed

        """
        self._cache_file = cache_file
        self._ttl = ttl
        self._cache: CachedData[dict[str, str]] = CachedData(
            cache_file=cache_file,
            ttl=ttl,
            fetch_fn=dict,  # Empty dict for lazy population
        )

    def get_grid_identifier(
        self, station_id: str, latitude: float, longitude: float
    ) -> str:
        """Get cached grid identifier or discover from API if missing.

        Args:
            station_id: Unique station identifier
            latitude: Station latitude
            longitude: Station longitude

        Returns:
            API-specific grid identifier string

        Raises:
            httpx.HTTPStatusError: If API discovery fails after retries
            NotImplementedError: If subclass doesn't implement _discover_grid

        """
        # Get current cache mapping
        mapping = self._cache.get()

        # Return cached identifier if exists
        if station_id in mapping:
            logger.debug(f"Cache hit for station {station_id}: {mapping[station_id]}")
            return mapping[station_id]

        # Cache miss: discover from API
        logger.info(f"Cache miss for station {station_id}, discovering grid from API")
        grid_id = self._discover_grid(latitude, longitude)

        # Update cache with new mapping
        mapping[station_id] = grid_id
        self._save_mapping(mapping)

        logger.info(f"Discovered and cached grid {grid_id} for station {station_id}")
        return grid_id

    def _discover_grid(self, latitude: float, longitude: float) -> str:
        """Discover grid identifier from API for given coordinates.

        Args:
            latitude: Coordinate latitude
            longitude: Coordinate longitude

        Returns:
            API-specific grid identifier

        Raises:
            NotImplementedError: Must be implemented by subclass

        """
        msg = "Subclass must implement _discover_grid"
        raise NotImplementedError(msg)

    def _save_mapping(self, mapping: dict[str, str]) -> None:
        """Save updated mapping to cache file.

        Args:
            mapping: Updated station_id → grid_identifier mapping

        """
        # Update cache's internal memory cache and persist to file
        cache_data = {
            "data": mapping,
            "cached_at": time.time(),
            "ttl": self._ttl,
        }

        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            with self._cache_file.open("w") as f:
                json.dump(cache_data, f, indent=2)
            logger.debug(f"Saved updated mapping to {self._cache_file}")
        except OSError as e:
            msg = f"Failed to save mapping to {self._cache_file}: {e}"
            logger.exception(msg)


class NWSGridCache(WeatherGridCache):
    """NWS-specific grid cache with lazy discovery from forecast URLs.

    Discovers NWS grid identifiers by calling the points API and extracting
    gridpoint information from the forecast URL. Grid identifiers have format
    "LOT/85,67" where LOT is the office ID and 85,67 are grid coordinates.

    """

    def __init__(self, cache_file: Path, ttl: int) -> None:
        """Initialize NWS grid cache.

        Args:
            cache_file: Path to JSON cache file (nws_grid_mapping.json)
            ttl: Time-to-live in seconds before refresh needed

        """
        super().__init__(cache_file, ttl)
        self._client = httpx.Client(
            headers={
                "User-Agent": "(cta-eta, contact@example.com)"
            }  # Will be overridden by actual config
        )

    @stamina.retry(on=httpx.HTTPStatusError, attempts=10)
    def _discover_grid(self, latitude: float, longitude: float) -> str:
        """Discover NWS grid identifier from points API.

        Calls NWS points API to get forecast URL, then extracts gridpoint
        information in format "OFFICE/GRID_X,GRID_Y".

        Args:
            latitude: Coordinate latitude
            longitude: Coordinate longitude

        Returns:
            NWS grid identifier (e.g., "LOT/85,67")

        Raises:
            httpx.HTTPStatusError: If API request fails after retries

        """
        grid_id = discover_nws_grid(self._client, latitude, longitude)
        return grid_id


class OpenMeteoGridCache(WeatherGridCache):
    """Open-Meteo-specific grid cache with lazy discovery from API responses.

    Discovers Open-Meteo grid identifiers by making a minimal API request and
    extracting the actual coordinates used by the API. Open-Meteo rounds/snaps
    coordinates to their internal grid, so we cache these actual values.

    """

    def __init__(self, cache_file: Path, ttl: int) -> None:
        """Initialize Open-Meteo grid cache.

        Args:
            cache_file: Path to JSON cache file (open_meteo_grid_mapping.json)
            ttl: Time-to-live in seconds before refresh needed

        """
        super().__init__(cache_file, ttl)
        self._client = httpx.Client()

    @stamina.retry(on=httpx.HTTPStatusError, attempts=10)
    def _discover_grid(self, latitude: float, longitude: float) -> str:
        """Discover Open-Meteo grid identifier from API response.

        Makes minimal API request to Open-Meteo and extracts the actual
        coordinates used by the API (they round/snap to their grid).

        Args:
            latitude: Coordinate latitude
            longitude: Coordinate longitude

        Returns:
            Open-Meteo grid identifier (e.g., "41.88,-87.63")

        Raises:
            httpx.HTTPStatusError: If API request fails after retries

        """
        grid_id = discover_open_meteo_grid(self._client, latitude, longitude)
        return grid_id


def get_nws_grid_cache(config: dict) -> NWSGridCache:
    """Create an NWS grid cache configured from `config`.

    Args:
        config: Configuration dict with cache section

    Returns:
        Configured NWSGridCache instance

    """
    cache_file = Path(str(config["cache"]["directory"])) / "nws_grid_mapping.json"
    ttl = int(config["cache"]["weather_mapping_ttl"])
    return NWSGridCache(cache_file, ttl)


def get_open_meteo_grid_cache(config: dict) -> OpenMeteoGridCache:
    """Create an Open-Meteo grid cache configured from `config`.

    Args:
        config: Configuration dict with cache section

    Returns:
        Configured OpenMeteoGridCache instance

    """
    cache_file = (
        Path(str(config["cache"]["directory"])) / "open_meteo_grid_mapping.json"
    )
    ttl = int(config["cache"]["weather_mapping_ttl"])
    return OpenMeteoGridCache(cache_file, ttl)
