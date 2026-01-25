"""Lazy discovery weather grid caching for API-native grid mappings.

This module provides grid caching that learns API-specific grid identifiers from
actual API responses, rather than pre-computing grid points. This approach:
- Lets NWS and Open-Meteo APIs naturally determine their own grid systems
- Caches API-specific grid identifiers for instant lookups during polling
- Enables zero-waste weather polling by reducing ~300 stations to ~50 grid points
- Persists mappings across daemon restarts using a persistent KV cache

Grid identifier formats:
- NWS: "LOT/85,67" (office/grid_x,grid_y)
- Open-Meteo: "41.88,-87.63" (canonicalized lat,lon returned by API)
- OpenWeatherMap: "41.88,-87.63" (rounded lat,lon returned by API)
"""

import logging
from pathlib import Path
from typing import Final

import httpx
import stamina

### OWN MODULES
from cta_eta.data_collection.apis.api_weather_nws import (
    _get_auth_header,
    discover_nws_grid,
)
from cta_eta.data_collection.config import get_config_section, load_config
from cta_eta.data_collection.storage_cache.kv_cache import PersistentKVCache

logger = logging.getLogger(__name__)

config = load_config()
retry_config = get_config_section("retry", config=config)
MAX_RETRY_ATTEMPTS: Final[int] = int(retry_config.get("max_retry_attempts", 10))


class WeatherGridCache:
    """Persistent station_id → grid_identifier mapping cache.

    For rate-limited providers (Open-Meteo, OpenWeatherMap), this cache intentionally
    does *not* perform discovery calls. The orchestrator is expected to:
    - read the mapping (returns None on cache miss/expiry)
    - perform the real weather API call using station coordinates
    - update the mapping using `set_grid_identifier()` from the real response

    NWS is the exception: forecasts require a gridpoint ID derived via the Points
    endpoint, so `NWSGridCache` provides a `resolve_grid_identifier()` helper.

    Attributes:
        _cache: PersistentKVCache instance managing station_id → grid_identifier mapping
        _ttl: Per-entry time-to-live in seconds (None means never expire)

    """

    def __init__(self, cache_file: Path, ttl: int) -> None:
        """Initialize grid cache with file path and TTL.

        Args:
            cache_file: Path to JSON cache file for persistence
            ttl: Per-entry time-to-live in seconds before re-discovery

        """
        self._ttl = ttl
        self._cache: PersistentKVCache[str] = PersistentKVCache(
            cache_file=cache_file, ttl=ttl
        )

    def get_grid_identifier(self, station_id: str) -> str | None:
        """Get cached grid identifier if present and not expired.

        Args:
            station_id: Unique station identifier

        Returns:
            API-specific grid identifier string, or None on cache miss/expiry.

        """
        grid_id = self._cache.get(station_id)
        if grid_id is not None:
            logger.debug(f"Cache hit for station {station_id}: {grid_id}")
        return grid_id

    def set_grid_identifier(self, station_id: str, grid_id: str) -> None:
        """Manually set/update a station → grid mapping."""
        self._cache.set(station_id, grid_id)

    def delete_station(self, station_id: str) -> None:
        """Remove a station from the mapping cache."""
        self._cache.delete(station_id)


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
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure async HTTP client is initialized with proper User-Agent header.

        Uses NWS API authentication header from environment variables (NWS_APP_NAME,
        NWS_EMAIL) as required by NWS API policy.

        """
        if self._client is None:
            try:
                headers = _get_auth_header()
            except ValueError:
                logger.exception(
                    "Failed to get NWS User-Agent header. "
                    "NWS_APP_NAME and NWS_EMAIL must be set in environment variables."
                )
                raise
            self._client = httpx.AsyncClient(headers=headers)
        return self._client

    async def aclose(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def resolve_grid_identifier(
        self, station_id: str, latitude: float, longitude: float
    ) -> str:
        """Resolve NWS grid identifier for a station, discovering on miss/expiry.

        Args:
            station_id: Unique station identifier
            latitude: Station latitude
            longitude: Station longitude

        Returns:
            NWS grid identifier (e.g., "LOT/85,67")

        Raises:
            httpx.HTTPStatusError: If API request fails after retries.

        """
        cached = self.get_grid_identifier(station_id)
        if cached is not None:
            return cached

        logger.info(
            f"Cache miss for station {station_id}, discovering NWS grid from Points API"
        )
        client = await self._ensure_client()
        grid_id = await self._discover_grid(client, latitude, longitude)
        self.set_grid_identifier(station_id, grid_id)
        return grid_id

    @stamina.retry(on=httpx.HTTPStatusError, attempts=MAX_RETRY_ATTEMPTS)
    async def _discover_grid(
        self, client: httpx.AsyncClient, latitude: float, longitude: float
    ) -> str:
        """Discover NWS grid identifier from Points API for given coordinates."""
        return await discover_nws_grid(client, latitude, longitude)


class OpenMeteoGridCache(WeatherGridCache):
    """Open-Meteo station → grid mapping cache.

    Open-Meteo returns less precise latitude longitude coordinates than input.
    They also will snap to their nearest weather station.

    The orchestrator is responsible for calling Open-Meteo and persisting the
    canonicalized coordinates into this cache via `set_grid_identifier()`.

    """


class OpenWeatherMapGridCache(WeatherGridCache):
    """OpenWeatherMap station → grid mapping cache.

    OpenWeatherMap returns less precise latitude longitude coordinates than input.
    However, I do not observe snapping to the nearest weather station.

    The orchestrator is responsible for calling OpenWeatherMap and persisting the
    canonicalized coordinates into this cache via `set_grid_identifier()`.

    """


def get_nws_grid_cache(config: dict) -> NWSGridCache:
    """Create an NWS grid cache configured from `config`.

    Args:
        config: Configuration dict with cache section

    Returns:
        Configured NWSGridCache instance

    Raises:
        ValueError: If required cache configuration is missing

    """
    cache_config = get_config_section("cache", config=config)
    cache_dir = cache_config.get("directory", ".cache")
    if not cache_dir:
        msg = "config['cache']['directory'] is required but missing or empty"
        raise ValueError(msg)

    ttl = cache_config.get("weather_mapping_ttl", 604800)  # Default: 7 days
    cache_file = Path(str(cache_dir)) / "nws_grid_mapping.json"
    return NWSGridCache(cache_file, int(ttl))


def get_open_meteo_grid_cache(config: dict) -> OpenMeteoGridCache:
    """Create an Open-Meteo grid cache configured from `config`.

    Args:
        config: Configuration dict with cache section

    Returns:
        Configured OpenMeteoGridCache instance

    Raises:
        ValueError: If required cache configuration is missing

    """
    cache_config = get_config_section("cache", config=config)
    cache_dir = cache_config.get("directory", ".cache")
    if not cache_dir:
        msg = "config['cache']['directory'] is required but missing or empty"
        raise ValueError(msg)

    ttl = cache_config.get("weather_mapping_ttl", 604800)  # Default: 7 days
    cache_file = Path(str(cache_dir)) / "open_meteo_grid_mapping.json"
    return OpenMeteoGridCache(cache_file, int(ttl))


def get_openweathermap_grid_cache(config: dict) -> OpenWeatherMapGridCache:
    """Create an OpenWeatherMap grid cache configured from `config`.

    Args:
        config: Configuration dict with cache section

    Returns:
        Configured OpenWeatherMapGridCache instance

    Raises:
        ValueError: If required cache configuration is missing

    """
    cache_config = get_config_section("cache", config=config)
    cache_dir = cache_config.get("directory", ".cache")
    if not cache_dir:
        msg = "config['cache']['directory'] is required but missing or empty"
        raise ValueError(msg)

    ttl = cache_config.get("weather_mapping_ttl", 604800)  # Default: 7 days
    cache_file = Path(str(cache_dir)) / "openweathermap_grid_mapping.json"
    return OpenWeatherMapGridCache(cache_file, int(ttl))
