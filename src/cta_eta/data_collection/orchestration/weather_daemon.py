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
import functools
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

import aiometer
import httpx

# Own modules
from cta_eta.data_collection.apis.api_cta_stations import get_stations_cache
from cta_eta.data_collection.apis.api_weather_nws import (
    discover_nws_grid,
    get_nws_hourly_forecast,
)
from cta_eta.data_collection.apis.api_weather_open_meteo import get_open_meteo_current
from cta_eta.data_collection.apis.api_weather_openweathermap import (
    get_openweathermap_current,
)
from cta_eta.data_collection.config import (
    get_config_section,
    load_config,
    validate_config,
)
from cta_eta.data_collection.logging import log_context
from cta_eta.data_collection.merging.weather_merger import merge_weather_sources
from cta_eta.data_collection.orchestration.daemon import AsyncBaseDaemon
from cta_eta.data_collection.orchestration.daemon_utils import (
    ErrorCategory,
    classify_error,
)
from cta_eta.data_collection.storage_cache.journal_writer import create_journal_writer
from cta_eta.data_collection.storage_cache.weather_grid_cache import (
    get_nws_grid_cache,
    get_open_meteo_grid_cache,
)

if TYPE_CHECKING:
    import logging

    from cta_eta.data_collection.storage_cache.cache import CachedData
    from cta_eta.data_collection.storage_cache.journal_writer import JournalWriter
    from cta_eta.data_collection.storage_cache.weather_grid_cache import (
        NWSGridCache,
        OpenMeteoGridCache,
    )


@dataclass(frozen=True, slots=True)
class _StationGridMapping:
    station_id: str
    station_latitude: float
    station_longitude: float
    nws_grid_id: str
    open_meteo_grid_id: str | None  # None on cache miss; lazily discovered from real API call


def _om_effective_grid(mapping: _StationGridMapping) -> str:
    """Return effective Open-Meteo grid key: canonical cached grid or raw station lat,lon."""
    return mapping.open_meteo_grid_id or f"{mapping.station_latitude},{mapping.station_longitude}"


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
        storage: JournalWriter for storing unified weather records
        weather_interval: Collection interval in seconds (default: 900 = 15 minutes)
        last_collection_time: Timestamp of last successful collection cycle
        records_stored_last_cycle: Number of records stored in last cycle

    """

    stations_cache: CachedData[list[dict[str, Any]]]
    nws_grid_cache: NWSGridCache
    om_grid_cache: OpenMeteoGridCache
    storage: JournalWriter
    weather_interval: int
    last_collection_time: float
    records_stored_last_cycle: int
    open_meteo_max_per_second: float
    open_meteo_max_at_once: int

    def __init__(
        self,
        logger: logging.Logger,
        config: dict[str, dict[str, str | int | float | bool]] | None = None,
    ) -> None:
        """Initialize weather daemon with caches and configuration.

        Args:
            config: Configuration dictionary with collection settings
            logger: Logger instance for structured logging

        """
        if config is None:
            config = load_config()
        super().__init__(config, logger)

        # Validate configuration for required credentials
        validate_config(
            config,
            required_features=["weather_collection", "weather_collection_fallback"],
        )

        # Load stations cache for deduplication
        self.stations_cache = get_stations_cache(config)

        # Load weather grid caches for deduplication
        self.nws_grid_cache = get_nws_grid_cache(config)
        self.om_grid_cache = get_open_meteo_grid_cache(config)

        # Initialize storage backend for IPC journal writes
        self.storage = create_journal_writer(config)

        # Extract weather collection interval from config (convert minutes to seconds)
        collection_config = config.get("collection", {})
        weather_interval_minutes = int(
            collection_config.get("weather_interval_minutes", 30)
        )
        self.weather_interval = weather_interval_minutes * 60

        # Load rate limits from config with fallback defaults

        # NWS API: https://api.weather.gov/
        nws_rate_limit_config = get_config_section("rate_limits.nws")
        self.nws_max_per_second = float(nws_rate_limit_config.get("max_per_second", 1))
        self.nws_max_at_once = int(nws_rate_limit_config.get("max_at_once", 1))

        # Open-Meteo API: https://open-meteo.com/en/docs
        open_meteo_rate_limit_config = get_config_section("rate_limits.open_meteo")
        self.open_meteo_max_per_second = float(
            open_meteo_rate_limit_config.get("max_per_second", 1)
        )
        self.open_meteo_max_at_once = int(
            open_meteo_rate_limit_config.get("max_at_once", 1)
        )

        # OpenWeatherMap API: https://openweathermap.org/api/one-call-3
        openweathermap_rate_limit_config = get_config_section(
            "rate_limits.openweathermap"
        )
        self.openweathermap_max_per_second = float(
            openweathermap_rate_limit_config.get("max_per_second", 1)
        )
        self.openweathermap_max_at_once = int(
            openweathermap_rate_limit_config.get("max_at_once", 1)
        )

        # Initialize state tracking
        self.last_collection_time = 0.0
        self.records_stored_last_cycle = 0

    @override
    async def run(self) -> None:
        """Run the main weather collection loop.

        Runs continuous collection cycles until stopped.
        Creates HTTP clients once and reuses them across cycles for connection pooling.
        """
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        limits = httpx.Limits(max_connections=50, max_keepalive_connections=10)

        async with (
            httpx.AsyncClient(timeout=timeout, limits=limits) as nws_client,
            httpx.AsyncClient(timeout=timeout, limits=limits) as om_client,
            httpx.AsyncClient(timeout=timeout, limits=limits) as owm_client,
        ):
            while self.running:
                try:
                    await self._collect_weather_cycle(nws_client, om_client, owm_client)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    category = classify_error(e)
                    match category:
                        case ErrorCategory.CONFIGURATION:
                            self.logger.exception(
                                "Configuration error detected. Exiting daemon.",
                                extra={
                                    "extra_fields": {
                                        "error_type": type(e).__name__,
                                        "error_category": category.value,
                                        "error_message": str(e),
                                    }
                                },
                            )
                            # Exit gracefully on configuration errors
                            self.running = False
                            raise
                        case ErrorCategory.RATE_LIMIT:
                            self.logger.warning(
                                f"Rate limit error: {e}. Applying backoff.",
                                extra={
                                    "extra_fields": {
                                        "error_type": type(e).__name__,
                                        "error_category": category.value,
                                        "error_message": str(e),
                                    }
                                },
                            )
                            # Apply longer backoff for rate limits
                            await self.sleep(self.weather_interval * 2)
                            continue
                        case ErrorCategory.TRANSIENT:
                            self.logger.warning(
                                f"Transient error in collection cycle: {e}. Will retry next cycle.",
                                extra={
                                    "extra_fields": {
                                        "error_type": type(e).__name__,
                                        "error_category": category.value,
                                        "error_message": str(e),
                                    }
                                },
                            )
                        case _:
                            self.logger.exception(
                                "Weather collection cycle failed",
                                extra={
                                    "extra_fields": {
                                        "error_type": type(e).__name__,
                                        "error_category": category.value,
                                        "error_message": str(e),
                                    }
                                },
                            )

                await self.sleep(self.weather_interval)

    async def _collect_weather_cycle(
        self,
        nws_client: httpx.AsyncClient,
        om_client: httpx.AsyncClient,
        owm_client: httpx.AsyncClient,
    ) -> None:
        """Execute one complete weather collection cycle.

        This method orchestrates the multi-phase weather collection process:

        1. **Resolve Station → Grid Mappings**: Maps each CTA station to its corresponding
           weather grid identifiers for NWS and Open-Meteo. Uses cached mappings when
           available, performs discovery for cache misses. This deduplication step reduces
           ~145 stations to ~50 unique weather grid points.

        2. **Parallel Multi-Source Fetch**: Fetches weather data from NWS and Open-Meteo
           in parallel using asyncio.gather(). Each unique grid point is fetched once,
           and the result is reused for all stations mapping to that grid.

        3. **Fallback Handling**: If NWS or Open-Meteo fails for any grid point,
           OpenWeatherMap is called as a fallback source.

        4. **Merge and Store**: Merges weather data from all sources using precedence
           rules (NWS > Open-Meteo > OpenWeatherMap), attaches station metadata, and
           stores unified records to Parquet storage.

        The method handles partial failures gracefully - one source failing for one grid
        point doesn't stop collection for other grid points. All errors are logged but
        don't stop the cycle.

        Args:
            nws_client: HTTP client for NWS API requests
            om_client: HTTP client for Open-Meteo API requests
            owm_client: HTTP client for OpenWeatherMap API requests (fallback)

        """
        cycle_start_time = time.time()
        cycle_id = (
            self.diagnostics.new_cycle_id() if self.diagnostics.enabled else "disabled"
        )

        # Initialize variables for logging (in case of early returns)
        unique_nws_grids: set[str] = set()
        unique_om_grids: set[str] = set()
        station_mappings: list[_StationGridMapping] = []
        merged_records: list[dict[str, Any]] = []

        with log_context(
            daemon_class=self.__class__.__name__,
            cycle_id=cycle_id,
            diag_run_id=self.diagnostics.run_id,
        ):
            self.logger.info(
                "Starting weather collection cycle",
                extra={"extra_fields": {"cycle_id": cycle_id}},
            )

            try:
                async with self.diagnostics.span("weather_cycle", cycle_id=cycle_id):
                    # Step 1: Resolve station → provider grid mappings.
                    async with self.diagnostics.span(
                        "resolve_station_grid_mappings", cycle_id=cycle_id
                    ):
                        station_mappings = await self._get_station_grid_mappings(
                            nws_client
                        )

                    if not station_mappings:
                        self.logger.warning(
                            "No station mappings resolved, skipping cycle"
                        )
                        return

                    unique_nws_grids = {m.nws_grid_id for m in station_mappings}
                    unique_om_grids = {_om_effective_grid(m) for m in station_mappings}

                    # Step 2: Fetch each provider once per unique provider grid ID.
                    # NWS deduplicates by cached grid ID; Open-Meteo deduplicates by
                    # effective grid key (canonical cached grid or raw station lat,lon).
                    nws_by_grid_task = asyncio.create_task(
                        self._fetch_nws_by_grid(nws_client, sorted(unique_nws_grids))
                    )
                    om_by_grid_task = asyncio.create_task(
                        self._fetch_open_meteo_by_grid(om_client, station_mappings)
                    )

                    nws_by_grid, om_by_grid = await asyncio.gather(
                        nws_by_grid_task, om_by_grid_task
                    )

                    # Ensure we have dicts (not None)
                    if not isinstance(nws_by_grid, dict):
                        nws_by_grid = {}
                    if not isinstance(om_by_grid, dict):
                        om_by_grid = {}

                    # Step 3: Optional OpenWeatherMap fallback for stations where either primary
                    # source failed. Dedupe fallback calls by effective Open-Meteo grid key.
                    fallback_grids = {
                        _om_effective_grid(m)
                        for m in station_mappings
                        if nws_by_grid.get(m.nws_grid_id) is None
                        or om_by_grid.get(_om_effective_grid(m)) is None
                    }

                    owm_by_grid: dict[str, dict[str, Any] | None] = {}
                    if fallback_grids:
                        async with self.diagnostics.span(
                            "openweathermap_fallback_fetch",
                            cycle_id=cycle_id,
                            fallback_grid_count=len(fallback_grids),
                        ):
                            owm_by_grid = await self._fetch_openweathermap_by_grid(
                                owm_client, sorted(fallback_grids)
                            )

                    merged_records = self._merge_station_weather(
                        station_mappings,
                        nws_by_grid,
                        om_by_grid,
                        owm_by_grid,
                    )

                    self.logger.info(
                        f"Merged {len(merged_records)} weather records from {len(station_mappings)} stations"
                    )

                    self._store_merged_records(merged_records)

            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as e:
                self.logger.exception(
                    "Error during weather collection cycle",
                    extra={
                        "extra_fields": {
                            "cycle_id": cycle_id,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                        }
                    },
                )
                raise

        # Step 6: Log summary statistics (guarded against undefined variables)
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

    def _merge_station_weather(
        self,
        station_mappings: list[_StationGridMapping],
        nws_by_grid: dict[str, dict[str, Any] | None],
        om_by_grid: dict[str, dict[str, Any] | None],
        owm_by_grid: dict[str, dict[str, Any] | None],
    ) -> list[dict[str, Any]]:
        """Merge provider grid-scoped results into station-scoped unified records."""
        merged_records: list[dict[str, Any]] = []
        collection_timestamp = time.time()

        for mapping in station_mappings:
            om_key = _om_effective_grid(mapping)
            nws_data = nws_by_grid.get(mapping.nws_grid_id)
            om_data = om_by_grid.get(om_key)
            owm_data = owm_by_grid.get(om_key)

            merged = merge_weather_sources(nws_data, om_data, owm_data)
            if merged is None:
                continue

            merged["station_id"] = mapping.station_id
            merged["nws_grid_id"] = mapping.nws_grid_id
            merged["open_meteo_grid_id"] = om_key

            # Preserve schema: station coordinates (not provider grid coordinates).
            merged["latitude"] = mapping.station_latitude
            merged["longitude"] = mapping.station_longitude
            merged["collection_timestamp"] = collection_timestamp
            merged_records.append(merged)

        return merged_records

    def _store_merged_records(self, merged_records: list[dict[str, Any]]) -> None:
        """Store merged records to IPC journal, updating daemon state and logging."""
        if not merged_records:
            self.logger.warning("No weather records to store this cycle")
            self.records_stored_last_cycle = 0
            return

        try:
            self.storage.append_batch(merged_records, dataset_name="weather")
        except Exception:
            self.logger.exception("Failed to store weather records to IPC journal")
            self.records_stored_last_cycle = 0
        else:
            self.records_stored_last_cycle = len(merged_records)
            self.logger.info(
                f"Stored {len(merged_records)} weather records to IPC journal"
            )

    async def _fetch_nws_by_grid(
        self, client: httpx.AsyncClient, grid_ids: list[str]
    ) -> dict[str, dict[str, Any] | None]:
        async def _fetch_one(grid_id: str) -> tuple[str, dict[str, Any] | None]:
            try:
                async with self.diagnostics.span(
                    "nws.get_hourly_forecast",
                    grid_id=grid_id,
                ):
                    data = await get_nws_hourly_forecast(client, grid_id)
            except asyncio.CancelledError:
                raise
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

        self.diagnostics.record_event(
            "aiometer_run",
            operation="nws.get_hourly_forecast",
            item_count=len(grid_ids),
            max_per_second=self.nws_max_per_second,
            max_at_once=self.nws_max_at_once,
        )
        jobs = [functools.partial(_fetch_one, g) for g in grid_ids]
        results = await aiometer.run_all(
            jobs,
            max_at_once=self.nws_max_at_once,
            max_per_second=self.nws_max_per_second,
        )
        return {g: v for g, v in results if v is not None}

    async def _fetch_open_meteo_by_grid(
        self,
        client: httpx.AsyncClient,
        mappings: list[_StationGridMapping],
    ) -> dict[str, dict[str, Any] | None]:
        """Fetch Open-Meteo weather for each unique grid, updating the grid cache lazily.

        On a cache hit the effective key is the cached canonical grid (e.g. "41.88,-87.63"),
        enabling deduplication across stations that share a grid point.  On a cache miss the
        effective key falls back to the station's raw lat,lon.  Either way, the API accepts
        any valid lat,lon string and returns the canonical coordinates in its response; those
        are written back to the grid cache so the next cycle benefits from full deduplication.

        Returns a dict keyed by effective grid key (see ``_om_effective_grid``).
        """
        # Group station IDs by effective grid key for deduplication and cache updates.
        grid_to_station_ids: dict[str, list[str]] = {}
        for m in mappings:
            grid_to_station_ids.setdefault(_om_effective_grid(m), []).append(m.station_id)

        unique_grids = sorted(grid_to_station_ids)

        async def _fetch_one(grid_id: str) -> tuple[str, dict[str, Any] | None]:
            try:
                async with self.diagnostics.span(
                    "open_meteo.get_current",
                    grid_id=grid_id,
                ):
                    data = await get_open_meteo_current(client, grid_id)
            except asyncio.CancelledError:
                raise
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
                # Lazily update grid cache from the canonical coordinates in the response.
                canonical_lat = data.get("latitude")
                canonical_lon = data.get("longitude")
                if isinstance(canonical_lat, float) and isinstance(canonical_lon, float):
                    canonical_grid = f"{canonical_lat},{canonical_lon}"
                    for station_id in grid_to_station_ids.get(grid_id, []):
                        self.om_grid_cache.set_grid_identifier(station_id, canonical_grid)
                return (grid_id, data)

        self.diagnostics.record_event(
            "aiometer_run",
            operation="open_meteo.get_current",
            item_count=len(unique_grids),
            max_per_second=self.open_meteo_max_per_second,
            max_at_once=self.open_meteo_max_at_once,
        )
        jobs = [functools.partial(_fetch_one, g) for g in unique_grids]
        results = await aiometer.run_all(
            jobs,
            max_at_once=self.open_meteo_max_at_once,
            max_per_second=self.open_meteo_max_per_second,
        )
        return {g: v for g, v in results if v is not None}

    async def _fetch_openweathermap_by_grid(
        self, client: httpx.AsyncClient, grid_ids: list[str]
    ) -> dict[str, dict[str, Any] | None]:
        if not grid_ids:
            return {}

        async def _fetch_one(grid_id: str) -> tuple[str, dict[str, Any] | None]:
            try:
                data = await get_openweathermap_current(client, grid_id)
            except asyncio.CancelledError:
                raise
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

        jobs = [functools.partial(_fetch_one, g) for g in grid_ids]
        results = await aiometer.run_all(
            jobs,
            max_at_once=self.openweathermap_max_at_once,
            max_per_second=self.openweathermap_max_per_second,
        )
        return {g: v for g, v in results if v is not None}

    async def _get_station_grid_mappings(
        self,
        nws_client: httpx.AsyncClient | None = None,
    ) -> list[_StationGridMapping]:
        """Resolve station → NWS grid mappings, deferring Open-Meteo grid discovery.

        For each station:
        1. **NWS Grid Resolution**: Checks cache for NWS grid ID (permanent, TTL=None).
           On cache miss (first run only), calls NWS Points API to discover the grid
           identifier ("LOT/85,67"). Discovered IDs are cached permanently.

        2. **Open-Meteo Grid**: Returns cached canonical grid if present, or None on
           cache miss. Cache misses are resolved lazily by ``_fetch_open_meteo_by_grid``
           during the real weather fetch — no throwaway discovery call is made here.

        Returns station-scoped mappings. The caller deduplicates NWS calls by NWS grid
        ID and Open-Meteo calls by effective grid key (``_om_effective_grid``).

        Args:
            nws_client: HTTP client for NWS Points API (created if None)

        Returns:
            List of _StationGridMapping objects, one per station. open_meteo_grid_id
            may be None for stations with no cached canonical grid yet.

        """
        if nws_client is None:
            timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
            limits = httpx.Limits(max_connections=50, max_keepalive_connections=10)
            async with httpx.AsyncClient(timeout=timeout, limits=limits) as nws_client_ctx:
                return await self._get_station_grid_mappings(nws_client_ctx)

        stations = self.stations_cache.get()
        nws_cache_misses = 0
        mappings: list[_StationGridMapping] = []

        self.diagnostics.record_event(
            "station_grid_mapping_start",
            total_stations=len(stations),
        )

        for station in stations:
            station_id = station["id"]
            lat = float(station["latitude"])
            lon = float(station["longitude"])

            nws_grid = self.nws_grid_cache.get_grid_identifier(station_id)
            if nws_grid is None:
                try:
                    async with self.diagnostics.span(
                        "nws.discover_grid",
                        station_id=station_id,
                        latitude=lat,
                        longitude=lon,
                    ):
                        nws_grid = await discover_nws_grid(nws_client, lat, lon)
                except Exception:
                    self.logger.exception(
                        f"Failed to discover NWS grid for station {station_id}",
                        extra={"extra_fields": {"station_id": station_id}},
                    )
                    continue
                else:
                    nws_cache_misses += 1
                    self.nws_grid_cache.set_grid_identifier(station_id, nws_grid)

            # Open-Meteo grid may be None; lazily resolved during the real weather fetch.
            om_grid = self.om_grid_cache.get_grid_identifier(station_id)

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
        unique_om = {m.open_meteo_grid_id for m in mappings if m.open_meteo_grid_id}
        om_cache_misses = sum(1 for m in mappings if m.open_meteo_grid_id is None)

        self.logger.info(
            f"Resolved {len(stations)} stations to {len(unique_nws)} NWS grids and {len(unique_om)} cached Open-Meteo grids",
            extra={
                "extra_fields": {
                    "total_stations": len(stations),
                    "stations_with_mappings": len(mappings),
                    "unique_nws_grids": len(unique_nws),
                    "unique_cached_open_meteo_grids": len(unique_om),
                    "nws_cache_misses": nws_cache_misses,
                    "om_cache_misses": om_cache_misses,
                }
            },
        )

        return mappings

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

    @override
    def _apply_state(self, state: dict[str, str | int | float]) -> None:
        """Apply state to daemon instance."""
        self.last_collection_time = float(state.get("last_collection_timestamp", 0.0))
        self.records_stored_last_cycle = int(state.get("records_stored_last_cycle", 0))
        self.weather_interval = int(state.get("weather_interval_seconds", 0))
        self.logger.info(
            "Applied daemon state from previous run",
            extra={
                "extra_fields": {
                    "last_collection_timestamp": self.last_collection_time,
                    "records_stored_last_cycle": self.records_stored_last_cycle,
                    "weather_interval_seconds": self.weather_interval,
                }
            },
        )

    @override
    def _pre_shutdown_hook(self) -> None:
        """Close the journal writer on daemon shutdown to flush the IPC EOS marker."""
        try:
            self.storage.close()
            self.logger.debug("Closed journal writer during shutdown")
        except Exception as e:  # noqa: BLE001
            self.logger.warning(
                f"Failed to close journal writer: {e}",
                extra={
                    "extra_fields": {
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                    }
                },
            )


if __name__ == "__main__":
    from cta_eta.data_collection.logging import get_logger

    config = load_config()
    logger = get_logger("weather_daemon")

    # Have logger write to console
    import logging

    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)
    logger.propagate = False

    daemon = WeatherDaemon(logger, config)
    daemon.start()
