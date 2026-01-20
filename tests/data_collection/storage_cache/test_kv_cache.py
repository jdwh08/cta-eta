"""Unit tests for PersistentKVCache (per-entry TTL KV persistence)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from cta_eta.data_collection.storage_cache.kv_cache import PersistentKVCache

if TYPE_CHECKING:
    from pathlib import Path


class TestPersistentKVCache:
    """Test cases for PersistentKVCache."""

    @pytest.fixture
    def cache_file(self, tmp_path: Path) -> Path:
        """Create cache file path for testing."""
        return tmp_path / "kv_cache.json"

    @pytest.fixture
    def cache(self, cache_file: Path) -> PersistentKVCache[dict[str, str]]:
        """Create a KV cache instance with TTL enabled."""
        return PersistentKVCache(cache_file=cache_file, ttl=60)

    def test_init_sets_attributes(self, cache_file: Path) -> None:
        """Test that __init__ sets all attributes correctly."""
        # Arrange
        ttl = 123

        # Act
        cache = PersistentKVCache(cache_file=cache_file, ttl=ttl)

        # Assert
        assert cache._cache_file == cache_file
        assert cache._ttl == ttl
        assert cache._memory_cache is None

    def test_get_missing_key_returns_none_without_creating_file(
        self, cache: PersistentKVCache[dict[str, str]], cache_file: Path
    ) -> None:
        """Test get() returns None on missing key and does not persist."""
        # Arrange
        assert not cache_file.exists()

        # Act
        value = cache.get("missing")

        # Assert
        assert value is None
        assert not cache_file.exists()

    def test_set_then_get_persists_and_can_be_loaded_by_new_instance(
        self, mocker: pytest.MockFixture, cache_file: Path
    ) -> None:
        """Test set() persists to disk and new instance can read it."""
        # Arrange
        cache = PersistentKVCache[dict[str, str]](cache_file=cache_file, ttl=60)
        call_count = 0

        def fake_time() -> float:
            nonlocal call_count
            call_count += 1
            # set(): updated_at then saved_at
            if call_count <= 2:  # noqa: PLR2004
                return 100.0
            # subsequent calls (e.g., get/items/prune) should not expire
            return 101.0

        mocker.patch(
            "cta_eta.data_collection.storage_cache.kv_cache.time.time",
            side_effect=fake_time,
        )

        # Act
        cache.set("station_123", {"grid": "A1"})
        reloaded = PersistentKVCache[dict[str, str]](cache_file=cache_file, ttl=60)
        value = reloaded.get("station_123")

        # Assert
        assert cache_file.exists()
        assert value == {"grid": "A1"}

        payload = json.loads(cache_file.read_text())
        assert payload["schema_version"] == 1
        assert payload["saved_at"] == 100.0  # noqa: PLR2004
        assert payload["data"]["station_123"]["value"] == {"grid": "A1"}
        assert payload["data"]["station_123"]["updated_at"] == 100.0  # noqa: PLR2004

    def test_get_with_expired_entry_returns_none(
        self, mocker: pytest.MockFixture, tmp_path: Path
    ) -> None:
        """Test TTL expiry path for get()."""
        # Arrange
        cache_file = tmp_path / "kv.json"
        cache = PersistentKVCache[str](cache_file=cache_file, ttl=10)
        mocker.patch(
            "cta_eta.data_collection.storage_cache.kv_cache.time.time",
            side_effect=[100.0, 100.0],
        )
        cache.set("k", "v")

        # Act
        mocker.patch(
            "cta_eta.data_collection.storage_cache.kv_cache.time.time",
            return_value=111.0,
        )
        value = cache.get("k")

        # Assert
        assert value is None

    def test_get_with_ttl_none_never_expires(
        self, mocker: pytest.MockFixture, cache_file: Path
    ) -> None:
        """Test TTL disabled path: entries never expire."""
        # Arrange
        cache = PersistentKVCache[str](cache_file=cache_file, ttl=None)
        mocker.patch(
            "cta_eta.data_collection.storage_cache.kv_cache.time.time",
            side_effect=[100.0, 100.0],
        )
        cache.set("k", "v")

        # Act
        mocker.patch(
            "cta_eta.data_collection.storage_cache.kv_cache.time.time",
            return_value=10_000.0,
        )
        value = cache.get("k")

        # Assert
        assert value == "v"

    def test_items_filters_expired_and_corrupt_entries(
        self, mocker: pytest.MockFixture, cache_file: Path
    ) -> None:
        """Test items() skips expired entries and malformed payloads."""
        # Arrange
        cache = PersistentKVCache[str](cache_file=cache_file, ttl=10)
        cache._memory_cache = {
            "schema_version": 1,
            "saved_at": 0.0,
            "data": {
                "fresh": {"value": "a", "updated_at": 100.0},
                "expired": {"value": "b", "updated_at": 89.0},
                "not_a_dict": "nope",
                "missing_updated_at": {"value": "x"},
                "bad_updated_at_type": {"value": "y", "updated_at": "100"},
            },
        }
        mocker.patch(
            "cta_eta.data_collection.storage_cache.kv_cache.time.time",
            return_value=100.0,
        )

        # Act
        items = cache.items()

        # Assert
        assert items == [("fresh", "a")]

    def test_prune_expired_removes_and_persists(
        self, mocker: pytest.MockFixture, cache_file: Path
    ) -> None:
        """Test prune_expired() removes expired keys and calls save."""
        # Arrange
        cache = PersistentKVCache[str](cache_file=cache_file, ttl=10)
        cache._memory_cache = {
            "schema_version": 1,
            "saved_at": 0.0,
            "data": {
                "fresh": {"value": "a", "updated_at": 100.0},
                "expired": {"value": "b", "updated_at": 89.0},
            },
        }
        mocker.patch(
            "cta_eta.data_collection.storage_cache.kv_cache.time.time",
            return_value=100.0,
        )
        save_spy = mocker.spy(cache, "_save_to_file")

        # Act
        removed = cache.prune_expired()

        # Assert
        assert removed == 1
        assert cache._memory_cache is not None
        assert "expired" not in cache._memory_cache["data"]
        assert "fresh" in cache._memory_cache["data"]
        save_spy.assert_called_once()

    def test_prune_expired_ttl_none_is_noop(
        self, mocker: pytest.MockFixture, cache_file: Path
    ) -> None:
        """Test prune_expired() returns 0 when TTL disabled."""
        # Arrange
        cache = PersistentKVCache[str](cache_file=cache_file, ttl=None)
        cache._memory_cache = {"schema_version": 1, "saved_at": 0.0, "data": {}}
        save_spy = mocker.spy(cache, "_save_to_file")

        # Act
        removed = cache.prune_expired()

        # Assert
        assert removed == 0
        save_spy.assert_not_called()

    def test_delete_existing_key_persists(
        self, mocker: pytest.MockFixture, cache_file: Path
    ) -> None:
        """Test delete() removes key and persists when key exists."""
        # Arrange
        cache = PersistentKVCache[str](cache_file=cache_file, ttl=60)
        cache._memory_cache = {
            "schema_version": 1,
            "saved_at": 0.0,
            "data": {"k": {"value": "v", "updated_at": 100.0}},
        }
        save_spy = mocker.spy(cache, "_save_to_file")

        # Act
        cache.delete("k")

        # Assert
        assert cache._memory_cache is not None
        assert "k" not in cache._memory_cache["data"]
        save_spy.assert_called_once()

    def test_delete_missing_key_does_not_persist(
        self, mocker: pytest.MockFixture, cache_file: Path
    ) -> None:
        """Test delete() is a no-op when key missing (no file write)."""
        # Arrange
        cache = PersistentKVCache[str](cache_file=cache_file, ttl=60)
        cache._memory_cache = {"schema_version": 1, "saved_at": 0.0, "data": {}}
        save_spy = mocker.spy(cache, "_save_to_file")

        # Act
        cache.delete("missing")

        # Assert
        save_spy.assert_not_called()

    def test_load_from_file_invalid_json_starts_empty(self, cache_file: Path) -> None:
        """Test invalid JSON on disk is handled as empty cache."""
        # Arrange
        cache_file.write_text("{ invalid json }")
        cache = PersistentKVCache[str](cache_file=cache_file, ttl=60)

        # Act
        value = cache.get("anything")

        # Assert
        assert value is None

    def test_load_from_file_non_dict_payload_starts_empty(
        self, cache_file: Path
    ) -> None:
        """Test non-dict JSON payload is treated as empty cache."""
        # Arrange
        cache_file.write_text(json.dumps([1, 2, 3]))
        cache = PersistentKVCache[str](cache_file=cache_file, ttl=60)

        # Act
        value = cache.get("k")

        # Assert
        assert value is None

    def test_get_returns_none_for_malformed_entry(self, cache_file: Path) -> None:
        """Test get() returns None for malformed entries (wrong types)."""
        # Arrange
        cache = PersistentKVCache[str](cache_file=cache_file, ttl=60)
        cache._memory_cache = {
            "schema_version": 1,
            "saved_at": 0.0,
            "data": {
                "bad_entry": "nope",
                "missing_updated_at": {"value": "x"},
                "bad_updated_at": {"value": "y", "updated_at": "100"},
            },
        }

        # Act
        bad_entry = cache.get("bad_entry")
        missing_updated_at = cache.get("missing_updated_at")
        bad_updated_at = cache.get("bad_updated_at")

        # Assert
        assert bad_entry is None
        assert missing_updated_at is None
        assert bad_updated_at is None

    def test_get_data_dict_repairs_non_dict_data_field(self, cache_file: Path) -> None:
        """Test _get_data_dict() replaces non-dict 'data' payload with dict."""
        # Arrange
        cache = PersistentKVCache[str](cache_file=cache_file, ttl=60)
        cache._memory_cache = {"schema_version": 1, "saved_at": 0.0, "data": []}

        # Act
        data = cache._get_data_dict()

        # Assert
        assert data == {}
        assert isinstance(cache._memory_cache["data"], dict)

    def test_save_to_file_oserror_is_swallowed_and_temp_cleaned(
        self, mocker: pytest.MockFixture, tmp_path: Path
    ) -> None:
        """Test _save_to_file() handles OSError and removes temp file best-effort."""
        # Arrange
        cache_file = tmp_path / "cache.json"
        cache = PersistentKVCache[str](cache_file=cache_file, ttl=60)
        cache._memory_cache = {
            "schema_version": 1,
            "saved_at": 0.0,
            "data": {"k": {"value": "v", "updated_at": 100.0}},
        }

        # Avoid reliance on fsync semantics for this failure-path test.
        mocker.patch("cta_eta.data_collection.storage_cache.kv_cache.os.fsync")
        mocker.patch(
            "cta_eta.data_collection.storage_cache.kv_cache.Path.replace",
            side_effect=OSError("replace failed"),
        )

        # Act (should not raise)
        cache._save_to_file()

        # Assert
        tmp_files = list(cache_file.parent.glob(f".{cache_file.name}.*.tmp"))
        assert tmp_files == []

    def test_set_with_non_json_serializable_value_raises_type_error(
        self, mocker: pytest.MockFixture, cache_file: Path
    ) -> None:
        """Test set() raises when value cannot be JSON serialized."""
        # Arrange
        cache = PersistentKVCache[object](cache_file=cache_file, ttl=60)
        mocker.patch(
            "cta_eta.data_collection.storage_cache.kv_cache.time.time",
            side_effect=[100.0, 100.0],
        )

        # Act & Assert
        with pytest.raises(TypeError):
            cache.set("k", object())
