"""TTL-based cache infrastructure with file persistence for static data.

This module provides a generic caching system with time-to-live (TTL) semantics
and JSON file-based persistence, enabling daemon restarts without losing cached data.
"""

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class CachedData[T]:
    """Generic TTL cache with lazy refresh and file persistence.

    Provides in-memory caching with automatic refresh when TTL expires.
    Data is persisted to JSON file to survive daemon restarts.

    Type Parameters:
        T: The type of cached data

    Attributes:
        _cache_file: Path to JSON cache file
        _ttl: Time-to-live in seconds
        _fetch_fn: Function to fetch fresh data when cache expires
        _memory_cache: In-memory cache dict with data and metadata
    """

    def __init__(
        self,
        cache_file: Path,
        ttl: int,
        fetch_fn: Callable[[], T],
    ) -> None:
        """Initialize cache with file path, TTL, and fetch function.

        Args:
            cache_file: Path to JSON cache file for persistence
            ttl: Time-to-live in seconds before refresh needed
            fetch_fn: Callable that fetches fresh data when cache expires
        """
        self._cache_file = cache_file
        self._ttl = ttl
        self._fetch_fn = fetch_fn
        self._memory_cache: dict[str, Any] | None = None

    def get(self) -> T:
        """Get cached data, refreshing if expired or missing.

        Returns:
            Cached data of type T

        Raises:
            Any exception raised by fetch_fn during refresh
        """
        # Load from file if not in memory
        if self._memory_cache is None:
            self._load_from_file()

        # Refresh if expired or missing
        if self._memory_cache is None or self._is_expired():
            self._refresh()

        # Return cached data (guaranteed to exist after refresh)
        assert self._memory_cache is not None
        return self._memory_cache["data"]

    def _is_expired(self) -> bool:
        """Check if cached data has exceeded TTL.

        Returns:
            True if cache is expired, False otherwise
        """
        if self._memory_cache is None:
            return True

        cached_at = self._memory_cache.get("cached_at", 0)
        return time.time() - cached_at > self._ttl

    def _load_from_file(self) -> None:
        """Load cache from JSON file into memory.

        Sets _memory_cache to None if file doesn't exist or JSON is invalid.
        Missing files are normal for first run; JSON errors log warning.
        """
        if not self._cache_file.exists():
            logger.debug(f"Cache file not found: {self._cache_file} (will fetch)")
            self._memory_cache = None
            return

        try:
            with open(self._cache_file, "r") as f:
                self._memory_cache = json.load(f)
            logger.debug(f"Loaded cache from {self._cache_file}")
        except json.JSONDecodeError as e:
            logger.warning(
                f"Failed to decode cache file {self._cache_file}: {e} (will refresh)"
            )
            self._memory_cache = None

    def _save_to_file(self) -> None:
        """Save current memory cache to JSON file.

        Creates parent directory if missing. Logs errors but doesn't raise
        (failure to save cache is non-critical, just means slower next start).
        """
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._cache_file, "w") as f:
                json.dump(self._memory_cache, f, indent=2)
            logger.debug(f"Saved cache to {self._cache_file}")
        except OSError as e:
            logger.error(f"Failed to save cache to {self._cache_file}: {e}")

    def _refresh(self) -> None:
        """Fetch fresh data, update cache, and save to file.

        Calls fetch_fn to get new data, updates memory cache with current
        timestamp and TTL, then persists to file.

        Raises:
            Any exception raised by fetch_fn
        """
        logger.info(f"Refreshing cache from fetch function")
        data = self._fetch_fn()

        self._memory_cache = {
            "data": data,
            "cached_at": time.time(),
            "ttl": self._ttl,
        }

        self._save_to_file()
        logger.info(f"Cache refreshed successfully")


def create_cached_data[T](
    cache_name: str,
    config: dict[str, dict[str, str | int | float | bool]],
    fetch_fn: Callable[[], T],
) -> CachedData[T]:
    """Create a CachedData instance from configuration.

    Factory function that constructs cache from config dict, following
    established pattern from Phase 1/2 for config-driven instantiation.

    Args:
        cache_name: Name of cache (e.g., "stations", "track_geometry")
        config: Configuration dict with "cache" section
        fetch_fn: Callable that fetches fresh data when cache expires

    Returns:
        Configured CachedData instance

    Raises:
        ValueError: If cache_name TTL not found in config
        OSError: If cache directory creation fails
    """
    cache_config = config["cache"]

    # Construct cache file path
    cache_dir = Path(str(cache_config["directory"]))
    cache_file = cache_dir / f"{cache_name}.json"

    # Look up TTL for this cache
    ttl_key = f"{cache_name}_ttl"
    if ttl_key not in cache_config:
        raise ValueError(
            f"No TTL configured for cache '{cache_name}' "
            f"(expected key '{ttl_key}' in [cache] section)"
        )

    ttl = int(cache_config[ttl_key])

    # Create cache directory if missing
    cache_dir.mkdir(parents=True, exist_ok=True)

    return CachedData(cache_file, ttl, fetch_fn)
