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
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

import aiometer
import httpx

# Own modules
from cta_eta.data_collection.apis.api_cta_stations import get_stations_cache
from cta_eta.data_collection.apis.api_weather_nws import (
    discover_nws_grid_async,
    get_nws_hourly_forecast_async,
)
from cta_eta.data_collection.apis.api_weather_open_meteo import (
    discover_open_meteo_grid_async,
    get_open_meteo_current_async,
)
from cta_eta.data_collection.apis.api_weather_openweathermap import (
    get_openweathermap_current_async,
)
from cta_eta.data_collection.logging import log_context
from cta_eta.data_collection.merging.weather_merger import merge_weather_sources
from cta_eta.data_collection.orchestration.daemon_async import AsyncBaseDaemon
from cta_eta.data_collection.storage_cache.storage import create_parquet_writer
from cta_eta.data_collection.storage_cache.weather_grid_cache import (
    get_nws_grid_cache,
    get_open_meteo_grid_cache,
)

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable

    from cta_eta.data_collection.storage_cache.cache import CachedData
    from cta_eta.data_collection.storage_cache.storage import ParquetWriter
    from cta_eta.data_collection.storage_cache.weather_grid_cache import (
        NWSGridCache,
        OpenMeteoGridCache,
    )

_OPEN_METEO_MAX_PER_SECOND: float = 0.1  # 6/minute
_OPEN_METEO_MAX_AT_ONCE: int = 3


class _AsyncRateLimiter:
    """Simple rate limiter for asyncio (spreads request starts over time)."""

    def __init__(self, *, max_per_second: float) -> None:
        self._min_interval_s = 1.0 / max_per_second if max_per_second > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next_start_at = 0.0  # monotonic timestamp

    async def wait_turn(self) -> None:
        if self._min_interval_s <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait_s = max(0.0, self._next_start_at - now)
            self._next_start_at = max(self._next_start_at, now) + self._min_interval_s
        if wait_s > 0:
            await asyncio.sleep(wait_s)


class _DiscoveryStateMarker:
    """Writes a progress marker file for long-running cold-cache discovery."""

    def __init__(
        self,
        *,
        provider: str,
        total: int,
        write: Callable[[dict[str, object]], None],
        daemon_class: str,
    ) -> None:
        self._provider = provider
        self._total = total
        self._write = write
        self._daemon_class = daemon_class
        self._succeeded = 0
        self._failed = 0
        self._started_at = time.time()

        self._payload: dict[str, object] = {
            "daemon_class": daemon_class,
            "provider": provider,
            "status": "in_progress",
            "total": total,
            "succeeded": 0,
            "failed": 0,
            "started_at": self._started_at,
            "updated_at": self._started_at,
        }

    def start(self) -> None:
        self._write(self._payload)

    def success(self) -> None:
        self._succeeded += 1
        self._payload["succeeded"] = self._succeeded
        self._payload["updated_at"] = time.time()
        self._write(self._payload)

    def failure(self) -> None:
        self._failed += 1
        self._payload["failed"] = self._failed
        self._payload["updated_at"] = time.time()
        self._write(self._payload)

    def finish(self, status: str, *, error: BaseException | None = None) -> None:
        self._payload["status"] = status
        self._payload["updated_at"] = time.time()
        if error is not None:
            self._payload["error_type"] = type(error).__name__
            self._payload["error_message"] = str(error)
        self._write(self._payload)


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

        Runs continuous collection cycles until stopped.
        """
        while self.running:
            try:
                await self._collect_weather_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("Weather collection cycle failed")

            await self.sleep(self.weather_interval)

    async def _collect_weather_cycle(self) -> None:
        """Execute one weather collection cycle."""
        cycle_start_time = time.time()
        cycle_id = (
            self.diagnostics.new_cycle_id() if self.diagnostics.enabled else "disabled"
        )

        with log_context(
            daemon_class=self.__class__.__name__,
            cycle_id=cycle_id,
            diag_run_id=self.diagnostics.run_id,
        ):
            self.logger.info(
                "Starting weather collection cycle",
                extra={"extra_fields": {"cycle_id": cycle_id}},
            )

            timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
            limits = httpx.Limits(max_connections=50, max_keepalive_connections=10)

            async with (
                self.diagnostics.span("weather_cycle", cycle_id=cycle_id),
                httpx.AsyncClient(timeout=timeout, limits=limits) as nws_client,
                httpx.AsyncClient(timeout=timeout, limits=limits) as om_client,
                httpx.AsyncClient(timeout=timeout, limits=limits) as owm_client,
            ):
                # Step 1: Resolve station → provider grid mappings.
                async with self.diagnostics.span(
                    "resolve_station_grid_mappings", cycle_id=cycle_id
                ):
                    station_mappings = await self._get_station_grid_mappings(
                        nws_client, om_client
                    )

                unique_nws_grids = {m.nws_grid_id for m in station_mappings}
                unique_om_grids = {m.open_meteo_grid_id for m in station_mappings}

                # Step 2: Fetch each provider once per unique provider grid ID.
                nws_by_grid_task = asyncio.create_task(
                    self._fetch_nws_by_grid(nws_client, sorted(unique_nws_grids))
                )
                om_by_grid_task = asyncio.create_task(
                    self._fetch_open_meteo_by_grid(om_client, sorted(unique_om_grids))
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
            nws_data = nws_by_grid.get(mapping.nws_grid_id)
            om_data = om_by_grid.get(mapping.open_meteo_grid_id)
            owm_data = owm_by_grid.get(mapping.open_meteo_grid_id)

            merged = merge_weather_sources(nws_data, om_data, owm_data)
            if merged is None:
                continue

            merged["station_id"] = mapping.station_id
            merged["nws_grid_id"] = mapping.nws_grid_id
            merged["open_meteo_grid_id"] = mapping.open_meteo_grid_id

            # Preserve schema: station coordinates (not provider grid coordinates).
            merged["latitude"] = mapping.station_latitude
            merged["longitude"] = mapping.station_longitude
            merged["collection_timestamp"] = collection_timestamp
            merged_records.append(merged)

        return merged_records

    def _store_merged_records(self, merged_records: list[dict[str, Any]]) -> None:
        """Store merged records to Parquet, updating daemon state and logging."""
        if not merged_records:
            self.logger.warning("No weather records to store this cycle")
            self.records_stored_last_cycle = 0
            return

        try:
            self.storage.append_batch(merged_records, dataset_name="weather_unified")
        except Exception:
            self.logger.exception("Failed to store weather records to Parquet")
            self.records_stored_last_cycle = 0
        else:
            self.records_stored_last_cycle = len(merged_records)
            self.logger.info(f"Stored {len(merged_records)} weather records to Parquet")

    async def _fetch_nws_by_grid(
        self, client: httpx.AsyncClient, grid_ids: list[str]
    ) -> dict[str, dict[str, Any] | None] | None:
        async def _fetch_one(grid_id: str) -> tuple[str, dict[str, Any] | None]:
            try:
                async with self.diagnostics.span(
                    "nws.get_hourly_forecast",
                    grid_id=grid_id,
                ):
                    data = await get_nws_hourly_forecast_async(client, grid_id)
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
            max_per_second=2.0,
            max_at_once=10,
        )
        results = await aiometer.run_on_each(
            _fetch_one,
            grid_ids,
            max_per_second=2.0,
            max_at_once=10,
        )
        return None if results is None else dict(results)

    async def _fetch_open_meteo_by_grid(
        self, client: httpx.AsyncClient, grid_ids: list[str]
    ) -> dict[str, dict[str, Any] | None] | None:
        async def _fetch_one(grid_id: str) -> tuple[str, dict[str, Any] | None]:
            try:
                async with self.diagnostics.span(
                    "open_meteo.get_current",
                    grid_id=grid_id,
                ):
                    data = await get_open_meteo_current_async(client, grid_id)
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
                return (grid_id, data)

        self.diagnostics.record_event(
            "aiometer_run",
            operation="open_meteo.get_current",
            item_count=len(grid_ids),
            max_per_second=_OPEN_METEO_MAX_PER_SECOND,
            max_at_once=_OPEN_METEO_MAX_AT_ONCE,
        )
        results = await aiometer.run_on_each(
            _fetch_one,
            grid_ids,
            max_per_second=_OPEN_METEO_MAX_PER_SECOND,
            max_at_once=_OPEN_METEO_MAX_AT_ONCE,
        )
        return None if results is None else dict(results)

    async def _fetch_openweathermap_by_grid(
        self, client: httpx.AsyncClient, grid_ids: list[str]
    ) -> dict[str, dict[str, Any] | None]:
        if not grid_ids:
            return {}

        async def _fetch_one(grid_id: str) -> tuple[str, dict[str, Any] | None]:
            try:
                data = await get_openweathermap_current_async(client, grid_id)
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

        results = await aiometer.run_on_each(
            _fetch_one,
            grid_ids,
            max_per_second=1.0,
            max_at_once=5,
        )
        return {} if results is None else dict(results)

    async def _get_station_grid_mappings(
        self,
        nws_client: httpx.AsyncClient | None = None,
        om_client: httpx.AsyncClient | None = None,
    ) -> list[_StationGridMapping]:
        """Resolve station → provider grid mappings for this polling cycle.

        This method returns station-scoped mappings so the caller can:
        - dedupe NWS calls by NWS grid ID
        - dedupe Open-Meteo calls by Open-Meteo grid ID
        and reuse provider responses across all stations mapping to the same grid.
        """
        if nws_client is None or om_client is None:
            timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
            limits = httpx.Limits(max_connections=50, max_keepalive_connections=10)
            async with (
                httpx.AsyncClient(timeout=timeout, limits=limits) as nws_client_ctx,
                httpx.AsyncClient(timeout=timeout, limits=limits) as om_client_ctx,
            ):
                return await self._get_station_grid_mappings(
                    nws_client_ctx, om_client_ctx
                )

        stations = self.stations_cache.get()
        cache_misses = 0

        base_by_station: dict[str, tuple[float, float, str]] = {}
        mappings: list[_StationGridMapping] = []
        om_discovery_requests: list[tuple[str, float, float]] = []

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
                        nws_grid = await discover_nws_grid_async(nws_client, lat, lon)
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
            self.diagnostics.record_event(
                "station_grid_mapping_cache_miss",
                provider="open_meteo",
                miss_count=len(om_discovery_requests),
                max_per_second=_OPEN_METEO_MAX_PER_SECOND,
                max_at_once=_OPEN_METEO_MAX_AT_ONCE,
            )
            discovered = await self._discover_open_meteo_grids_for_stations(
                om_client, om_discovery_requests
            )
            for station_id, om_grid in discovered.items():
                base = base_by_station.get(station_id)
                if base is None:
                    continue
                lat, lon, nws_grid = base
                cache_misses += 1
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

    async def _discover_open_meteo_grids_for_stations(  # noqa: C901
        self,
        client: httpx.AsyncClient,
        requests: list[tuple[str, float, float]],
    ) -> dict[str, str]:
        marker = _DiscoveryStateMarker(
            provider="open_meteo",
            total=len(requests),
            write=self._write_discovery_state_marker,
            daemon_class=self.__class__.__name__,
        )
        marker.start()

        self.diagnostics.record_event(
            "aiometer_run",
            operation="open_meteo.discover_grid",
            item_count=len(requests),
            max_per_second=_OPEN_METEO_MAX_PER_SECOND,
            max_at_once=_OPEN_METEO_MAX_AT_ONCE,
        )

        # Cold-cache fill can take a long time (rate limited); persist each success
        # immediately so a restart doesn't have to redo completed discoveries.
        out: dict[str, str] = {}
        sem = asyncio.Semaphore(_OPEN_METEO_MAX_AT_ONCE)
        limiter = _AsyncRateLimiter(max_per_second=_OPEN_METEO_MAX_PER_SECOND)

        async def _discover_one(
            req: tuple[str, float, float],
        ) -> tuple[str, str] | None:
            station_id, lat, lon = req
            async with sem:
                await limiter.wait_turn()
                try:
                    async with self.diagnostics.span(
                        "open_meteo.discover_grid",
                        station_id=station_id,
                        latitude=lat,
                        longitude=lon,
                    ):
                        grid = await discover_open_meteo_grid_async(client, lat, lon)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.logger.exception(
                        f"Failed to discover Open-Meteo grid for station {station_id}",
                        extra={"extra_fields": {"station_id": station_id}},
                    )
                    marker.failure()
                    return None
                else:
                    self.om_grid_cache.set_grid_identifier(station_id, grid)
                    self.diagnostics.record_event(
                        "cache_write",
                        provider="open_meteo",
                        station_id=station_id,
                        grid_id=grid,
                    )
                    marker.success()
                    return (station_id, grid)

        tasks = [asyncio.create_task(_discover_one(req)) for req in requests]
        try:
            for fut in asyncio.as_completed(tasks):
                result = await fut
                if result is None:
                    continue
                station_id, grid_id = result
                out[station_id] = grid_id
        except asyncio.CancelledError:
            marker.finish("cancelled")
            raise
        except Exception as e:
            marker.finish("failed", error=e)
            raise
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

        marker.finish("completed")
        return out

    def _write_discovery_state_marker(self, state: dict[str, object]) -> None:
        """Write an atomic discovery progress marker to `.daemon_state/` (best-effort)."""
        try:
            state_dir = Path(".daemon_state")
            state_dir.mkdir(exist_ok=True)
            path = state_dir / f"{self.__class__.__name__}.cold_cache.json"
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
            tmp.replace(path)
        except (OSError, ValueError, TypeError):
            return

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


if __name__ == "__main__":
    from cta_eta.data_collection.config import load_config
    from cta_eta.data_collection.logging import get_logger

    config = load_config()
    logger = get_logger("weather_daemon")

    # Have logger write to console
    import logging

    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)
    logger.propagate = False

    daemon = WeatherDaemon(config, logger)
    daemon.start()
