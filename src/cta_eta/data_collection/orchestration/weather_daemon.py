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

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

import aiometer
import httpx

### OWN MODULES
from cta_eta.data_collection.apis.api_cta_stations import get_stations_cache
from cta_eta.data_collection.apis.api_weather_nws import (
    discover_nws_grid,
    get_nws_hourly_forecast,
)
from cta_eta.data_collection.apis.api_weather_open_meteo import (
    discover_open_meteo_grid,
    get_open_meteo_current,
)
from cta_eta.data_collection.apis.api_weather_openweathermap import (
    get_openweathermap_current,
)
from cta_eta.data_collection.merging.weather_merger import merge_weather_sources
from cta_eta.data_collection.orchestration.daemon_async import AsyncBaseDaemon
from cta_eta.data_collection.storage_cache.storage import create_parquet_writer
from cta_eta.data_collection.storage_cache.weather_grid_cache import (
    get_nws_grid_cache,
    get_open_meteo_grid_cache,
)

if TYPE_CHECKING:
    import logging

    from cta_eta.data_collection.storage_cache.cache import CachedData
    from cta_eta.data_collection.storage_cache.storage import ParquetWriter
    from cta_eta.data_collection.storage_cache.weather_grid_cache import (
        NWSGridCache,
        OpenMeteoGridCache,
    )

_OPEN_METEO_MAX_PER_SECOND: float = 0.1  # 6/minute
_OPEN_METEO_MAX_AT_ONCE: int = 3


@dataclass(frozen=True, slots=True)
class _StationGridMapping:
    station_id: str
    station_latitude: float
    station_longitude: float
    nws_grid_id: str
    open_meteo_grid_id: str


class WeatherDaemon(AsyncBaseDaemon):
    """Continuous weather collection daemon with parallel multi-source polling.

    Inherits lifecycle management from AsyncBaseDaemon and implements weather-specific
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
        storage: ParquetWriter for storing unified weather records
        weather_interval: Collection interval in seconds (default: 900 = 15 minutes)
        last_collection_time: Timestamp of last successful collection cycle
        records_stored_last_cycle: Number of records stored in last cycle

    """

    stations_cache: CachedData[list[dict[str, Any]]]
    nws_grid_cache: NWSGridCache
    om_grid_cache: OpenMeteoGridCache
    storage: ParquetWriter
    weather_interval: int
    last_collection_time: float
    records_stored_last_cycle: int

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

        # Initialize storage backend for Parquet writes
        self.storage = create_parquet_writer(config)

        # Extract weather collection interval from config (convert minutes to seconds)
        collection_config = config.get("collection", {})
        weather_interval_minutes = int(
            collection_config.get("weather_interval_minutes", 30)
        )
        self.weather_interval = weather_interval_minutes * 60

        # Initialize state tracking
        self.last_collection_time = 0.0
        self.records_stored_last_cycle = 0

    @override
    async def run(self) -> None:
        """Run the main weather collection loop.

        Runs continuous collection cycles until stopped. Uses interruptible daemon
        sleep so SIGTERM wakes the loop promptly, even for long polling intervals.
        """
        while self.running:
            try:
                await self._collect_weather_cycle()
            except Exception:
                self.logger.exception("Weather collection cycle failed")

            await self.sleep(self.weather_interval)

    async def _collect_weather_cycle(self) -> None:
        """Execute one weather collection cycle."""
        cycle_start_time = time.time()
        self.logger.info("Starting weather collection cycle")

        # Step 1: Build station → (NWS grid, Open-Meteo grid) mappings.
        # We dedupe at the provider grid level
        # reuse provider responses for all stations that map to the same grid.
        station_mappings = await self._get_station_grid_mappings()

        unique_nws_grids = {m.nws_grid_id for m in station_mappings}
        unique_om_grids = {m.open_meteo_grid_id for m in station_mappings}

        # Step 2: Fetch each provider once per unique provider grid ID.
        nws_by_grid_task = asyncio.create_task(
            self._fetch_nws_by_grid(sorted(unique_nws_grids))
        )
        om_by_grid_task = asyncio.create_task(
            self._fetch_open_meteo_by_grid(sorted(unique_om_grids))
        )

        nws_by_grid, om_by_grid = await asyncio.gather(
            nws_by_grid_task, om_by_grid_task
        )
        if nws_by_grid is None or om_by_grid is None:
            self.logger.error("No results from weather collection cycle")
            return

        # Step 3: Optional OpenWeatherMap fallback for stations where either primary
        # source failed. We dedupe fallback calls by Open-Meteo grid ID (a lat,lon key).
        fallback_grids = {
            m.open_meteo_grid_id
            for m in station_mappings
            if nws_by_grid.get(m.nws_grid_id) is None
            or om_by_grid.get(m.open_meteo_grid_id) is None
        }

        owm_by_grid = await self._fetch_openweathermap_by_grid(sorted(fallback_grids))

        # Step 4: Merge results from all sources into station-scoped records.
        merged_records: list[dict[str, Any]] = []
        collection_timestamp = time.time()
        for mapping in station_mappings:
            nws_data = nws_by_grid.get(mapping.nws_grid_id)
            om_data = om_by_grid.get(mapping.open_meteo_grid_id)
            owm_data = owm_by_grid.get(mapping.open_meteo_grid_id)

            merged = merge_weather_sources(nws_data, om_data, owm_data)
            if merged is not None:
                # Add metadata fields
                merged["station_id"] = mapping.station_id
                merged["nws_grid_id"] = mapping.nws_grid_id
                merged["open_meteo_grid_id"] = mapping.open_meteo_grid_id

                # Preserve the existing schema shape used by tests and prior Parquet
                # writes: `latitude`/`longitude` refer to the station coordinates.
                merged["latitude"] = mapping.station_latitude
                merged["longitude"] = mapping.station_longitude

                merged["collection_timestamp"] = collection_timestamp
                merged_records.append(merged)

        self.logger.info(
            f"Merged {len(merged_records)} weather records from {len(station_mappings)} stations"
        )

        # Step 5: Store merged records to Parquet
        if merged_records:
            try:
                self.storage.append_batch(
                    merged_records, dataset_name="weather_unified"
                )
                self.records_stored_last_cycle = len(merged_records)
                self.logger.info(
                    f"Stored {len(merged_records)} weather records to Parquet"
                )
            except Exception:
                self.logger.exception("Failed to store weather records to Parquet")
                self.records_stored_last_cycle = 0
        else:
            self.logger.warning("No weather records to store this cycle")
            self.records_stored_last_cycle = 0

        # Step 6: Log summary statistics
        success_count = len(merged_records)
        cycle_duration_ms = (time.time() - cycle_start_time) * 1000

        self.logger.info(
            f"Collected weather for {success_count}/{len(station_mappings)} stations",
            extra={
                "extra_fields": {
                    "unique_nws_grid_points": len(unique_nws_grids),
                    "unique_open_meteo_grid_points": len(unique_om_grids),
                    "stations_in_cycle": len(station_mappings),
                    "records_stored": self.records_stored_last_cycle,
                    "cycle_duration_ms": round(cycle_duration_ms, 2),
                }
            },
        )

        self.last_collection_time = time.time()

    async def _fetch_nws_by_grid(
        self, grid_ids: list[str]
    ) -> dict[str, dict[str, Any] | None] | None:
        async def _fetch_one(grid_id: str) -> tuple[str, dict[str, Any] | None]:
            try:
                data = await asyncio.to_thread(self._get_nws_hourly_forecast, grid_id)
            except Exception as e:  # noqa: BLE001
                self.logger.warning(
                    f"NWS API call failed for grid {grid_id}: {e}",
                    extra={
                        "extra_fields": {
                            "grid_id": grid_id,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                        }
                    },
                )
                return (grid_id, None)
            else:
                return (grid_id, data)

        results = await aiometer.run_on_each(
            _fetch_one,
            grid_ids,
            max_per_second=2.0,
            max_at_once=10,
        )
        return None if results is None else dict(results)

    async def _fetch_open_meteo_by_grid(
        self, grid_ids: list[str]
    ) -> dict[str, dict[str, Any] | None] | None:
        async def _fetch_one(grid_id: str) -> tuple[str, dict[str, Any] | None]:
            try:
                data = await asyncio.to_thread(self._get_open_meteo_current, grid_id)
            except Exception as e:  # noqa: BLE001
                self.logger.warning(
                    f"Open-Meteo API call failed for grid {grid_id}: {e}",
                    extra={
                        "extra_fields": {
                            "grid_id": grid_id,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                        }
                    },
                )
                return (grid_id, None)
            else:
                return (grid_id, data)

        results = await aiometer.run_on_each(
            _fetch_one,
            grid_ids,
            max_per_second=_OPEN_METEO_MAX_PER_SECOND,
            max_at_once=_OPEN_METEO_MAX_AT_ONCE,
        )
        return None if results is None else dict(results)

    async def _fetch_openweathermap_by_grid(
        self, grid_ids: list[str]
    ) -> dict[str, dict[str, Any] | None]:
        if not grid_ids:
            return {}

        async def _fetch_one(grid_id: str) -> tuple[str, dict[str, Any] | None]:
            try:
                data = await asyncio.to_thread(
                    self._get_openweathermap_current, grid_id
                )
            except Exception as e:
                self.logger.exception(
                    f"OpenWeatherMap fallback failed for grid point {grid_id}",
                    extra={
                        "extra_fields": {
                            "grid_id": grid_id,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                        }
                    },
                )
                return (grid_id, None)
            else:
                return (grid_id, data)

        results = await aiometer.run_on_each(
            _fetch_one,
            grid_ids,
            max_per_second=1.0,
            max_at_once=5,
        )
        return {} if results is None else dict(results)

    async def _get_station_grid_mappings(self) -> list[_StationGridMapping]:
        """Resolve station → provider grid mappings for this polling cycle.

        This method returns station-scoped mappings so the caller can:
        - dedupe NWS calls by NWS grid ID
        - dedupe Open-Meteo calls by Open-Meteo grid ID
        and reuse provider responses across all stations mapping to the same grid.
        """
        stations = self.stations_cache.get()
        cache_misses = 0

        base_by_station: dict[str, tuple[float, float, str]] = {}
        mappings: list[_StationGridMapping] = []
        om_discovery_requests: list[tuple[str, float, float]] = []

        for station in stations:
            station_id = station["id"]
            lat = float(station["latitude"])
            lon = float(station["longitude"])

            nws_grid = self.nws_grid_cache.get_grid_identifier(station_id)
            if nws_grid is None:
                try:
                    nws_grid = await asyncio.to_thread(
                        self._discover_nws_grid, lat, lon
                    )
                except Exception:
                    self.logger.exception(
                        f"Failed to discover NWS grid for station {station_id}",
                        extra={"extra_fields": {"station_id": station_id}},
                    )
                    continue
                else:
                    cache_misses += 1
                    # Cache writes are done on the event loop thread to avoid
                    # concurrent file writes in the persistent KV cache.
                    self.nws_grid_cache.set_grid_identifier(station_id, nws_grid)

            base_by_station[station_id] = (lat, lon, nws_grid)

            om_grid = self.om_grid_cache.get_grid_identifier(station_id)
            if om_grid is None:
                om_discovery_requests.append((station_id, lat, lon))
                continue

            mappings.append(
                _StationGridMapping(
                    station_id=station_id,
                    station_latitude=lat,
                    station_longitude=lon,
                    nws_grid_id=nws_grid,
                    open_meteo_grid_id=om_grid,
                )
            )

        if om_discovery_requests:
            discovered = await self._discover_open_meteo_grids_for_stations(
                om_discovery_requests
            )
            for station_id, om_grid in discovered.items():
                base = base_by_station.get(station_id)
                if base is None:
                    continue
                lat, lon, nws_grid = base
                cache_misses += 1
                self.om_grid_cache.set_grid_identifier(station_id, om_grid)
                mappings.append(
                    _StationGridMapping(
                        station_id=station_id,
                        station_latitude=lat,
                        station_longitude=lon,
                        nws_grid_id=nws_grid,
                        open_meteo_grid_id=om_grid,
                    )
                )

        unique_nws = {m.nws_grid_id for m in mappings}
        unique_om = {m.open_meteo_grid_id for m in mappings}

        self.logger.info(
            f"Resolved {len(stations)} stations to {len(unique_nws)} NWS grids and {len(unique_om)} Open-Meteo grids",
            extra={
                "extra_fields": {
                    "total_stations": len(stations),
                    "stations_with_mappings": len(mappings),
                    "unique_nws_grids": len(unique_nws),
                    "unique_open_meteo_grids": len(unique_om),
                    "cache_misses": cache_misses,
                }
            },
        )

        return mappings

    async def _discover_open_meteo_grids_for_stations(
        self, requests: list[tuple[str, float, float]]
    ) -> dict[str, str]:
        async def _discover_one(
            req: tuple[str, float, float],
        ) -> tuple[str, str] | None:
            station_id, lat, lon = req
            try:
                grid = await asyncio.to_thread(self._discover_open_meteo_grid, lat, lon)
            except Exception:
                self.logger.exception(
                    f"Failed to discover Open-Meteo grid for station {station_id}",
                    extra={"extra_fields": {"station_id": station_id}},
                )
                return None
            else:
                return (station_id, grid)

        discovered = await aiometer.run_on_each(
            _discover_one,
            requests,
            max_per_second=_OPEN_METEO_MAX_PER_SECOND,
            max_at_once=_OPEN_METEO_MAX_AT_ONCE,
        )
        if discovered is None:
            return {}

        out: dict[str, str] = {}
        for item in discovered:
            if item is None:
                continue
            station_id, grid_id = item
            out[station_id] = grid_id
        return out

    def _discover_nws_grid(self, latitude: float, longitude: float) -> str:
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        with httpx.Client(timeout=timeout) as client:
            return discover_nws_grid(client, latitude, longitude)

    def _discover_open_meteo_grid(self, latitude: float, longitude: float) -> str:
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        with httpx.Client(timeout=timeout) as client:
            return discover_open_meteo_grid(client, latitude, longitude)

    def _get_nws_hourly_forecast(self, nws_grid_id: str) -> dict[str, Any]:
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        with httpx.Client(timeout=timeout) as client:
            return get_nws_hourly_forecast(client, nws_grid_id)

    def _get_open_meteo_current(self, open_meteo_grid_id: str) -> dict[str, Any]:
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        with httpx.Client(timeout=timeout) as client:
            return get_open_meteo_current(client, open_meteo_grid_id)

    def _get_openweathermap_current(self, grid_id: str) -> dict[str, Any]:
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        with httpx.Client(timeout=timeout) as client:
            return get_openweathermap_current(client, grid_id)

    @override
    def _get_state(self) -> dict[str, str | int | float]:
        """Get current daemon state for persistence.

        Returns:
            Dictionary with daemon state to persist across restarts

        """
        return {
            "last_collection_timestamp": self.last_collection_time,
            "records_stored_last_cycle": self.records_stored_last_cycle,
            "weather_interval_seconds": self.weather_interval,
        }
