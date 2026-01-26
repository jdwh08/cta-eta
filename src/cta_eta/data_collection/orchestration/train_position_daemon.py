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
import datetime
import time
from typing import TYPE_CHECKING, Any, override
from zoneinfo import ZoneInfo

import aiometer
import httpx
import stamina

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
    probe_102_attempts: int
    probe_102_intervals: list[int]

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

        # Load CTA error 102 (daily quota) probe configuration
        self.probe_102_attempts = int(collection_config.get("probe_102_attempts", 2))
        probe_intervals_raw = collection_config.get("probe_102_intervals", [300, 900])
        self.probe_102_intervals = (
            [int(x) for x in probe_intervals_raw]
            if isinstance(probe_intervals_raw, list)
            else [300, 900]
        )

        # Initialize state tracking
        self.last_poll_timestamp = 0.0
        self.total_records_collected = 0
        self.current_poll_count = 0

    @override
    async def run(self) -> None:
        """Run the main train position collection loop.

        Runs continuous collection cycles until stopped.
        Creates HTTP client once and reuses it across cycles for connection pooling.

        Implements CTA-specific error handling:
        - TRANSIENT: Extended retry (5-10 min total) with poll blocking
        - DAILY_QUOTA: Bounded probe, then sleep until midnight Chicago
        - CONFIGURATION: Exit gracefully
        - RATE_LIMIT: 2x backoff with poll blocking
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
                        case ErrorCategory.DAILY_QUOTA:
                            # CTA error 102: daily quota exceeded
                            await self._handle_daily_quota_error(client, e)
                            continue  # Skip normal sleep, handled in _handle_daily_quota_error
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
                            # Apply longer backoff for rate limits (poll blocking)
                            await self.sleep(self.train_poll_interval * 2)
                            continue
                        case ErrorCategory.TRANSIENT:
                            # Extended retry with poll blocking
                            success = await self._retry_with_extended_backoff(client, e)
                            if not success:
                                # Retry exhausted, log gap and resume schedule
                                self.logger.warning(
                                    "Transient error retry exhausted. Accepting gap and resuming schedule.",
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

        Orchestrates: record poll timestamp → fetch from CTA → normalize →
        store and update state. Fetch and store are delegated to helpers.

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
                async with self.diagnostics.span(
                    "train_positions_cycle", cycle_id=cycle_id
                ):
                    poll_timestamp = time.time()
                    raw_response = await self._fetch_train_positions_from_cta(
                        client, cycle_id
                    )

                    records = normalize_train_positions(
                        raw_response,
                        datetime.datetime.fromtimestamp(
                            poll_timestamp, tz=ZoneInfo("America/Chicago")
                        ),
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

                    self._store_train_position_records(
                        records, cycle_id, cycle_start_time, poll_timestamp
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

    async def _fetch_train_positions_from_cta(
        self, client: httpx.AsyncClient, cycle_id: str
    ) -> dict[str, Any]:
        """Fetch raw train positions from CTA API with aiometer rate limiting."""

        async def _fetch() -> dict[str, Any]:
            async with self.diagnostics.span(
                "cta.get_train_positions", cycle_id=cycle_id
            ):
                return await get_train_positions(client)

        self.diagnostics.record_event(
            "aiometer_run",
            operation="cta.get_train_positions",
            item_count=1,
            max_per_second=self.cta_max_per_second,
            max_at_once=self.cta_max_at_once,
        )
        jobs = [_fetch]
        results = await aiometer.run_all(
            jobs,
            max_at_once=self.cta_max_at_once,
            max_per_second=self.cta_max_per_second,
        )
        return results[0]

    def _store_train_position_records(
        self,
        records: list[dict[str, Any]],
        cycle_id: str,
        cycle_start_time: float,
        poll_timestamp: float,
    ) -> None:
        """Store records to Parquet, update daemon state on success, log and record diagnostics."""
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
            return

        self.last_poll_timestamp = poll_timestamp
        self.total_records_collected += len(records)
        self.current_poll_count += 1

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

    async def _retry_with_extended_backoff(
        self, client: httpx.AsyncClient, original_error: Exception
    ) -> bool:
        """Retry the collection cycle with extended backoff for transient errors.

        Uses stamina retry with extended max wait (5-10 minutes total).
        Blocks subsequent polls during retry - only returns to normal schedule
        when retry succeeds or exhausts attempts.

        Args:
            client: HTTP client for CTA API requests
            original_error: The original transient error

        Returns:
            True if retry succeeded, False if retry exhausted

        """
        self.logger.warning(
            "Transient error detected. Retrying with extended backoff (5-10 min total).",
            extra={
                "extra_fields": {
                    "error_type": type(original_error).__name__,
                    "error_message": str(original_error),
                }
            },
        )

        # Use stamina retry with extended backoff for transient errors
        # This blocks the poll - we won't continue to next cycle until retry completes
        retry_attempts = [0]  # Mutable list to track attempts in closure

        try:
            async for attempt in stamina.retry_context(
                on=Exception,
                attempts=10,
                wait_initial=1.0,
                wait_max=60.0,  # Up to 60s between retries
                wait_jitter=5.0,
            ):
                with attempt:
                    retry_attempts[0] += 1
                    self.logger.info(
                        f"Retry attempt {retry_attempts[0]}/10 for transient error",
                        extra={
                            "extra_fields": {
                                "retry_attempt": retry_attempts[0],
                                "original_error_type": type(original_error).__name__,
                            }
                        },
                    )
                    try:
                        await self._collect_train_positions_cycle(client)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        cat = classify_error(e)
                        # Only continue retrying if it's still transient
                        # If it becomes CONFIGURATION or DAILY_QUOTA, let it bubble up
                        if cat != ErrorCategory.TRANSIENT:
                            self.logger.warning(
                                f"Error category changed from TRANSIENT to {cat.value} during retry. Stopping retry.",
                                extra={
                                    "extra_fields": {
                                        "new_category": cat.value,
                                        "error_type": type(e).__name__,
                                    }
                                },
                            )
                            raise  # Let the outer handler deal with it
                        # Continue retry loop for transient errors
                        raise
                    else:
                        # Success path - no exception raised
                        self.logger.info(
                            f"Retry succeeded on attempt {retry_attempts[0]}",
                            extra={"extra_fields": {"retry_attempt": retry_attempts[0]}},
                        )
                        return True
        except Exception as e:
            # Retry exhausted - stamina raises the last exception after all attempts
            # Check if it's still a transient error or if category changed
            cat = classify_error(e)
            if cat != ErrorCategory.TRANSIENT:
                # Category changed during retry - re-raise for outer handler
                raise
            # Retry exhausted for transient error
            return False

        # Should not reach here, but for completeness
        return False

    async def _handle_daily_quota_error(
        self, client: httpx.AsyncClient, error: Exception
    ) -> None:
        """Handle CTA daily quota exceeded (error 102) with bounded probe and midnight sleep.

        Strategy:
        1. Bounded probe: probe_102_attempts times with probe_102_intervals
        2. If time until midnight < 15 min, skip probe and go straight to midnight sleep
        3. Each probe: sleep interval, then one poll attempt
        4. On success: resume normal 15s schedule
        5. On continued 102: next probe or sleep until midnight
        6. Sleep until midnight: compute next midnight Chicago + 5 min buffer, sleep in chunks

        All sleeps are chunked and shutdown-interruptible via self.sleep().

        Args:
            client: HTTP client for CTA API requests
            error: The original DAILY_QUOTA error

        """
        self.logger.critical(
            "CTA daily API quota exceeded (error 102). Initiating bounded probe then midnight sleep.",
            extra={
                "extra_fields": {
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "probe_attempts": self.probe_102_attempts,
                    "probe_intervals": self.probe_102_intervals,
                }
            },
        )

        # Compute time until next midnight Chicago
        chicago_tz = ZoneInfo("America/Chicago")
        now_chicago = datetime.datetime.now(chicago_tz)
        next_midnight = (now_chicago + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        seconds_until_midnight = (next_midnight - now_chicago).total_seconds()

        # If less than 15 minutes until midnight, skip probe and go straight to midnight sleep
        min_seconds_for_probe = 900  # 15 minutes
        if seconds_until_midnight < min_seconds_for_probe:
            self.logger.info(
                f"Less than 15 minutes until midnight ({seconds_until_midnight:.0f}s). Skipping probe, sleeping until midnight.",
                extra={
                    "extra_fields": {
                        "seconds_until_midnight": seconds_until_midnight,
                        "next_midnight_chicago": next_midnight.isoformat(),
                    }
                },
            )
            await self._sleep_until_midnight(next_midnight)
            return

        # Bounded probe
        for probe_idx in range(self.probe_102_attempts):
            if probe_idx < len(self.probe_102_intervals):
                interval = self.probe_102_intervals[probe_idx]
            else:
                # If we run out of configured intervals, use the last one
                interval = self.probe_102_intervals[-1]

            self.logger.info(
                f"Probe {probe_idx + 1}/{self.probe_102_attempts}: sleeping {interval}s before next poll attempt",
                extra={
                    "extra_fields": {
                        "probe_index": probe_idx + 1,
                        "probe_interval_seconds": interval,
                    }
                },
            )

            await self.sleep(interval)
            if not self.running:
                return  # Daemon shutting down

            # Attempt one poll
            try:
                self.logger.info(
                    f"Probe {probe_idx + 1}/{self.probe_102_attempts}: attempting poll",
                    extra={"extra_fields": {"probe_index": probe_idx + 1}},
                )
                await self._collect_train_positions_cycle(client)
            except Exception as e:
                cat = classify_error(e)
                if cat == ErrorCategory.DAILY_QUOTA:
                    self.logger.warning(
                        f"Probe {probe_idx + 1} still returns 102. Continuing to next probe or midnight sleep.",
                        extra={
                            "extra_fields": {
                                "probe_index": probe_idx + 1,
                                "error_type": type(e).__name__,
                            }
                        },
                    )
                    continue  # Try next probe
                # Different error category - let outer handler deal with it
                self.logger.warning(
                    f"Probe {probe_idx + 1} returned non-102 error ({cat.value}). Re-raising for outer handler.",
                    extra={
                        "extra_fields": {
                            "probe_index": probe_idx + 1,
                            "error_category": cat.value,
                            "error_type": type(e).__name__,
                        }
                    },
                )
                raise
            else:
                # Success! Resume normal schedule
                self.logger.info(
                    f"Probe {probe_idx + 1} succeeded! Daily quota appears restored. Resuming normal schedule.",
                    extra={"extra_fields": {"probe_index": probe_idx + 1}},
                )
                return

        # All probes exhausted and still 102 - sleep until midnight
        self.logger.info(
            "All probes exhausted, still returning 102. Sleeping until midnight Chicago.",
            extra={
                "extra_fields": {
                    "next_midnight_chicago": next_midnight.isoformat(),
                }
            },
        )

        # Recompute next midnight in case probes took significant time
        now_chicago = datetime.datetime.now(chicago_tz)
        next_midnight = (now_chicago + datetime.timedelta(days=1)).replace(
            hour=0, minute=5, second=0, microsecond=0
        )  # Midnight + 5 min buffer
        await self._sleep_until_midnight(next_midnight)

    async def _sleep_until_midnight(
        self, target_time: datetime.datetime
    ) -> None:
        """Sleep until target_time in chunks to allow shutdown interruption.

        Args:
            target_time: Target datetime to sleep until (must be timezone-aware)

        """
        while self.running:
            now = datetime.datetime.now(target_time.tzinfo)
            remaining_seconds = (target_time - now).total_seconds()

            if remaining_seconds <= 0:
                self.logger.info(
                    "Reached target time (midnight + 5 min). Resuming normal schedule.",
                    extra={
                        "extra_fields": {
                            "target_time": target_time.isoformat(),
                        }
                    },
                )
                return

            # Sleep in chunks of 60 seconds to allow shutdown checks
            chunk_seconds = min(60.0, remaining_seconds)
            self.logger.debug(
                f"Sleeping {chunk_seconds:.0f}s (remaining: {remaining_seconds:.0f}s until {target_time.isoformat()})",
                extra={
                    "extra_fields": {
                        "chunk_seconds": chunk_seconds,
                        "remaining_seconds": remaining_seconds,
                        "target_time": target_time.isoformat(),
                    }
                },
            )
            await self.sleep(chunk_seconds)

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
