"""Unit tests for TTL-based cache infrastructure with file persistence."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from cta_eta.data_collection.storage_cache.cache import CachedData, create_cached_data


class TestCachedData:
    """Test cases for CachedData generic class."""

    @pytest.fixture
    def temp_cache_dir(self, tmp_path: Path) -> Path:
        """Create temporary directory for cache files."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        return cache_dir

    @pytest.fixture
    def cache_file(self, temp_cache_dir: Path) -> Path:
        """Create cache file path for testing."""
        return temp_cache_dir / "test_cache.json"

    @pytest.fixture
    def mock_fetch_fn(self) -> Mock:
        """Create mock fetch function."""
        return Mock(return_value={"test": "data"})

    @pytest.fixture
    def cached_data(
        self, cache_file: Path, mock_fetch_fn: Mock
    ) -> CachedData[dict[str, str]]:
        """Create CachedData instance for testing."""
        return CachedData(cache_file=cache_file, ttl=3600, fetch_fn=mock_fetch_fn)

    def test_init_sets_attributes(self, cache_file: Path, mock_fetch_fn: Mock) -> None:
        """Test that __init__ sets all attributes correctly."""
        # Arrange
        ttl = 3600

        # Act
        cache = CachedData(cache_file=cache_file, ttl=ttl, fetch_fn=mock_fetch_fn)

        # Assert
        assert cache._cache_file == cache_file
        assert cache._ttl == ttl
        assert cache._fetch_fn is mock_fetch_fn
        assert cache._memory_cache is None

    def test_get_with_no_file_calls_fetch_fn(
        self, cached_data: CachedData[dict[str, str]], mock_fetch_fn: Mock
    ) -> None:
        """Test that get() calls fetch_fn when cache file doesn't exist."""
        # Arrange
        assert not cached_data._cache_file.exists()

        # Act
        result = cached_data.get()

        # Assert
        mock_fetch_fn.assert_called_once()
        assert result == {"test": "data"}
        assert cached_data._memory_cache is not None
        assert cached_data._memory_cache["data"] == {"test": "data"}
        assert "cached_at" in cached_data._memory_cache
        assert cached_data._memory_cache["ttl"] == 3600

    def test_get_with_no_file_saves_to_file(
        self, cached_data: CachedData[dict[str, str]], cache_file: Path
    ) -> None:
        """Test that get() saves cache to file when file doesn't exist."""
        # Arrange
        assert not cache_file.exists()

        # Act
        cached_data.get()

        # Assert
        assert cache_file.exists()
        with cache_file.open() as f:
            saved_data = json.load(f)
        assert saved_data["data"] == {"test": "data"}
        assert "cached_at" in saved_data
        assert saved_data["ttl"] == 3600

    def test_get_with_valid_file_loads_from_file(
        self,
        cached_data: CachedData[dict[str, str]],
        cache_file: Path,
        mock_fetch_fn: Mock,
    ) -> None:
        """Test that get() loads from file when valid cache exists."""
        # Arrange
        cached_data_obj = {
            "data": {"cached": "value"},
            "cached_at": time.time(),
            "ttl": 3600,
        }
        with cache_file.open("w") as f:
            json.dump(cached_data_obj, f)

        # Act
        result = cached_data.get()

        # Assert
        assert result == {"cached": "value"}
        mock_fetch_fn.assert_not_called()

    def test_get_with_expired_cache_refreshes(
        self,
        cached_data: CachedData[dict[str, str]],
        cache_file: Path,
        mock_fetch_fn: Mock,
    ) -> None:
        """Test that get() refreshes cache when TTL has expired."""
        # Arrange
        old_time = time.time() - 7200  # 2 hours ago (expired for 1 hour TTL)
        cached_data_obj = {
            "data": {"old": "value"},
            "cached_at": old_time,
            "ttl": 3600,
        }
        with cache_file.open("w") as f:
            json.dump(cached_data_obj, f)

        # Act
        result = cached_data.get()

        # Assert
        assert result == {"test": "data"}
        mock_fetch_fn.assert_called_once()
        assert cached_data._memory_cache is not None
        assert cached_data._memory_cache["data"] == {"test": "data"}

    def test_get_with_invalid_json_refreshes(
        self,
        cached_data: CachedData[dict[str, str]],
        cache_file: Path,
        mock_fetch_fn: Mock,
    ) -> None:
        """Test that get() refreshes cache when JSON file is invalid."""
        # Arrange
        cache_file.write_text("{ invalid json }")

        # Act
        result = cached_data.get()

        # Assert
        assert result == {"test": "data"}
        mock_fetch_fn.assert_called_once()

    def test_get_with_missing_cached_at_refreshes(
        self,
        cached_data: CachedData[dict[str, str]],
        cache_file: Path,
        mock_fetch_fn: Mock,
    ) -> None:
        """Test that get() refreshes cache when cached_at is missing."""
        # Arrange
        cached_data_obj = {"data": {"test": "value"}, "ttl": 3600}
        with cache_file.open("w") as f:
            json.dump(cached_data_obj, f)

        # Act
        result = cached_data.get()

        # Assert
        assert result == {"test": "data"}
        mock_fetch_fn.assert_called_once()

    def test_get_when_fetch_fn_raises_exception_propagates(
        self, cache_file: Path
    ) -> None:
        """Test that get() propagates exceptions from fetch_fn."""
        # Arrange
        error = ValueError("Fetch failed")
        failing_fetch = Mock(side_effect=error)
        cache = CachedData(cache_file=cache_file, ttl=3600, fetch_fn=failing_fetch)

        # Act & Assert
        with pytest.raises(ValueError, match="Fetch failed"):
            cache.get()

    def test_get_after_refresh_failure_raises_value_error(
        self, cache_file: Path
    ) -> None:
        """Test that get() raises ValueError if cache is None after refresh."""
        # Arrange
        fetch_fn = Mock(return_value={"data": "value"})

        cache = CachedData(cache_file=cache_file, ttl=3600, fetch_fn=fetch_fn)

        # Mock _save_to_file to set _memory_cache to None (simulating failure)
        original_save = cache._save_to_file

        def failing_save() -> None:
            cache._memory_cache = None
            original_save()

        cache._save_to_file = failing_save  # type: ignore[assignment]

        # Act & Assert
        with pytest.raises(ValueError, match="Cache failed to load after refresh"):
            cache.get()

    def test_is_expired_with_no_cache_returns_true(
        self, cached_data: CachedData[dict[str, str]]
    ) -> None:
        """Test that _is_expired() returns True when cache is None."""
        # Arrange
        cached_data._memory_cache = None

        # Act
        result = cached_data._is_expired()

        # Assert
        assert result is True

    def test_is_expired_with_fresh_cache_returns_false(
        self, cached_data: CachedData[dict[str, str]]
    ) -> None:
        """Test that _is_expired() returns False when cache is fresh."""
        # Arrange
        cached_data._memory_cache = {
            "data": {"test": "data"},
            "cached_at": time.time(),
            "ttl": 3600,
        }

        # Act
        result = cached_data._is_expired()

        # Assert
        assert result is False

    def test_is_expired_with_expired_cache_returns_true(
        self, cached_data: CachedData[dict[str, str]]
    ) -> None:
        """Test that _is_expired() returns True when cache is expired."""
        # Arrange
        cached_data._memory_cache = {
            "data": {"test": "data"},
            "cached_at": time.time() - 7200,  # 2 hours ago
            "ttl": 3600,
        }

        # Act
        result = cached_data._is_expired()

        # Assert
        assert result is True

    def test_is_expired_with_exact_ttl_boundary_returns_false(
        self, cached_data: CachedData[dict[str, str]]
    ) -> None:
        """Test that _is_expired() returns False at exact TTL boundary."""
        # Arrange
        # Mock time.time() for deterministic behavior
        current_time = 1000000.0
        cached_at = current_time - 3600  # Exactly TTL seconds ago
        with patch(
            "cta_eta.data_collection.storage_cache.cache.time.time",
            return_value=current_time,
        ):
            cached_data._memory_cache = {
                "data": {"test": "data"},
                "cached_at": cached_at,
                "ttl": 3600,
            }

            # Act
            result = cached_data._is_expired()

            # Assert
            # Implementation uses > (not >=), so at exact boundary it's not expired
            assert result is False

    def test_is_expired_with_missing_cached_at_returns_true(
        self, cached_data: CachedData[dict[str, str]]
    ) -> None:
        """Test that _is_expired() returns True when cached_at is missing."""
        # Arrange
        cached_data._memory_cache = {"data": {"test": "data"}, "ttl": 3600}

        # Act
        result = cached_data._is_expired()

        # Assert
        assert result is True

    def test_load_from_file_with_missing_file_sets_cache_to_none(
        self, cached_data: CachedData[dict[str, str]]
    ) -> None:
        """Test that _load_from_file() sets cache to None when file doesn't exist."""
        # Arrange
        assert not cached_data._cache_file.exists()

        # Act
        cached_data._load_from_file()

        # Assert
        assert cached_data._memory_cache is None

    def test_load_from_file_with_valid_file_loads_data(
        self, cached_data: CachedData[dict[str, str]], cache_file: Path
    ) -> None:
        """Test that _load_from_file() loads valid JSON from file."""
        # Arrange
        cached_data_obj = {
            "data": {"loaded": "value"},
            "cached_at": time.time(),
            "ttl": 3600,
        }
        with cache_file.open("w") as f:
            json.dump(cached_data_obj, f)

        # Act
        cached_data._load_from_file()

        # Assert
        assert cached_data._memory_cache is not None
        assert cached_data._memory_cache["data"] == {"loaded": "value"}

    def test_load_from_file_with_invalid_json_sets_cache_to_none(
        self, cached_data: CachedData[dict[str, str]], cache_file: Path
    ) -> None:
        """Test that _load_from_file() sets cache to None on JSON decode error."""
        # Arrange
        cache_file.write_text("{ invalid json syntax }")

        # Act
        cached_data._load_from_file()

        # Assert
        assert cached_data._memory_cache is None

    def test_save_to_file_creates_parent_directory(
        self, tmp_path: Path, mock_fetch_fn: Mock
    ) -> None:
        """Test that _save_to_file() creates parent directory if missing."""
        # Arrange
        cache_file = tmp_path / "nested" / "dir" / "cache.json"
        cache = CachedData(cache_file=cache_file, ttl=3600, fetch_fn=mock_fetch_fn)
        cache._memory_cache = {
            "data": {"test": "data"},
            "cached_at": time.time(),
            "ttl": 3600,
        }
        assert not cache_file.parent.exists()

        # Act
        cache._save_to_file()

        # Assert
        assert cache_file.parent.exists()
        assert cache_file.exists()

    def test_save_to_file_writes_valid_json(
        self, cached_data: CachedData[dict[str, str]], cache_file: Path
    ) -> None:
        """Test that _save_to_file() writes valid JSON to file."""
        # Arrange
        cached_data._memory_cache = {
            "data": {"saved": "value"},
            "cached_at": 1234567890.0,
            "ttl": 3600,
        }

        # Act
        cached_data._save_to_file()

        # Assert
        assert cache_file.exists()
        with cache_file.open() as f:
            saved_data = json.load(f)
        assert saved_data["data"] == {"saved": "value"}
        assert saved_data["cached_at"] == 1234567890.0
        assert saved_data["ttl"] == 3600

    def test_save_to_file_handles_write_failure_gracefully(
        self, cached_data: CachedData[dict[str, str]], cache_file: Path
    ) -> None:
        """Test that _save_to_file() handles OSError gracefully without raising."""
        # Arrange
        cached_data._memory_cache = {
            "data": {"test": "data"},
            "cached_at": time.time(),
            "ttl": 3600,
        }

        # Make parent directory read-only to cause write failure
        cache_file.parent.chmod(0o444)

        try:
            # Act (should not raise)
            cached_data._save_to_file()

            # Assert - function completes without exception
            # File may or may not exist depending on when error occurs
        finally:
            # Cleanup
            cache_file.parent.chmod(0o755)

    def test_refresh_calls_fetch_fn(
        self, cached_data: CachedData[dict[str, str]], mock_fetch_fn: Mock
    ) -> None:
        """Test that _refresh() calls fetch_fn to get fresh data."""
        # Arrange
        cached_data._memory_cache = None

        # Act
        cached_data._refresh()

        # Assert
        mock_fetch_fn.assert_called_once()
        assert cached_data._memory_cache is not None
        assert cached_data._memory_cache["data"] == {"test": "data"}

    def test_refresh_updates_cached_at(
        self, cached_data: CachedData[dict[str, str]]
    ) -> None:
        """Test that _refresh() updates cached_at timestamp."""
        # Arrange
        cached_data._memory_cache = None
        before_time = time.time()

        # Act
        cached_data._refresh()

        # Assert
        assert cached_data._memory_cache is not None
        cached_at = cached_data._memory_cache["cached_at"]
        assert cached_at >= before_time
        assert cached_at <= time.time()

    def test_refresh_saves_to_file(
        self, cached_data: CachedData[dict[str, str]], cache_file: Path
    ) -> None:
        """Test that _refresh() saves updated cache to file."""
        # Arrange
        cached_data._memory_cache = None

        # Act
        cached_data._refresh()

        # Assert
        assert cache_file.exists()
        with cache_file.open() as f:
            saved_data = json.load(f)
        assert saved_data["data"] == {"test": "data"}

    def test_refresh_propagates_fetch_fn_exception(self, cache_file: Path) -> None:
        """Test that _refresh() propagates exceptions from fetch_fn."""
        # Arrange
        error = RuntimeError("Network error")
        failing_fetch = Mock(side_effect=error)
        cache = CachedData(cache_file=cache_file, ttl=3600, fetch_fn=failing_fetch)

        # Act & Assert
        with pytest.raises(RuntimeError, match="Network error"):
            cache._refresh()

    def test_get_with_complex_data_types(self, temp_cache_dir: Path) -> None:
        """Test that CachedData works with complex data types."""
        # Arrange
        cache_file = temp_cache_dir / "complex_cache.json"
        complex_data = {
            "stations": [
                {"id": 1, "name": "Station A", "coordinates": [41.8781, -87.6298]},
                {"id": 2, "name": "Station B", "coordinates": [41.8819, -87.6278]},
            ],
            "metadata": {"count": 2, "updated": "2026-01-17"},
        }
        fetch_fn = Mock(return_value=complex_data)
        cache = CachedData(cache_file=cache_file, ttl=3600, fetch_fn=fetch_fn)

        # Act
        result = cache.get()

        # Assert
        assert result == complex_data
        assert cache_file.exists()
        with cache_file.open() as f:
            saved_data = json.load(f)
        assert saved_data["data"] == complex_data

    def test_get_uses_memory_cache_on_second_call(
        self, cached_data: CachedData[dict[str, str]], mock_fetch_fn: Mock
    ) -> None:
        """Test that get() uses memory cache on subsequent calls without file I/O."""
        # Arrange
        cached_data._memory_cache = {
            "data": {"memory": "cached"},
            "cached_at": time.time(),
            "ttl": 3600,
        }

        # Act
        result1 = cached_data.get()
        result2 = cached_data.get()

        # Assert
        assert result1 == {"memory": "cached"}
        assert result2 == {"memory": "cached"}
        mock_fetch_fn.assert_not_called()

    def test_get_with_zero_ttl_always_refreshes(
        self, cache_file: Path, mock_fetch_fn: Mock
    ) -> None:
        """Test that get() always refreshes when TTL is 0."""
        # Arrange
        cache = CachedData(cache_file=cache_file, ttl=0, fetch_fn=mock_fetch_fn)
        cache._memory_cache = {
            "data": {"old": "value"},
            "cached_at": time.time(),
            "ttl": 0,
        }

        # Act
        result = cache.get()

        # Assert
        assert result == {"test": "data"}
        mock_fetch_fn.assert_called_once()

    def test_get_with_very_large_ttl_never_expires(
        self, cache_file: Path, mock_fetch_fn: Mock
    ) -> None:
        """Test that get() doesn't refresh with very large TTL."""
        # Arrange
        cache = CachedData(cache_file=cache_file, ttl=999999999, fetch_fn=mock_fetch_fn)
        old_time = time.time() - 86400  # 1 day ago
        cache._memory_cache = {
            "data": {"old": "value"},
            "cached_at": old_time,
            "ttl": 999999999,
        }

        # Act
        result = cache.get()

        # Assert
        assert result == {"old": "value"}
        mock_fetch_fn.assert_not_called()


class TestCreateCachedData:
    """Test cases for create_cached_data factory function."""

    @pytest.fixture
    def mock_config(self, tmp_path: Path) -> dict[str, dict[str, Any]]:
        """Create mock configuration dict."""
        cache_dir = tmp_path / "cache"
        return {
            "cache": {
                "directory": str(cache_dir),
                "stations_ttl": 604800,
                "track_geometry_ttl": 2592000,
                "weather_mapping_ttl": 604800,
            }
        }

    @pytest.fixture
    def mock_fetch_fn(self) -> Mock:
        """Create mock fetch function."""
        return Mock(return_value={"test": "data"})

    def test_create_cached_data_with_valid_config(
        self, mock_config: dict[str, dict[str, Any]], mock_fetch_fn: Mock
    ) -> None:
        """Test that create_cached_data creates CachedData with valid config."""
        # Arrange
        cache_name = "stations"

        # Act
        cache = create_cached_data(cache_name, mock_config, mock_fetch_fn)

        # Assert
        assert isinstance(cache, CachedData)
        assert cache._ttl == 604800
        assert cache._fetch_fn is mock_fetch_fn
        cache_dir = Path(mock_config["cache"]["directory"])
        assert cache._cache_file == cache_dir / "stations.json"

    def test_create_cached_data_creates_directory(
        self, mock_config: dict[str, dict[str, Any]], mock_fetch_fn: Mock
    ) -> None:
        """Test that create_cached_data creates cache directory if missing."""
        # Arrange
        cache_dir = Path(mock_config["cache"]["directory"])
        if cache_dir.exists():
            cache_dir.rmdir()
        assert not cache_dir.exists()

        # Act
        create_cached_data("stations", mock_config, mock_fetch_fn)

        # Assert
        assert cache_dir.exists()
        assert cache_dir.is_dir()

    def test_create_cached_data_with_existing_directory(
        self, mock_config: dict[str, dict[str, Any]], mock_fetch_fn: Mock
    ) -> None:
        """Test that create_cached_data works with existing directory."""
        # Arrange
        cache_dir = Path(mock_config["cache"]["directory"])
        cache_dir.mkdir(parents=True, exist_ok=True)
        assert cache_dir.exists()

        # Act
        cache = create_cached_data("stations", mock_config, mock_fetch_fn)

        # Assert
        assert isinstance(cache, CachedData)
        assert cache._cache_file.parent == cache_dir

    def test_create_cached_data_with_missing_ttl_raises_value_error(
        self, mock_config: dict[str, dict[str, Any]], mock_fetch_fn: Mock
    ) -> None:
        """Test that create_cached_data raises ValueError when TTL is missing."""
        # Arrange
        cache_name = "unknown_cache"
        assert f"{cache_name}_ttl" not in mock_config["cache"]

        # Act & Assert
        with pytest.raises(ValueError, match="No TTL configured for cache"):
            create_cached_data(cache_name, mock_config, mock_fetch_fn)

    def test_create_cached_data_with_different_cache_names(
        self, mock_config: dict[str, dict[str, Any]], mock_fetch_fn: Mock
    ) -> None:
        """Test that create_cached_data works with different cache names."""
        # Arrange
        cache_names = ["stations", "track_geometry", "weather_mapping"]

        # Act
        caches = [
            create_cached_data(name, mock_config, mock_fetch_fn) for name in cache_names
        ]

        # Assert
        assert len(caches) == 3
        assert caches[0]._ttl == 604800  # stations_ttl
        assert caches[1]._ttl == 2592000  # track_geometry_ttl
        assert caches[2]._ttl == 604800  # weather_mapping_ttl

        cache_dir = Path(mock_config["cache"]["directory"])
        assert caches[0]._cache_file == cache_dir / "stations.json"
        assert caches[1]._cache_file == cache_dir / "track_geometry.json"
        assert caches[2]._cache_file == cache_dir / "weather_mapping.json"

    def test_create_cached_data_with_nested_directory(
        self, tmp_path: Path, mock_fetch_fn: Mock
    ) -> None:
        """Test that create_cached_data creates nested cache directory."""
        # Arrange
        nested_dir = tmp_path / "nested" / "cache" / "dir"
        config = {
            "cache": {
                "directory": str(nested_dir),
                "test_ttl": 3600,
            }
        }
        assert not nested_dir.exists()

        # Act
        cache = create_cached_data("test", config, mock_fetch_fn)

        # Assert
        assert nested_dir.exists()
        assert cache._cache_file.parent == nested_dir

    def test_create_cached_data_propagates_directory_creation_error(
        self, tmp_path: Path, mock_fetch_fn: Mock
    ) -> None:
        """Test that create_cached_data propagates OSError from directory creation."""
        # Arrange
        # Create a file with the same name as the directory we want to create
        file_path = tmp_path / "cache"
        file_path.write_text("not a directory")
        config = {
            "cache": {
                "directory": str(file_path / "subdir"),
                "test_ttl": 3600,
            }
        }

        # Act & Assert
        # mkdir raises NotADirectoryError when parent is a file
        with pytest.raises((OSError, NotADirectoryError)):
            create_cached_data("test", config, mock_fetch_fn)

    def test_create_cached_data_with_string_path(
        self, mock_config: dict[str, dict[str, Any]], mock_fetch_fn: Mock
    ) -> None:
        """Test that create_cached_data handles string directory path."""
        # Arrange
        cache_name = "stations"

        # Act
        cache = create_cached_data(cache_name, mock_config, mock_fetch_fn)

        # Assert
        assert isinstance(cache._cache_file, Path)
        assert cache._cache_file.name == "stations.json"

    def test_create_cached_data_with_int_ttl(
        self, mock_config: dict[str, dict[str, Any]], mock_fetch_fn: Mock
    ) -> None:
        """Test that create_cached_data converts TTL to int."""
        # Arrange
        config = {
            "cache": {
                "directory": str(Path(mock_config["cache"]["directory"])),
                "test_ttl": 3600.0,  # Float value
            }
        }

        # Act
        cache = create_cached_data("test", config, mock_fetch_fn)

        # Assert
        assert cache._ttl == 3600
        assert isinstance(cache._ttl, int)
