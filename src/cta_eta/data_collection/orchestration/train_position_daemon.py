"""Train position polling daemon with continuous 15-second data collection.

This module provides continuous 15-second train position polling that fetches all 8 CTA
train lines in a single API call, capturing ~230k snapshots/day while staying well under
CTA's 50k/day rate limit.

The daemon:
- Polls train positions every 15 seconds (configurable)
- Fetches all 8 CTA lines in one API call (efficient batch)
- Normalizes nested route/train structure to flat records
- Stores to Parquet with dataset_name="train_positions"
- Persists state across restarts for monitoring and debugging
"""

from __future__ import annotations

import asyncio
import functools
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, override

import aiometer
import httpx

from cta_eta.data_collection.apis.api_train_position import (
    get_train_positions,
    normalize_train_positions,
)
from cta_eta.data_collection.config import get_config_section, load_config
from cta_eta.data_collection.logging import log_context
from cta_eta.data_collection.orchestration.daemon_async import AsyncBaseDaemon
from cta_eta.data_collection.orchestration.daemon_utils import (
    ErrorCategory,
    classify_error,
)
from cta_eta.data_collection.storage_cache.storage import create_parquet_writer

if TYPE_CHECKING:
    import logging

    from cta_eta.data_collection.storage_cache.storage import ParquetWriter


class TrainPositionDaemon(AsyncBaseDaemon):
    """Continuous train position collection daemon with 15-second polling.

    Inherits lifecycle management from AsyncBaseDaemon and implements train position
    collection logic. The daemon:
    1. Loads configuration during initialization
    2. Runs async polling loop collecting positions every 15 seconds
    3. Fetches all 8 CTA lines in one API call (~0.58 req/sec sustained)
    4. Normalizes nested route/train responses to flat records
    5. Stores to Parquet with daily partitions (Hive-style at 3 AM Chicago time)

    Attributes:
        config: Configuration dictionary from config.toml
        logger: Structured logger instance
        running: Boolean flag controlling main loop execution
        storage: ParquetWriter for storing train position records
        train_poll_interval: Collection interval in seconds (default: 15)
        last_poll_timestamp: Timestamp of last successful poll
        total_records_collected: Total number of train records stored across all cycles
        current_poll_count: Number of polls completed in this daemon run
        cta_max_per_second: CTA API rate limit (requests per second)
        cta_max_at_once: CTA API max concurrent requests

    """

    storage: ParquetWriter
    train_poll_interval: int
    last_poll_timestamp: float
    total_records_collected: int
    current_poll_count: int
    cta_max_per_second: float
    cta_max_at_once: int

    def __init__(
        self,
        logger: logging.Logger,
        config: dict[str, dict[str, str | int | float | bool]] | None = None,
    ) -> None:
        """Initialize train position daemon with storage and configuration.

        Args:
            logger: Logger instance for structured logging
            config: Configuration dictionary with collection settings

        """
        if config is None:
            config = load_config()
        super().__init__(config, logger)

        # Initialize storage backend for Parquet writes
        self.storage = create_parquet_writer(config)

        # Extract train polling interval from config
        collection_config = config.get("collection", {})
        self.train_poll_interval = int(
            collection_config.get("train_poll_interval_seconds", 15)
        )

        # Load CTA rate limits from config
        cta_rate_limit_config = get_config_section("rate_limits.cta")
        self.cta_max_per_second = float(cta_rate_limit_config.get("max_per_second"))
        self.cta_max_at_once = int(cta_rate_limit_config.get("max_at_once"))

        # Initialize state tracking
        self.last_poll_timestamp = 0.0
        self.total_records_collected = 0
        self.current_poll_count = 0

    @override
    async def run(self) -> None:
        """Run the main train position collection loop.

        Runs continuous collection cycles until stopped.
        Creates HTTP client once and reuses it across cycles for connection pooling.
        """
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
        limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)

        async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
            while self.running:
                try:
                    await self._collect_train_positions_cycle(client)
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
                            await self.sleep(self.train_poll_interval * 2)
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
                                "Train position collection cycle failed",
                                extra={
                                    "extra_fields": {
                                        "error_type": type(e).__name__,
                                        "error_category": category.value,
                                        "error_message": str(e),
                                    }
                                },
                            )

                await self.sleep(self.train_poll_interval)

    async def _collect_train_positions_cycle(self, client: httpx.AsyncClient) -> None:
        """Execute one complete train position collection cycle.

        This method orchestrates the train position collection process:
        1. Record poll timestamp BEFORE API call for precise timing
        2. Fetch train positions from CTA API (all 8 lines in one call)
        3. Normalize nested route/train structure to flat records
        4. Store records to Parquet with dataset_name="train_positions"
        5. Update daemon state and log summary

        Args:
            client: HTTP client for CTA API requests

        """
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
                "Starting train position collection cycle",
                extra={"extra_fields": {"cycle_id": cycle_id}},
            )

            try:
                async with self.diagnostics.span("train_positions_cycle", cycle_id=cycle_id):
                    # Record poll timestamp BEFORE API call for precise timing
                    poll_timestamp = time.time()

                    # Fetch train positions from CTA API with aiometer rate limiting
                    async def _fetch_train_positions() -> dict[str, Any]:
                        async with self.diagnostics.span("cta.get_train_positions", cycle_id=cycle_id):
                            return await get_train_positions(client)

                    # Record diagnostic event before aiometer call
                    self.diagnostics.record_event(
                        "aiometer_run",
                        operation="cta.get_train_positions",
                        item_count=1,
                        max_per_second=self.cta_max_per_second,
                        max_at_once=self.cta_max_at_once,
                    )

                    # Wrap in aiometer for rate limiting
                    jobs = [functools.partial(_fetch_train_positions)]
                    results = await aiometer.run_all(
                        jobs,
                        max_at_once=self.cta_max_at_once,
                        max_per_second=self.cta_max_per_second,
                    )
                    raw_response = results[0]  # Single result

                    # Normalize nested route/train structure to flat records
                    records = normalize_train_positions(
                        raw_response,
                        datetime.fromtimestamp(poll_timestamp, tz=datetime.UTC),
                    )

                    self.logger.info(
                        f"Fetched {len(records)} train positions from CTA API",
                        extra={
                            "extra_fields": {
                                "cycle_id": cycle_id,
                                "records_fetched": len(records),
                            }
                        },
                    )

                    # Store records to Parquet
                    try:
                        self.storage.append_batch(records, dataset_name="train_positions")
                    except Exception:
                        self.logger.exception(
                            "Failed to store train position records",
                            extra={
                                "extra_fields": {
                                    "cycle_id": cycle_id,
                                    "records_attempted": len(records),
                                }
                            },
                        )
                        # Don't update state on storage failure
                        # Don't re-raise - storage failure shouldn't stop daemon
                    else:
                        # Update state on successful storage
                        self.last_poll_timestamp = poll_timestamp
                        self.total_records_collected += len(records)
                        self.current_poll_count += 1

                        # Record diagnostic event
                        self.diagnostics.record_event(
                            "train_positions_stored",
                            cycle_id=cycle_id,
                            records_stored=len(records),
                        )

                        cycle_duration_ms = (time.time() - cycle_start_time) * 1000

                        self.logger.info(
                            f"Stored {len(records)} train position records to Parquet",
                            extra={
                                "extra_fields": {
                                    "cycle_id": cycle_id,
                                    "records_stored": len(records),
                                    "cycle_duration_ms": round(cycle_duration_ms, 2),
                                    "total_records_collected": self.total_records_collected,
                                    "current_poll_count": self.current_poll_count,
                                }
                            },
                        )

            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as e:
                self.logger.exception(
                    "Error during train position collection cycle",
                    extra={
                        "extra_fields": {
                            "cycle_id": cycle_id,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                        }
                    },
                )
                raise

    @override
    def _get_state(self) -> dict[str, str | int | float]:
        """Get current daemon state for persistence.

        Returns:
            Dictionary with daemon state to persist across restarts

        """
        return {
            "last_poll_timestamp": self.last_poll_timestamp,
            "total_records_collected": self.total_records_collected,
            "current_poll_count": self.current_poll_count,
            "train_poll_interval_seconds": self.train_poll_interval,
        }


if __name__ == "__main__":
    from cta_eta.data_collection.config import load_config
    from cta_eta.data_collection.logging import get_logger

    config = load_config()
    logger = get_logger("train_position_daemon")

    # Have logger write to console
    import logging

    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)
    logger.propagate = False

    daemon = TrainPositionDaemon(logger, config)
    daemon.start()
