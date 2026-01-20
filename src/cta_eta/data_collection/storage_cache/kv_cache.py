"""Persistent key-value cache with per-entry TTL and file backing.

This cache is intended for *derived* mappings that are learned incrementally over time,
such as station_id → weather grid identifier mappings.

Unlike `CachedData`, this cache does not assume there exists a canonical "full refresh"
endpoint. Entries are added/updated/deleted individually and persisted to disk so the
collector can restart without losing learned mappings.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PersistentKVCache[V]:
    """Persistent key-value cache with per-entry TTL.

    Values must be JSON-serializable.

    On-disk schema:
        {
          "schema_version": 1,
          "saved_at": <epoch seconds>,
          "data": {
            "<key>": {"value": <json>, "updated_at": <epoch seconds>},
            ...
          }
        }
    """

    def __init__(self, cache_file: Path, ttl: int | None) -> None:
        """Initialize cache with file path and optional TTL.

        Args:
            cache_file: Path to JSON cache file for persistence
            ttl: Per-entry TTL in seconds. If None, entries never expire.

        """
        self._cache_file = cache_file
        self._ttl = ttl
        self._memory_cache: dict[str, Any] | None = None

    def get(self, key: str) -> V | None:
        """Get a value if present and not expired.

        Args:
            key: Cache key

        Returns:
            Cached value, or None if missing or expired.

        """
        data = self._get_data_dict()
        entry = data.get(key)
        if not isinstance(entry, dict):
            return None

        updated_at = entry.get("updated_at")
        if not isinstance(updated_at, (int, float)):
            return None

        if self._ttl is not None and time.time() - float(updated_at) > self._ttl:
            return None

        # Values are expected to be JSON-serializable; return as-is.
        return entry.get("value")

    def set(self, key: str, value: V) -> None:
        """Set/update a cache entry and persist to disk.

        Args:
            key: Cache key
            value: JSON-serializable value

        """
        data = self._get_data_dict()
        data[key] = {"value": value, "updated_at": time.time()}
        self._save_to_file()

    def delete(self, key: str) -> None:
        """Delete a cache entry if present and persist to disk."""
        data = self._get_data_dict()
        if key in data:
            del data[key]
            self._save_to_file()

    def items(self) -> list[tuple[str, V]]:
        """Return non-expired items as (key, value) pairs."""
        now = time.time()
        out: list[tuple[str, V]] = []
        data = self._get_data_dict()
        for k, entry in data.items():
            if not isinstance(entry, dict):
                continue
            updated_at = entry.get("updated_at")
            if not isinstance(updated_at, (int, float)):
                continue
            if self._ttl is not None and now - float(updated_at) > self._ttl:
                continue
            out.append((k, entry.get("value")))
        return out

    def prune_expired(self) -> int:
        """Remove expired entries and persist if any were removed.

        Returns:
            Number of entries removed.

        """
        if self._ttl is None:
            return 0

        data = self._get_data_dict()
        now = time.time()

        expired_keys = [
            k
            for k, entry in data.items()
            if isinstance(entry, dict)
            and isinstance(entry.get("updated_at"), (int, float))
            and now - float(entry["updated_at"]) > self._ttl
        ]
        for k in expired_keys:
            del data[k]

        if expired_keys:
            self._save_to_file()

        return len(expired_keys)

    def _get_data_dict(self) -> dict[str, Any]:
        """Return the internal data dict, loading from disk if needed."""
        if self._memory_cache is None:
            self._load_from_file()
        if self._memory_cache is None:
            self._memory_cache = {"schema_version": 1, "saved_at": 0.0, "data": {}}

        data = self._memory_cache.get("data")
        if not isinstance(data, dict):
            data = {}
            self._memory_cache["data"] = data
        return data

    def _load_from_file(self) -> None:
        """Load cache from JSON file into memory.

        Sets _memory_cache to None if file doesn't exist or JSON is invalid.
        Missing files are normal for first run; JSON errors log warning.
        """
        if not self._cache_file.exists():
            logger.debug(
                f"KV cache file not found: {self._cache_file} (starting empty)"
            )
            self._memory_cache = None
            return

        try:
            with self._cache_file.open() as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                msg = f"Unexpected KV cache payload type: {type(payload)} (starting empty)"
                logger.warning(msg)
                self._memory_cache = None
                return

            self._memory_cache = payload
            logger.debug(f"Loaded KV cache from {self._cache_file}")
        except json.JSONDecodeError as e:
            msg = f"Failed to decode KV cache file {self._cache_file}: {e} (starting empty)"
            logger.warning(msg)
            self._memory_cache = None

    def _save_to_file(self) -> None:
        """Persist memory cache to disk using atomic replace.

        Creates parent directory if missing. Logs errors but doesn't raise
        (failure to save cache is non-critical, just means slower next start).
        """
        if self._memory_cache is None:
            # Nothing to save.
            return

        self._memory_cache["schema_version"] = 1
        self._memory_cache["saved_at"] = time.time()

        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)

            # Write to a temp file in the same directory then atomically replace.
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self._cache_file.parent),
                prefix=f".{self._cache_file.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                json.dump(self._memory_cache, tmp, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = Path(tmp.name)

            tmp_path.replace(self._cache_file)
            logger.debug(f"Saved KV cache to {self._cache_file}")
        except OSError as e:
            msg = f"Failed to save KV cache to {self._cache_file}: {e}"
            logger.exception(msg)
            # Best-effort cleanup of temp file if it exists.
            try:
                if "tmp_path" in locals() and tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
            except OSError:
                logger.debug("Failed to clean up KV cache temp file after error")
