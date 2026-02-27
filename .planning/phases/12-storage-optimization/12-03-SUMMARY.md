---
phase: 12-storage-optimization
plan: "03"
subsystem: monitoring
tags: [pyarrow, parquet, argparse, schema-registry, cli, schema-drift]

# Dependency graph
requires:
  - phase: 12-02
    provides: schema_registry module with save_registry, Parquet drift annotation (schema_drift=true metadata)
  - phase: 12-01
    provides: schema registry JSON format and _REGISTRY_DIR constant in compact.py
provides:
  - _read_schema_drift() helper in cli.py reading schema_drift Parquet footer metadata
  - Schema column in cta-monitor compaction table (OK / DRIFT / ?)
  - cta-compact schema update subcommand promoting observed Parquet schema to registry with git commit
  - Nested subparser structure in cta-compact (run / schema update) with backward-compatible default
affects:
  - operator runbooks (reprocess invocation changed to 'cta-compact run --reprocess DATE')
  - systemd service (unaffected — no-arg invocation still runs compaction)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - module-level helper function (_read_schema_drift) placed outside cmd_compaction for testability
    - nested argparse subparsers with default fallback for backward-compatible systemd invocation
    - noqa suppression scoping — S603 on subprocess.run() call, S607 on the list argument line

key-files:
  created: []
  modified:
    - src/cta_eta/monitoring/cli.py
    - src/cta_eta/data_collection/compaction/compact.py
    - tests/monitoring/test_cli.py
    - tests/data_collection/compaction/test_compact.py

key-decisions:
  - "cta-compact --reprocess DATE no longer works; replaced by 'cta-compact run --reprocess DATE' (intentional breaking change documented in plan)"
  - "Schema column reads pq.read_metadata() from local staging Parquet; ? returned for missing/unreadable files (graceful degradation)"
  - "subprocess noqa suppressions scoped per-line: S603 on subprocess.run() call, S607 on the partial-path list argument"
  - "TestMainReprocessFlag and TestMainTargetDate updated to use new 'run' subcommand form (pre-existing tests broken by intentional CLI change)"

patterns-established:
  - "noqa S603/S607: S603 goes on subprocess.run() line, S607 goes on the list-argument line — they trigger independently"
  - "Operator CLI print statements suppressed with per-line # noqa: T201 rather than file-level noqa to limit suppression scope"

requirements-completed: []

# Metrics
duration: 8min
completed: 2026-02-27
---

# Phase 12 Plan 03: Schema Drift Surface Summary

**Schema column in cta-monitor compaction (OK/DRIFT/?) reading Parquet footer metadata, plus cta-compact schema update subcommand with git-commit-or-fallback pattern**

## Performance

- **Duration:** 8 min
- **Started:** 2026-02-27T01:19:08Z
- **Completed:** 2026-02-27T01:27:02Z
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments

- Added `_read_schema_drift()` module-level helper to `cli.py` that reads `schema_drift` from Parquet footer metadata and returns `DRIFT`, `OK`, or `?` for missing files
- Extended `cmd_compaction` table with a Schema column (width 98 chars), computed per record from the local staging Parquet
- Added `cta-compact schema update [--daemon]` nested subcommand that reads the latest Parquet schema, overwrites the registry file, and attempts a git commit with fallback to print-path message
- Restructured `cta-compact` main() with `run` / `schema` subparsers while preserving backward compatibility for the systemd service (no-arg invocation still runs compaction)
- Added 7 new tests: `TestCompactionSchemaColumn` (OK/DRIFT/? cases) and `TestSchemaUpdateCommand` (registry write, git commit, fallback, no-parquet)

## Task Commits

Each task was committed atomically:

1. **Task 1: Schema column in cta-monitor compaction view** - `555f73a` (feat)
2. **Task 2: cta-compact schema update subcommand** - `cbd149c` (feat)
3. **Task 3: Tests for schema column and schema update command** - `52f730a` (test)

**Plan metadata:** _(docs commit follows)_

## Files Created/Modified

- `src/cta_eta/monitoring/cli.py` - Added `_read_schema_drift()` and Schema column in `cmd_compaction` table
- `src/cta_eta/data_collection/compaction/compact.py` - Added `cmd_schema_update()`, restructured `main()` with nested subparsers, moved `subprocess` import to top-level
- `tests/monitoring/test_cli.py` - Added `TestCompactionSchemaColumn` (3 tests) and `TestSchemaUpdateCommand` (4 tests)
- `tests/data_collection/compaction/test_compact.py` - Updated 2 tests to use new `run --reprocess DATE` form

## Decisions Made

- `cta-compact --reprocess DATE` no longer works; replaced by `cta-compact run --reprocess DATE`. This is an intentional breaking change — the systemd service is unaffected (uses no-arg form), only manual operator reprocessing changes.
- Schema column reads `pq.read_metadata()` from the local staging Parquet path; returns `?` for missing or unreadable files (graceful degradation — no crash if Parquet absent).
- `subprocess` noqa suppressions are scoped per-line: S603 on the `subprocess.run()` call line, S607 on the list-argument line — they trigger at different AST positions.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated broken test_compact.py tests for intentional CLI change**
- **Found during:** Task 3 (full suite run after adding new tests)
- **Issue:** `TestMainReprocessFlag.test_reprocess_flag_sets_reprocess_true` and `TestMainTargetDate.test_reprocess_arg_sets_target_date` used old `--reprocess DATE` top-level flag, which was intentionally removed in Task 2
- **Fix:** Updated both tests to use new `run --reprocess DATE` subcommand form, matching the documented breaking change
- **Files modified:** `tests/data_collection/compaction/test_compact.py`
- **Verification:** All 946 tests pass with no regressions
- **Committed in:** `52f730a` (Task 3 commit)

**2. [Rule 1 - Bug] Fixed ruff linting in cmd_schema_update**
- **Found during:** Task 2 (IDE diagnostic feedback)
- **Issue:** `import subprocess` placed inside function body (PLC0415), `print` statements missing noqa suppression (T201), f-string without placeholder (F541), noqa directives on wrong lines for S603/S607
- **Fix:** Moved import to top-level, added per-line `# noqa: T201` on print statements, fixed f-string, corrected noqa placement (S603 on run() line, S607 on list argument line)
- **Files modified:** `src/cta_eta/data_collection/compaction/compact.py`
- **Verification:** `uv run ruff check` shows only pre-existing errors on `_compact_one_daemon` (out of scope)
- **Committed in:** `cbd149c` (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (both Rule 1 - Bug)
**Impact on plan:** Both fixes necessary for correctness and code quality. No scope creep.

## Issues Encountered

None — ruff noqa placement required iterative refinement (S603 vs S607 trigger on different AST nodes) but resolved within Task 2.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 12 complete: schema registry with drift detection, Parquet annotation, operator visibility via cta-monitor, and schema promotion via cta-compact schema update
- Operator workflow loop closed: drift visible in monitoring dashboard, resolvable with single command
- Ready to start production data collection (run `cta-train-daemon.service` and `cta-weather-daemon.service`)

---
*Phase: 12-storage-optimization*
*Completed: 2026-02-27*
