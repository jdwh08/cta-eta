---
phase: 02-storage-abstraction
plan: 01
subsystem: storage
tags: [parquet, fsspec, s3, gcs, pyarrow, timezone-aware]

# Dependency graph
requires:
  - phase: 01-foundation-configuration
    provides: hybrid config system (config.toml + .env), structured logging
provides:
  - Cloud-agnostic storage abstraction (StorageBackend ABC)
  - Local and cloud storage backends (LocalStorage, CloudStorage via fsspec)
  - Parquet writer with timezone-aware partitioning (ParquetWriter)
  - Factory functions for config-driven backend selection
affects: [03-static-data-management, 04-train-position-polling, 05-weather-collection]

# Tech tracking
tech-stack:
  added: [pyarrow, fsspec, s3fs, gcsfs]
  patterns: [ABC storage abstraction, factory pattern for backends, timezone-aware partitioning]

key-files:
  created: [src/cta_eta/storage.py]
  modified: [config.toml]

key-decisions:
  - "fsspec for unified S3/GCS API instead of separate boto3/google-cloud-storage clients"
  - "Hive-style partitioning (date=YYYY-MM-DD/) for analytical tool compatibility"
  - "3 AM America/Chicago partition split to minimize splitting active train runs"
  - "Timestamp suffixes on Parquet filenames to support multiple writes per partition"
  - "Environment variable credentials (AWS_*, GOOGLE_APPLICATION_CREDENTIALS) over config file"

patterns-established:
  - "ABC base classes with abstractmethod for swappable implementations"
  - "Factory functions taking config dict to instantiate backends"
  - "ZoneInfo (stdlib) for timezone handling instead of pytz"
  - "BytesIO buffer pattern for in-memory Parquet serialization"

issues-created: []

# Metrics
duration: 4min
completed: 2026-01-17
---

# Phase 2 Plan 1: Storage Abstraction Layer Summary

**Cloud-agnostic Parquet storage with fsspec-based S3/GCS backends and timezone-aware daily partitioning split at 3 AM Chicago time**

## Performance

- **Duration:** 4 min
- **Started:** 2026-01-18T01:01:14Z
- **Completed:** 2026-01-18T01:05:18Z
- **Tasks:** 3
- **Files modified:** 2

## Accomplishments

- Cloud-agnostic storage abstraction supporting local filesystem, AWS S3, and Google Cloud Storage via unified interface
- Timezone-aware daily partitioning using 3 AM America/Chicago split to minimize disrupting active train runs
- Configuration-driven backend selection enabling zero-code-change migration from local to cloud
- Hive-style Parquet partitioning (date=YYYY-MM-DD/) for compatibility with analytical tools

## Task Commits

Each task was committed atomically:

1. **Task 1: Create storage abstraction with local and cloud backends** - `62599a3` (feat)
2. **Task 2: Implement Parquet writer with timezone-aware partitioning** - `d8f36b7` (feat)
3. **Task 3: Integrate storage backend selection with config system** - `0effdcb` (feat)

**Plan metadata:** N/A (.planning directory is gitignored)

## Files Created/Modified

- `src/cta_eta/storage.py` - Storage abstraction layer with StorageBackend ABC, LocalStorage/CloudStorage implementations, ParquetWriter with timezone-aware partitioning, and factory functions
- `config.toml` - Added [storage] backend selection (local/s3/gcs), cloud bucket fields, partition_hour configuration

## Decisions Made

**fsspec for cloud abstraction:**
- Chose fsspec over separate boto3 (S3) and google-cloud-storage (GCS) libraries
- Rationale: Unified API reduces code duplication, built-in retry mechanisms, simpler dependency management

**3 AM partition split:**
- Daily partitions split at 3:00 AM America/Chicago time
- Rationale: Low ridership period minimizes splitting active train runs across partition boundaries, improving temporal continuity for model training

**Environment variable credentials:**
- Cloud credentials from AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY (S3) and GOOGLE_APPLICATION_CREDENTIALS (GCS)
- Rationale: Industry standard, avoids committing secrets, compatible with cloud deployment environments

**Timestamp-suffixed filenames:**
- Parquet files named data_{timestamp}.parquet within partitions
- Rationale: Supports multiple writes per partition without overwrites, preserves all collection cycles

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## Next Phase Readiness

Storage abstraction complete and verified:
- Type checking passes (basedpyright 0 errors)
- Imports successful from cta_eta.storage
- Factory functions create configured ParquetWriter
- Sample data writes to correct Hive-style partitions
- Partition split verified (2:59 AM → previous day, 3:00 AM → current day)

Ready for Phase 3 (Static Data Management) - cache infrastructure can now use storage backends to persist TTL data.

---
*Phase: 02-storage-abstraction*
*Completed: 2026-01-17*
