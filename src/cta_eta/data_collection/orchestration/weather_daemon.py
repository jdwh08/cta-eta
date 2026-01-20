"""Weather collection daemon with parallel multi-source polling and grid cache deduplication.

This module provides continuous 15-minute weather polling that queries NWS + Open-Meteo
in parallel, leveraging Phase 3 grid caches to minimize API calls from ~145 stations
to ~50 unique grid points.

The daemon:
- Polls weather every 15 minutes (configurable)
- Deduplicates stations to unique grid points using weather grid caches
- Queries NWS and Open-Meteo in parallel using asyncio.gather(return_exceptions=True)
- Handles partial failures gracefully (log warnings, continue collection)
- Persists state across restarts for monitoring and debugging
"""

import asyncio
import logging
import time
from typing import Any

import aiometer
import httpx

### OWN MODULES
from cta_eta.data_collection.apis.api_cta_stations import get_stations_cache
from cta_eta.data_collection.apis.api_weather_nws import get_nws_hourly_forecast
from cta_eta.data_collection.apis.api_weather_open_meteo import (
    discover_open_meteo_grid,
    get_open_meteo_current,
)
from cta_eta.data_collection.apis.api_weather_openweathermap import (
    get_openweathermap_current,
)
from cta_eta.data_collection.orchestration.daemon import BaseDaemon
from cta_eta.data_collection.storage_cache.cache import CachedData
from cta_eta.data_collection.storage_cache.weather_grid_cache import (
    NWSGridCache,
    OpenMeteoGridCache,
    get_nws_grid_cache,
    get_open_meteo_grid_cache,
)


class WeatherDaemon(BaseDaemon):
    """Continuous weather collection daemon with parallel multi-source polling.

    Inherits lifecycle management from BaseDaemon and implements weather-specific
    collection logic. The daemon:
    1. Loads station and grid caches during initialization
    2. Runs async polling loop collecting weather every 15 minutes
    3. Deduplicates ~145 stations to ~50 unique grid points
    4. Queries NWS + Open-Meteo in parallel for each grid point
    5. Handles partial failures gracefully (one source failing doesn't stop collection)

    Attributes:
        config: Configuration dictionary from config.toml
        logger: Structured logger instance
        running: Boolean flag controlling main loop execution
        stations_cache: CachedData instance for CTA stations
        nws_grid_cache: NWSGridCache for station → NWS grid mappings
        om_grid_cache: OpenMeteoGridCache for station → Open-Meteo grid mappings
        weather_interval: Collection interval in seconds (default: 900 = 15 minutes)
        last_collection_time: Timestamp of last successful collection cycle
        unique_grid_points_count: Number of unique grid points in last cycle

    """

    stations_cache: CachedData[list[dict[str, Any]]]
    nws_grid_cache: NWSGridCache
    om_grid_cache: OpenMeteoGridCache
    weather_interval: int
    last_collection_time: float
    unique_grid_points_count: int

    def __init__(
        self,
        config: dict[str, dict[str, str | int | float | bool]],
        logger: logging.Logger,
    ) -> None:
        """Initialize weather daemon with caches and configuration.

        Args:
            config: Configuration dictionary with collection settings
            logger: Logger instance for structured logging

        """
        super().__init__(config, logger)

        # Load stations cache for deduplication
        self.stations_cache = get_stations_cache(config)

        # Load weather grid caches for deduplication
        self.nws_grid_cache = get_nws_grid_cache(config)
        self.om_grid_cache = get_open_meteo_grid_cache(config)

        # Extract weather collection interval from config (convert minutes to seconds)
        collection_config = config.get("collection", {})
        weather_interval_minutes = int(
            collection_config.get("weather_interval_minutes", 30)
        )
        self.weather_interval = weather_interval_minutes * 60

        # Initialize state tracking
        self.last_collection_time = 0.0
        self.unique_grid_points_count = 0

    async def run(self) -> None:
        """Run the main weather collection loop.

        Creates httpx.AsyncClient for connection pooling and runs continuous
        collection cycles until stopped. Uses asyncio.sleep() to avoid blocking
        the event loop.

        The daemon:
        1. Creates async HTTP client with proper timeouts
        2. Loops while self.running is True
        3. Collects weather from all sources in parallel
        4. Sleeps for configured interval (NEVER uses time.sleep())
        5. Closes HTTP client on shutdown

        """
        # Create async HTTP client with reasonable timeouts
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            while self.running:
                try:
                    await self._collect_weather_cycle(client)
                except Exception:
                    self.logger.exception("Weather collection cycle failed")

                # CRITICAL: Use await asyncio.sleep() NOT time.sleep()
                # time.sleep() blocks entire event loop, daemon becomes unresponsive
                await asyncio.sleep(self.weather_interval)

    async def _collect_weather_cycle(self, client: httpx.AsyncClient) -> None:
        """Execute one weather collection cycle for all unique grid points.

        Args:
            client: Async HTTP client for API requests

        """
        cycle_start_time = time.time()
        self.logger.info("Starting weather collection cycle")

        # Step 1: Deduplicate stations to unique grid points
        unique_grid_points = await self._get_unique_grid_points(client)
        self.unique_grid_points_count = len(unique_grid_points)

        # Step 2: Define fetch function with fallback logic
        async def _fetch_with_fallback(
            grid_point: tuple[str, str, float, float]
        ) -> tuple[
            dict[str, Any] | None,
            dict[str, Any] | None,
            dict[str, Any] | None,
            float,
            float,
            float,
        ]:
            """Fetch weather with OpenWeatherMap fallback on source failures."""
            nws_grid, om_grid, lat, lon = grid_point
            nws_data, om_data, lat, lon, timestamp = (
                await self._fetch_weather_for_grid_point(
                    client, nws_grid, om_grid, lat, lon
                )
            )

            # Fallback to OpenWeatherMap if either source failed
            owm_data = None
            if nws_data is None or om_data is None:
                failed_sources = []
                if nws_data is None:
                    failed_sources.append("NWS")
                if om_data is None:
                    failed_sources.append("Open-Meteo")

                try:
                    owm_data = await asyncio.to_thread(
                        get_openweathermap_current, client, f"{lat},{lon}"
                    )
                    self.logger.info(
                        f"Falling back to OpenWeatherMap for grid point {lat},{lon} due to {', '.join(failed_sources)} failure"
                    )
                except Exception:
                    self.logger.exception(
                        f"OpenWeatherMap fallback failed for grid point {lat},{lon}",
                        extra={"extra_fields": {"latitude": lat, "longitude": lon}},
                    )

            return (nws_data, om_data, owm_data, lat, lon, timestamp)

        # Step 3: Collect weather with rate limiting (6 calls/minute = 0.1/second)
        results = await aiometer.run_on_each(
            _fetch_with_fallback,
            unique_grid_points,
            max_per_second=0.1,  # 6 per minute for Open-Meteo 10k/day limit
            max_at_once=3,  # Max 3 concurrent requests
        )

        # Step 4: Log summary statistics
        success_count = sum(
            1
            for nws_data, om_data, owm_data, _, _, _ in results
            if nws_data or om_data or owm_data
        )
        cycle_duration_ms = (time.time() - cycle_start_time) * 1000

        self.logger.info(
            f"Collected weather for {success_count}/{len(unique_grid_points)} grid points",
            extra={
                "extra_fields": {
                    "unique_grid_points": len(unique_grid_points),
                    "successful_collections": success_count,
                    "cycle_duration_ms": round(cycle_duration_ms, 2),
                }
            },
        )

        self.last_collection_time = time.time()

    async def _get_unique_grid_points(
        self, client: httpx.AsyncClient
    ) -> list[tuple[str, str, float, float]]:
        """Deduplicate stations to unique grid points using grid caches.

        For each station, get both NWS and Open-Meteo grid identifiers from caches.
        If either grid ID is missing (cache miss), attempt to discover it from the API
        and update the cache for future use.

        Args:
            client: Async HTTP client for API requests

        Returns:
            List of unique (nws_grid_id, om_grid_id, lat, lon) tuples

        """
        stations = self.stations_cache.get()
        unique_grids: set[tuple[str, str, float, float]] = set()
        cache_misses = 0

        for station in stations:
            station_id = station["id"]
            lat = float(station["latitude"])
            lon = float(station["longitude"])

            # Get NWS grid ID from cache, discover if missing
            nws_grid = self.nws_grid_cache.get_grid_identifier(station_id)
            if nws_grid is None:
                try:
                    # Use resolve_grid_identifier for NWS (handles discovery)
                    nws_grid = self.nws_grid_cache.resolve_grid_identifier(
                        station_id, lat, lon
                    )
                    cache_misses += 1
                except Exception:
                    self.logger.exception(
                        f"Failed to discover NWS grid for station {station_id}",
                        extra={"extra_fields": {"station_id": station_id}},
                    )
                    continue  # Skip this station if NWS grid discovery fails

            # Get Open-Meteo grid ID from cache, discover if missing
            om_grid = self.om_grid_cache.get_grid_identifier(station_id)
            if om_grid is None:
                try:
                    # Discover Open-Meteo grid and cache it
                    om_grid = discover_open_meteo_grid(client, lat, lon)
                    self.om_grid_cache.set_grid_identifier(station_id, om_grid)
                    cache_misses += 1
                except Exception:
                    self.logger.exception(
                        f"Failed to discover Open-Meteo grid for station {station_id}",
                        extra={"extra_fields": {"station_id": station_id}},
                    )
                    continue  # Skip this station if Open-Meteo grid discovery fails

            # Add to unique grids set (deduplication happens here)
            unique_grids.add((nws_grid, om_grid, lat, lon))

        self.logger.info(
            f"Deduplicated {len(stations)} stations to {len(unique_grids)} unique grid points",
            extra={
                "extra_fields": {
                    "total_stations": len(stations),
                    "unique_grid_points": len(unique_grids),
                    "cache_misses": cache_misses,
                }
            },
        )

        return list(unique_grids)

    async def _fetch_weather_for_grid_point(
        self,
        client: httpx.AsyncClient,
        nws_grid: str,
        om_grid: str,
        lat: float,
        lon: float,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, float, float, float]:
        """Fetch weather from NWS and Open-Meteo in parallel for a grid point.

        Uses asyncio.gather(return_exceptions=True) to query both APIs in parallel.
        Partial failures are logged as warnings but don't stop the collection.

        Args:
            client: Async HTTP client for API requests
            nws_grid: NWS grid identifier (e.g., "LOT/85,67")
            om_grid: Open-Meteo grid identifier (e.g., "41.88,-87.63")
            lat: Latitude of grid point
            lon: Longitude of grid point

        Returns:
            Tuple of (nws_data or None, om_data or None, lat, lon, timestamp)

        """
        timestamp = time.time()

        # Query both APIs in parallel using asyncio.gather
        # return_exceptions=True ensures one API failure doesn't stop the other
        results = await asyncio.gather(
            asyncio.to_thread(get_nws_hourly_forecast, client, nws_grid),
            asyncio.to_thread(get_open_meteo_current, client, om_grid),
            return_exceptions=True,
        )

        nws_result, om_result = results

        # Check for NWS failures
        nws_data = None
        if isinstance(nws_result, Exception):
            self.logger.warning(
                f"NWS API call failed for grid {nws_grid}: {nws_result}",
                extra={
                    "extra_fields": {
                        "grid_id": nws_grid,
                        "error_type": type(nws_result).__name__,
                        "error_message": str(nws_result),
                    }
                },
            )
        else:
            nws_data = nws_result

        # Check for Open-Meteo failures
        om_data = None
        if isinstance(om_result, Exception):
            self.logger.warning(
                f"Open-Meteo API call failed for grid {om_grid}: {om_result}",
                extra={
                    "extra_fields": {
                        "grid_id": om_grid,
                        "error_type": type(om_result).__name__,
                        "error_message": str(om_result),
                    }
                },
            )
        else:
            om_data = om_result

        return (nws_data, om_data, lat, lon, timestamp)

    def _get_state(self) -> dict[str, str | int | float]:
        """Get current daemon state for persistence.

        Returns:
            Dictionary with daemon state to persist across restarts

        """
        return {
            "last_collection_timestamp": self.last_collection_time,
            "unique_grid_points_count": self.unique_grid_points_count,
            "weather_interval_seconds": self.weather_interval,
        }
