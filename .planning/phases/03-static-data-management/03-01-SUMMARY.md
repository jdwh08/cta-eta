---
phase: 03-static-data-management
plan: 01
subsystem: cache
tags: [ttl-cache, json, file-persistence, generics]

# Dependency graph
requires:
  - phase: 02-storage-abstraction
    provides: Storage backend patterns (ABC base classes, factory functions)
  - phase: 01-foundation-configuration
    provides: Config system (load_config, TOML/env hybrid)
provides:
  - Generic CachedData[T] class with TTL and file persistence
  - Factory function for config-driven cache instantiation
  - Cache directory structure (.cache/)
affects: [04-train-position-polling, 05-multi-source-weather-collection]

# Tech tracking
tech-stack:
  added: []
  patterns: [generic-classes, lazy-refresh, json-persistence]

key-files:
  created: [src/cta_eta/cache.py]
  modified: [config.toml]

key-decisions:
  - "JSON file format for cache persistence (simple, human-readable, works with stdlib)"
  - "Lazy refresh pattern (check TTL on access, not proactive background refresh)"
  - "Non-thread-safe initially (single-threaded daemon assumption, locks can be added later)"
  - "Cache directory .cache/ separate from data/ (operational state vs collected data)"

patterns-established:
  - "Generic type parameters using Python 3.13+ syntax (CachedData[T])"
  - "Factory functions take config dict and return configured instances"
  - "File persistence with graceful degradation (missing/corrupt cache triggers refresh)"

issues-created: []

# Metrics
duration: 1min
completed: 2026-01-18
---

# Phase 3 Plan 1: Cache Infrastructure Summary

**Generic TTL cache with file persistence, lazy refresh, and config-driven factory for static CTA data**

## Performance

- **Duration:** 1 min
- **Started:** 2026-01-18T01:47:14Z
- **Completed:** 2026-01-18T01:48:24Z
- **Tasks:** 2 (Task 3 delivered in Task 1)
- **Files modified:** 2

## Accomplishments

- Generic `CachedData[T]` class with type-safe caching for any data type
- TTL-based lazy refresh (checks expiration on access, not background polling)
- JSON file persistence survives daemon restarts without data loss
- Config-driven factory function `create_cached_data` integrates with Phase 1 config system
- Cache directory `.cache/` created automatically, separate from collected data storage
- 0 basedpyright errors, fully type-safe with modern Python 3.13+ generics

## Task Commits

Each task was committed atomically:

1. **Task 1: Create generic CachedData class** - `f1b2185` (feat)
   - Generic CachedData[T] with Python 3.13+ syntax
   - Lazy TTL refresh pattern (check on access, refresh when expired)
   - JSON file persistence for daemon restart continuity
   - Factory function create_cached_data for config-driven instantiation

2. **Task 2: Add cache configuration** - `a0d9381` (feat)
   - [cache] section with directory and TTL values
   - stations_ttl: 604800 seconds (7 days)
   - track_geometry_ttl: 2592000 seconds (30 days)
   - weather_mapping_ttl: 604800 seconds (7 days)

**Plan metadata:** (will be committed after SUMMARY creation)

## Files Created/Modified

- `src/cta_eta/cache.py` - CachedData[T] class with lazy TTL refresh, JSON file persistence, factory function
- `config.toml` - [cache] section with directory path and TTL configuration for stations/geometry/weather mappings

## Decisions Made

**JSON file format for persistence:**
- Rationale: Standard library support, human-readable for debugging, simple serialization
- Alternative considered: pickle (rejected: not human-readable, version compatibility issues)

**Lazy refresh pattern:**
- Rationale: Simpler than background polling, refresh only when needed, no idle CPU usage
- Check TTL on `get()` access, refresh if expired
- Fresh data guaranteed without proactive refresh threads

**Non-thread-safe initially:**
- Rationale: Current daemon design is single-threaded (sequential polling)
- Threading locks can be added later if needed (simple addition to `get()` method)
- Avoids premature complexity

**Cache directory separate from data:**
- `.cache/` for operational state (stations, geometry, mappings)
- `data/` for collected Parquet files (train positions, weather)
- Logical separation: cache is replaceable, collected data is valuable

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Task 3 functionality delivered in Task 1**
- **Found during:** Task 1 implementation
- **Issue:** Factory function `create_cached_data` is logically part of cache module, natural to implement alongside CachedData class
- **Fix:** Implemented factory function in Task 1 commit instead of separate Task 3
- **Files modified:** src/cta_eta/cache.py (already in Task 1)
- **Verification:** Factory function works correctly with config integration
- **Committed in:** f1b2185 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (blocking - logical code organization)
**Impact on plan:** No scope change, just combined related functionality into single commit for cleaner implementation

## Issues Encountered

None - implementation proceeded smoothly with established patterns from Phase 1/2

## Next Phase Readiness

- Cache infrastructure complete and ready for use
- Phase 3 Plan 2 can now implement weather grid mapping with CachedData
- Phase 4 train polling can cache station lists using this infrastructure
- No blockers or concerns

---
*Phase: 03-static-data-management*
*Completed: 2026-01-18*
