# Phase 3: Static Data Management - Context

**Gathered:** 2026-01-18
**Status:** Ready for planning

<vision>
## How This Should Work

The caching system should be "set it and forget it" - when the daemon starts or needs static data, it just works. Caches refresh automatically based on TTL without me thinking about them. However, if I know something changed (like a new CTA station opened), I can manually force a refresh by simply deleting the cache file - the system will automatically fetch fresh data on the next access.

The weather grid mapping is the critical piece: instead of pre-computing grid points, the system should **learn the grid mappings from the APIs themselves**. When the weather poller needs data for a station, it looks up that station's cached grid mapping. If there's no mapping yet (cache empty or TTL expired), it queries the API with the station's coordinates, and the API naturally "snaps" to its own grid system - NWS returns gridpoints like "LOT/85,67", Open-Meteo returns rounded coordinates. The system caches these API-specific grid identifiers so future requests for that station can go directly to the right grid point.

This lazy discovery approach with TTL refresh is the best practice - no expensive startup, but the cache naturally refreshes all mappings when TTL expires, acting like a periodic "refresh all" mechanism.

</vision>

<essential>
## What Must Be Nailed

All three aspects are equally critical:

- **Zero API waste** - Never fetch static data more than necessary. The cache must effectively eliminate redundant API calls for rarely-changing data, preserving rate limits for the important polling operations.

- **Daemon reliability** - Caches must survive restarts and always work. When the daemon restarts at 3am or after a crash, cached data must be immediately available without delays or failures. Persistence is critical.

- **Speed** - Lookups must be instant during polling cycles. Train position polling happens every ~15 seconds. Cache lookups must be fast enough to not slow down the critical data collection loop.

</essential>

<boundaries>
## What's Out of Scope

- Cache invalidation strategies beyond simple TTL - no smart invalidation, cache warming, or complex eviction policies. Just time-based expiry.

- Caching time-series data (train positions, weather observations) - only static/reference data gets cached. The actual collected data goes through the storage abstraction from Phase 2.

- Distributed caching or multi-server coordination - single-process local cache only. No Redis, no shared cache across multiple daemon instances.

- Performance optimization and benchmarking - don't need to measure cache hit rates or optimize for microsecond lookups. Good enough performance is sufficient.

</boundaries>

<specifics>
## Specific Ideas

**Type-safe and well-structured** - Follow the existing codebase patterns established in Phases 1-2. Use ABC classes, factory functions, type hints. Clean, maintainable, Pythonic code.

**Error handling approach:**
- When cache refresh fails (API down, network issue), retry with exponential backoff (like the stamina decorator)
- After retries are exhausted, serve stale cache data temporarily and schedule another refresh attempt in the background
- Keep trying periodically until the API recovers - better to serve slightly outdated static data than fail completely

**Corrupted cache handling:**
- If a cache file is corrupted or has invalid JSON, treat it the same as initial fetch failure
- Delete the bad file and go through the normal fetch-with-retry flow
- Don't silently ignore corruption - log it, but handle it gracefully

**Weather grid mapping specifics:**
- Store separate cache files: `nws_grid_mapping.json` and `open_meteo_grid_mapping.json`
- Each maps `station_id → API-specific grid identifier` (e.g., '40101' → 'LOT/85,67' for NWS, or '40101' → (41.9, -87.6) for Open-Meteo)
- Lazy population: discover grid mappings as stations are encountered during polling
- TTL applies to grid mappings too - when expired, re-query APIs to refresh the mapping (handles cases where APIs change their grid systems)

**Basic infrastructure already exists:**
- `src/cta_eta/cache.py` has the `CachedData[T]` generic class with TTL and file persistence
- Focus Phase 3 work on the **integration points**: weather grid discovery, API-specific grid mapping caches, and integrating with the poller daemons

</specifics>

<notes>
## Additional Context

The user emphasized that letting APIs naturally determine their own grid systems is better than pre-computing grid points ourselves. This leverages the APIs' native grid logic and ensures we're always aligned with how they actually store and serve weather data.

The lazy population approach with TTL refresh is validated as best practice for senior software/data engineers working with API caching - it balances startup performance, cache freshness, and operational simplicity.

Manual override is kept simple: just delete the cache file to force refresh. No special commands needed.

</notes>

---

*Phase: 03-static-data-management*
*Context gathered: 2026-01-18*
