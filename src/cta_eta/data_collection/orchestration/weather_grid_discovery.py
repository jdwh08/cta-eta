"""Open-Meteo grid discovery for weather daemon with rate limiting and timeouts."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import aiometer

from cta_eta.data_collection.apis.api_weather_open_meteo import discover_open_meteo_grid
from cta_eta.data_collection.orchestration.daemon_utils import DiscoveryStateMarker

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable

    import httpx

    from cta_eta.data_collection.orchestration.diagnostics import DaemonDiagnostics
    from cta_eta.data_collection.storage_cache.weather_grid_cache import (
        OpenMeteoGridCache,
    )

# NOTE: constants so that we can patch them in tests
_PER_STATION_DISCOVERY_TIMEOUT_S: float = 30.0
_OVERALL_BATCH_TIMEOUT_S: float = 600.0


class WeatherGridDiscoverer:
    """Handles grid discovery for weather APIs with rate limiting and timeout management.

    This class encapsulates the complex logic for discovering weather grid identifiers
    for multiple stations concurrently, with proper rate limiting, timeouts, and error
    handling. It manages the discovery state and persists results to cache.

    Attributes:
        logger: Logger instance for structured logging
        diagnostics: DaemonDiagnostics instance for telemetry
        om_grid_cache: OpenMeteoGridCache for persisting discovered grids
        open_meteo_max_per_second: Rate limit for Open-Meteo API (requests per second)
        open_meteo_max_at_once: Maximum concurrent requests to Open-Meteo API
        write_discovery_state_marker: Callable to write discovery state markers

    """

    def __init__(
        self,
        *,
        logger: logging.Logger,
        diagnostics: DaemonDiagnostics,
        om_grid_cache: OpenMeteoGridCache,
        open_meteo_max_per_second: float,
        open_meteo_max_at_once: int,
        write_discovery_state_marker: Callable[[dict[str, object]], None],
        daemon_class: str,
    ) -> None:
        """Initialize grid discoverer with dependencies.

        Args:
            logger: Logger instance for structured logging
            diagnostics: DaemonDiagnostics instance for telemetry
            om_grid_cache: OpenMeteoGridCache for persisting discovered grids
            open_meteo_max_per_second: Rate limit for Open-Meteo API
            open_meteo_max_at_once: Maximum concurrent requests
            write_discovery_state_marker: Function to write discovery state markers
            daemon_class: Name of daemon class for state markers

        """
        self.logger = logger
        self.diagnostics = diagnostics
        self.om_grid_cache = om_grid_cache
        self.open_meteo_max_per_second = open_meteo_max_per_second
        self.open_meteo_max_at_once = open_meteo_max_at_once
        self._write_discovery_state_marker = write_discovery_state_marker
        self._daemon_class = daemon_class

    async def _discover_one(
        self,
        req: tuple[str, float, float],
        marker: DiscoveryStateMarker,
        client: httpx.AsyncClient,
        per_station_timeout_s: float,
    ) -> tuple[str, str] | None:
        """Discover one Open-Meteo grid for a station.

        Args:
            req: The request tuple (station_id, latitude, longitude)
            marker: The discovery state marker
            client: The HTTP client
            per_station_timeout_s: The per-station timeout

        Returns:
            The tuple (station_id, grid_id) if successful, None otherwise

        """
        station_id, lat, lon = req
        try:
            async with self.diagnostics.span(
                "open_meteo.discover_grid",
                station_id=station_id,
                latitude=lat,
                longitude=lon,
            ):
                grid = await asyncio.wait_for(
                    discover_open_meteo_grid(client, lat, lon),
                    timeout=per_station_timeout_s,
                )
        except TimeoutError:
            self.logger.warning(
                f"Open-Meteo grid discovery timed out for station {station_id}",
                extra={
                    "extra_fields": {
                        "station_id": station_id,
                        "timeout_seconds": per_station_timeout_s,
                    }
                },
            )
            marker.failure()
            return None
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

    async def discover_open_meteo_grids_for_stations(
        self,
        client: httpx.AsyncClient,
        requests: list[tuple[str, float, float]],
    ) -> dict[str, str]:
        """Discover Open-Meteo grid identifiers for multiple stations concurrently.

        This method handles the complex orchestration of discovering grid identifiers
        for multiple stations with:
        - Rate limiting (max_per_second, max_at_once) via aiometer.amap
        - Per-station timeouts (30 seconds)
        - Overall batch timeout (10 minutes)
        - Immediate cache persistence on success
        - Graceful handling of partial failures

        Args:
            client: HTTP client for API requests
            requests: List of (station_id, latitude, longitude) tuples

        Returns:
            Dictionary mapping station_id to grid_id for successfully discovered grids

        """
        marker = DiscoveryStateMarker(
            provider="open_meteo",
            total=len(requests),
            write=self._write_discovery_state_marker,
            daemon_class=self._daemon_class,
        )
        marker.start()

        self.diagnostics.record_event(
            "aiometer_run",
            operation="open_meteo.discover_grid",
            item_count=len(requests),
            max_per_second=self.open_meteo_max_per_second,
            max_at_once=self.open_meteo_max_at_once,
        )

        # Cold-cache fill can take a long time (rate limited); persist each success
        # immediately so a restart doesn't have to redo completed discoveries.
        # Add timeout: 30 seconds per station, with overall batch timeout of 10 minutes
        # (for ~145 stations at 0.1 req/s, worst case is ~24 minutes, but we cap at 10)
        per_station_timeout_s = _PER_STATION_DISCOVERY_TIMEOUT_S
        overall_batch_timeout_s = _OVERALL_BATCH_TIMEOUT_S

        async def discover_fn(req: tuple[str, float, float]) -> tuple[str, str] | None:
            return await self._discover_one(req, marker, client, per_station_timeout_s)

        out: dict[str, str] = {}
        start = time.monotonic()
        timed_out = False

        try:
            async with aiometer.amap(
                discover_fn,
                requests,
                max_at_once=self.open_meteo_max_at_once,
                max_per_second=self.open_meteo_max_per_second,
            ) as results:
                async for r in results:
                    if r is not None:
                        sid, gid = r
                        out[sid] = gid
                    if (time.monotonic() - start) >= overall_batch_timeout_s:
                        timed_out = True
                        self.logger.warning(
                            "Discovery batch timed out",
                            extra={
                                "extra_fields": {
                                    "overall_timeout_seconds": overall_batch_timeout_s,
                                }
                            },
                        )
                        break

            if timed_out:
                marker.finish("timeout")
            else:
                marker.finish("completed")
        except asyncio.CancelledError:
            marker.finish("cancelled")
            raise
        except Exception as e:
            marker.finish("failed", error=e)
            raise
        else:
            return out
