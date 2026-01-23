"""Unit tests for weather grid mapping cache wrappers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from cta_eta.data_collection.storage_cache.weather_grid_cache import (
    NWSGridCache,
    WeatherGridCache,
    get_nws_grid_cache,
    get_open_meteo_grid_cache,
    get_openweathermap_grid_cache,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestWeatherGridCache:
    """Test cases for provider-agnostic WeatherGridCache."""

    @pytest.fixture
    def cache_file(self, tmp_path: Path) -> Path:
        """Create cache file path for testing."""
        return tmp_path / "weather_grid_mapping.json"

    @pytest.fixture
    def cache(self, cache_file: Path) -> WeatherGridCache:
        """Create a WeatherGridCache instance."""
        return WeatherGridCache(cache_file=cache_file, ttl=60)

    def test_get_on_miss_returns_none_and_does_not_create_file(
        self, cache: WeatherGridCache, cache_file: Path
    ) -> None:
        """Test cache miss behavior remains side-effect free."""
        # Arrange
        station_id = "station_1"
        assert not cache_file.exists()

        # Act
        grid_id = cache.get_grid_identifier(station_id)

        # Assert
        assert grid_id is None
        assert not cache_file.exists()

    def test_set_then_get_then_delete_round_trip(self, cache: WeatherGridCache) -> None:
        """Test set/get/delete behavior for a station mapping."""
        # Arrange
        station_id = "station_123"
        expected_grid = "41.88,-87.63"

        # Act
        cache.set_grid_identifier(station_id, expected_grid)
        got = cache.get_grid_identifier(station_id)
        cache.delete_station(station_id)
        after_delete = cache.get_grid_identifier(station_id)

        # Assert
        assert got == expected_grid
        assert after_delete is None

    def test_get_expired_entry_returns_none(
        self, mocker: pytest.MockFixture, cache_file: Path
    ) -> None:
        """Test TTL expiry path is surfaced through WeatherGridCache."""
        # Arrange
        cache = WeatherGridCache(cache_file=cache_file, ttl=10)
        station_id = "station_expired"
        mocker.patch(
            "cta_eta.data_collection.storage_cache.kv_cache.time.time",
            side_effect=[100.0, 100.0],
        )
        cache.set_grid_identifier(station_id, "LOT/85,67")

        # Act
        # KV cache uses time.time() for TTL; force expiry deterministically.
        mocker.patch(
            "cta_eta.data_collection.storage_cache.kv_cache.time.time",
            return_value=111.0,
        )
        expired = cache.get_grid_identifier(station_id)

        # Assert
        assert expired is None


@pytest.mark.asyncio
class TestNWSGridCache:
    """Test cases for NWS-specific cache with lazy grid discovery."""

    @pytest.fixture
    def cache_file(self, tmp_path: Path) -> Path:
        """Create cache file path for testing."""
        return tmp_path / "nws_grid_mapping.json"

    @pytest.fixture
    def cache(self, cache_file: Path) -> NWSGridCache:
        """Create an NWSGridCache instance."""
        return NWSGridCache(cache_file=cache_file, ttl=60)

    async def test_resolve_returns_cached_grid_without_discovery(
        self, mocker: pytest.MockFixture, cache: NWSGridCache
    ) -> None:
        """Test that a cache hit avoids NWS discovery calls."""
        # Arrange
        station_id = "station_cached"
        cache.set_grid_identifier(station_id, "LOT/1,2")
        discover_spy = mocker.patch.object(cache, "_discover_grid")

        # Act
        grid_id = await cache.resolve_grid_identifier(
            station_id, latitude=41.88, longitude=-87.63
        )

        # Assert
        assert grid_id == "LOT/1,2"
        discover_spy.assert_not_awaited()

    async def test_resolve_on_miss_discovers_and_persists_mapping(
        self, mocker: pytest.MockFixture, cache: NWSGridCache, cache_file: Path
    ) -> None:
        """Test discovery path stores the discovered grid and reuses it."""
        # Arrange
        station_id = "station_miss"
        lat = 41.88
        lon = -87.63
        discovered = "LOT/85,67"
        discover = mocker.patch(
            "cta_eta.data_collection.storage_cache.weather_grid_cache.discover_nws_grid",
            new=mocker.AsyncMock(return_value=discovered),
        )

        # Act
        first = await cache.resolve_grid_identifier(
            station_id, latitude=lat, longitude=lon
        )
        second = await cache.resolve_grid_identifier(
            station_id, latitude=lat, longitude=lon
        )

        # Assert
        assert first == discovered
        assert second == discovered
        discover.assert_awaited_once()
        assert cache_file.exists()
        payload = json.loads(cache_file.read_text())
        assert payload["data"][station_id]["value"] == discovered

    async def test_resolve_propagates_http_status_error_and_does_not_cache(
        self, mocker: pytest.MockFixture, cache: NWSGridCache, cache_file: Path
    ) -> None:
        """Test failures do not poison the cache and exceptions bubble up."""
        # Arrange
        station_id = "station_error"
        request = httpx.Request("GET", "https://api.weather.gov/points/0,0")
        response = httpx.Response(status_code=503, request=request)
        err = httpx.HTTPStatusError(
            "upstream error", request=request, response=response
        )
        mocker.patch.object(cache, "_discover_grid", side_effect=err)
        set_spy = mocker.spy(cache._cache, "set")

        # Act & Assert
        with pytest.raises(httpx.HTTPStatusError, match="upstream error"):
            await cache.resolve_grid_identifier(
                station_id, latitude=41.88, longitude=-87.63
            )

        assert not cache_file.exists()
        set_spy.assert_not_called()

    async def test_aclose_closes_http_client(
        self, mocker: pytest.MockFixture, cache: NWSGridCache
    ) -> None:
        """Test aclose closes the underlying httpx async client."""
        # Arrange
        await cache._ensure_client()
        assert cache._client is not None
        close_spy = mocker.spy(cache._client, "aclose")

        # Act
        await cache.aclose()

        # Assert
        close_spy.assert_awaited_once()
        assert cache._client is None


class TestWeatherGridCacheFactories:
    """Test cases for provider cache factory helpers."""

    @pytest.fixture
    def config(self, tmp_path: Path) -> dict:
        """Create a minimal cache config dict."""
        return {
            "cache": {
                "directory": str(tmp_path / "cache"),
                "weather_mapping_ttl": "123",
            }
        }

    def test_get_nws_grid_cache_builds_expected_instance(self, config: dict) -> None:
        """Test NWS cache factory returns correct type and config."""
        # Arrange
        expected_name = "nws_grid_mapping.json"

        # Act
        cache = get_nws_grid_cache(config)

        # Assert
        assert isinstance(cache, NWSGridCache)
        assert cache._ttl == 123
        assert cache._cache._cache_file.name == expected_name

    def test_get_open_meteo_grid_cache_builds_expected_instance(
        self, config: dict
    ) -> None:
        """Test Open-Meteo cache factory returns expected cache."""
        # Arrange
        expected_name = "open_meteo_grid_mapping.json"

        # Act
        cache = get_open_meteo_grid_cache(config)

        # Assert
        assert isinstance(cache, WeatherGridCache)
        assert cache._ttl == 123
        assert cache._cache._cache_file.name == expected_name

    def test_get_openweathermap_grid_cache_builds_expected_instance(
        self, config: dict
    ) -> None:
        """Test OpenWeatherMap cache factory returns expected cache."""
        # Arrange
        expected_name = "openweathermap_grid_mapping.json"

        # Act
        cache = get_openweathermap_grid_cache(config)

        # Assert
        assert isinstance(cache, WeatherGridCache)
        assert cache._ttl == 123
        assert cache._cache._cache_file.name == expected_name
