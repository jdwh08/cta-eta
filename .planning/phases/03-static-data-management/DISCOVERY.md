# Phase 3 Discovery: Static Data Management (TTL Cache Infrastructure)

**Discovery Level:** Standard (15-30 min)
**Date:** 2026-01-17

## Questions to Answer

1. **What TTL cache patterns exist in Python?**
   - In-memory dictionaries with expiry timestamps
   - Dedicated libraries (cachetools, dogpile.cache, etc.)
   - File-based caches with metadata
   - Database-backed caches (SQLite, Redis)

2. **What refresh strategies are appropriate?**
   - Lazy refresh: Check TTL on access, refresh if expired
   - Eager refresh: Background thread/task refreshes before expiry
   - Hybrid: Lazy refresh with background pre-warming

3. **What needs to be cached?**
   - CTA stations list (~300 stations) - from Chicago Data Portal API
   - Track geometry (segments with coordinates) - from Chicago Data Portal API
   - Station-to-weather grid mappings (~300 stations → ~50 weather points)

4. **What are the update frequencies?**
   - Stations: Very low (months/years between changes)
   - Track geometry: Very low (changes only with construction)
   - Station-to-weather mappings: Derived from stations, same low frequency

5. **What are the performance requirements?**
   - Fast lookups during train position polling (~15s intervals)
   - Minimal API calls to preserve rate limits
   - Persistence across daemon restarts

## Research Findings

### Python TTL Cache Options

**Option 1: Manual in-memory dictionary with timestamps**
- Pros: Simple, no dependencies, full control
- Cons: Manual expiry checking, no persistence, more code to maintain
- Pattern:
  ```python
  cache = {"data": data, "expires_at": time.time() + ttl}
  if time.time() > cache["expires_at"]: refresh()
  ```

**Option 2: cachetools library**
- Library: https://github.com/tkem/cachetools
- Pros: TTL cache built-in (`TTLCache`), LRU/LFU variants, well-tested
- Cons: In-memory only (no persistence), another dependency
- Pattern:
  ```python
  from cachetools import TTLCache
  cache = TTLCache(maxsize=100, ttl=3600)
  cache["key"] = value  # Auto-expires after 1 hour
  ```

**Option 3: File-based cache with metadata**
- Pros: Persists across restarts, integrates with storage abstraction from Phase 2
- Cons: Slower than in-memory, need manual TTL checking, file I/O overhead
- Pattern:
  ```python
  # Save: {"data": [...], "cached_at": timestamp}
  # Load: Check if time.time() - cached_at > ttl
  ```

**Option 4: Custom hybrid approach**
- Pros: Best of both worlds - in-memory speed + file persistence, matches project patterns (ABC classes)
- Cons: More code to write
- Pattern:
  ```python
  class CachedData:
      _memory: dict | None  # Fast access
      _file_path: Path      # Persistence
      _ttl: int             # Seconds

      def get() -> dict:
          if _memory is None or _is_expired():
              _refresh_from_file_or_api()
          return _memory
  ```

### Refresh Strategy Recommendations

**For this use case (low-frequency static data):**
- **Lazy refresh** is sufficient
  - Check on first access after daemon start
  - Check TTL on each subsequent access
  - Refresh only when expired
- **No background refresh needed**
  - Data changes infrequently (months)
  - Polling daemons can tolerate brief refresh delays
  - Simpler implementation

### Cache Persistence Strategy

**Recommendation: File-based with in-memory caching**
- Store cached data in `.cache/` directory (JSON format)
- Metadata includes: `cached_at` timestamp, `ttl` seconds, `source_url` for refresh
- In-memory copy for fast repeated access
- Check TTL on each access, refresh from file or API as needed

**Integration with Phase 2 storage:**
- Could use `storage.py` backends for cache persistence
- BUT: Simpler to use local JSON files (caches are always local, not cloud-stored)
- Cache is operational state, not collected data

### Data-Specific TTL Recommendations

Based on update frequencies:

| Data Type | Suggested TTL | Rationale |
|-----------|---------------|-----------|
| CTA Stations | 7 days | Stations rarely change, weekly refresh is safe |
| Track Geometry | 30 days | Track shape only changes during construction |
| Station→Weather Mapping | 7 days | Derived from stations, same low frequency |

### Cache File Structure

```
.cache/
├── stations.json          # {"data": [...], "cached_at": 1234567890, "ttl": 604800}
├── track_geometry.json    # {"data": {...}, "cached_at": 1234567890, "ttl": 2592000}
└── weather_mapping.json   # {"data": {...}, "cached_at": 1234567890, "ttl": 604800}
```

## Decision: Custom File-Backed TTL Cache

**Chosen approach:** Custom `CachedData` class with lazy refresh and file persistence

**Rationale:**
1. Matches project patterns (ABC classes, factory functions from Phases 1-2)
2. Persistence across daemon restarts (critical for 24/7 operation)
3. No new dependencies (use stdlib json, pathlib, time)
4. Simple lazy refresh fits low-frequency static data
5. Type-safe with modern Python 3.13+ syntax

**Implementation sketch:**
```python
class CachedData:
    """File-backed TTL cache for static data with in-memory caching."""

    def __init__(self, cache_file: Path, ttl: int, fetch_fn: Callable[[], Any]):
        self._cache_file = cache_file
        self._ttl = ttl
        self._fetch_fn = fetch_fn
        self._memory_cache: dict[str, Any] | None = None

    def get(self) -> Any:
        """Get cached data, refreshing if expired."""
        if self._memory_cache is None:
            self._load_from_file()

        if self._is_expired():
            self._refresh()

        return self._memory_cache["data"]

    def _is_expired(self) -> bool:
        """Check if cache has expired."""
        if self._memory_cache is None:
            return True
        cached_at = self._memory_cache.get("cached_at", 0)
        return time.time() - cached_at > self._ttl

    def _refresh(self) -> None:
        """Fetch fresh data and update cache."""
        data = self._fetch_fn()
        self._memory_cache = {
            "data": data,
            "cached_at": time.time(),
            "ttl": self._ttl
        }
        self._save_to_file()
```

**Station-to-weather mapping:**
- Not from API - computed from stations list
- Map each station to nearest weather grid point
- Cache the mapping dict: `{station_id: (lat, lon)}`
- Grid points: ~50 locations covering Chicago area (0.1° grid ~= 11km spacing)

## Next Steps for Planning

1. Create `CachedData` class in `src/cta_eta/cache.py`
2. Integrate with existing API functions (`api_stations_weather.py`, `api_cta_track_shape.py`)
3. Create weather grid generation function (derive ~50 grid points from station coverage)
4. Add cache configuration to `config.toml` (cache directory, TTL values)
5. Update daemon framework to initialize caches on startup

---
*Discovery completed: 2026-01-17*
