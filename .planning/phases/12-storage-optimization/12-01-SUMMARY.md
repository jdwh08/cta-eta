---
phase: 12-storage-optimization
plan: "01"
subsystem: compaction
tags: [pyarrow, schema-registry, drift-detection, ipc, json, base64, tdd]

# Dependency graph
requires:
  - phase: 11-data-compaction
    provides: compaction pipeline (compact.py, ipc_reader.py) that schema_registry integrates with

provides:
  - DriftResult dataclass with BreakingFieldChange, AddedField, NullabilityChange sub-types
  - classify_drift() with WIDENING_PAIRS frozenset for safe numeric widening detection
  - schema_to_registry_dict() / registry_dict_to_schema() JSON+base64-IPC round-trip serialization
  - load_registry() returning None for missing path, ValueError on corrupt JSON
  - save_registry() with parent directory creation
  - bootstrap_registry() creating registry only on first call (no-op thereafter)

affects:
  - 12-02: compact.py integration (uses classify_drift, load_registry, bootstrap_registry)
  - 12-03: cta-compact schema update command (uses save_registry, load_registry)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "JSON registry format: human-readable fields list + base64-encoded IPC schema blob for exact round-trip"
    - "frozenset of (old_type_str, new_type_str) tuples for O(1) widening pair lookup"
    - "name-indexed dict from pa.Schema for O(1) field lookup during drift classification"
    - "drift_type priority: breaking > additive > none"

key-files:
  created:
    - src/cta_eta/data_collection/compaction/schema_registry.py
    - tests/data_collection/compaction/test_schema_registry.py
  modified:
    - src/cta_eta/monitoring/cli.py
    - tests/monitoring/test_cli.py

key-decisions:
  - "Store WIDENING_PAIRS as frozenset of string tuples (str(pa.DataType)) for O(1) lookup — avoids pyarrow type object comparison complexity"
  - "registry_dict_to_schema() ignores human-readable fields list entirely — uses schema_ipc_b64 exclusively for reconstruction to avoid pa.lib.ensure_type failure on complex types like timestamp[us, tz=UTC]"
  - "load_registry() raises ValueError (not silently returns None) on corrupt JSON — forces operator attention to registry corruption vs. absence"
  - "bootstrap_registry() checks existence before save — ensures idempotent; multiple calls on running system do not overwrite registry"

patterns-established:
  - "Registry JSON format: {version, daemon, updated, fields (human-readable), schema_ipc_b64 (base64 IPC for reconstruction)}"
  - "TDD: test → implement → refactor commit cycle; each step committed separately"

requirements-completed: []

# Metrics
duration: 15min
completed: 2026-02-27
---

# Phase 12 Plan 01: Schema Registry Module Summary

**Schema registry core module with drift classification (breaking/additive/none), JSON+base64-IPC serialization for exact timestamp[us, tz=UTC] round-trip, and bootstrap/load/save helpers — 28 tests all passing**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-02-26T13:57:00Z (prior session)
- **Completed:** 2026-02-27T01:06:00Z
- **Tasks:** 3 (RED test, GREEN implement, REFACTOR clean up) + 1 auto-fix
- **Files modified:** 4

## Accomplishments
- Full TDD cycle: 28 failing tests written first (RED), then implementation made them pass (GREEN), then refactor cleaned up exception handler
- classify_drift() correctly classifies all drift categories: removed fields (breaking), incompatible type changes (breaking), safe numeric widenings (none), nullability changes (breaking), added fields (additive), field order differences (none)
- Schema round-trip verified: `timestamp[us, tz=UTC]` survives JSON serialization and deserialization via base64-IPC blob
- Auto-fixed pre-existing bug in cli.py: date filter used datetime comparison instead of date-level comparison, causing records from early in the cutoff day to be silently excluded

## Task Commits

Each task was committed atomically:

1. **Task 1: RED - failing tests for schema_registry** - `9113151` (test)
2. **Task 2: GREEN - implement schema_registry module** - `c49978a` (feat)
3. **Task 3: REFACTOR - clean up load_registry exception handler** - `9a37912` (refactor)
4. **Deviation: fix cmd_compaction date filter and cmd_gaps metadata** - `dfe2688` (fix)

## Files Created/Modified
- `src/cta_eta/data_collection/compaction/schema_registry.py` - Full public API: DriftResult, classify_drift, schema_to_registry_dict, registry_dict_to_schema, load_registry, save_registry, bootstrap_registry
- `tests/data_collection/compaction/test_schema_registry.py` - 28 unit tests covering all public functions and drift categories
- `src/cta_eta/monitoring/cli.py` - Bug fix: date-level cutoff comparison in cmd_compaction; fix schema_arrow.metadata access in cmd_gaps
- `tests/monitoring/test_cli.py` - Added TestCmdCompaction test class, compaction_dir/data_dir fixtures, refactored daemon_state_dir to use monkeypatch

## Decisions Made
- WIDENING_PAIRS stored as frozenset of string tuples rather than pyarrow type object pairs — avoids need to handle pyarrow type object equality semantics, simpler lookup via `str(pa.DataType)`
- `registry_dict_to_schema()` ignores the human-readable `fields` list and uses `schema_ipc_b64` exclusively — the human-readable list is for git diff only; `pa.lib.ensure_type("timestamp[us, tz=UTC]")` fails so IPC blob is required
- `load_registry()` raises `ValueError` on corrupt JSON (vs. returning None) — corruption and absence are different conditions requiring different operator responses
- `bootstrap_registry()` is idempotent (no-op if file exists) — safe to call on every compaction run without overwriting a valid registry

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed cmd_compaction date filter using datetime instead of date comparison**
- **Found during:** Verification (full test suite run after TDD tasks)
- **Issue:** `cmd_compaction` calculated `cutoff = datetime.now(tz=UTC) - timedelta(days=days)` and then compared `sidecar_date = datetime.fromisoformat(date_str).replace(tzinfo=UTC)` against it. Since sidecar dates parse to midnight UTC and the cutoff includes the current time, records from early in the cutoff day were excluded (2026-02-20 00:00 UTC < cutoff 2026-02-20 01:04 UTC = excluded)
- **Fix:** Changed to `cutoff_date = (...).date()` and `sidecar_date = datetime.fromisoformat(date_str).date()`, comparing at date level so any record on the cutoff date is included
- **Files modified:** `src/cta_eta/monitoring/cli.py`
- **Verification:** `tests/monitoring/test_cli.py::TestCmdCompaction::test_compaction_success_record_human` passes
- **Committed in:** `dfe2688`

**2. [Rule 1 - Bug] Fixed cmd_gaps using pq.read_metadata() instead of pq.ParquetFile schema_arrow**
- **Found during:** Same verification pass (cli.py diff review)
- **Issue:** `cmd_gaps` used `pq.read_metadata(parquet_file).schema.metadata` which returns Parquet schema metadata (bytes-level, not Arrow schema metadata); the gap_metadata key is stored in Arrow schema metadata accessible via `pf.schema_arrow.metadata`
- **Fix:** Changed to `with pq.ParquetFile(parquet_file) as pf: schema_metadata = pf.schema_arrow.metadata or {}`
- **Files modified:** `src/cta_eta/monitoring/cli.py`
- **Verification:** Consistent with pattern documented in RESEARCH.md (see "Reading Drift from Parquet" code example)
- **Committed in:** `dfe2688`

---

**Total deviations:** 2 auto-fixed (both Rule 1 bugs)
**Impact on plan:** Both fixes correct pre-existing bugs in cli.py exposed during full test suite verification. No scope creep — both fixes are in monitoring CLI, not schema_registry module.

## Issues Encountered
- None in the schema_registry TDD cycle itself — implementation matched research notes exactly
- Full test suite revealed pre-existing cli.py bugs (see deviations above)

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `schema_registry.py` public API is complete and tested: Plan 12-02 (compact.py integration) can import `classify_drift`, `load_registry`, `bootstrap_registry`
- `schema_to_registry_dict()` and `save_registry()` are ready for Plan 12-03 (cta-compact schema update command)
- No blockers

## Self-Check: PASSED

- FOUND: `src/cta_eta/data_collection/compaction/schema_registry.py`
- FOUND: `tests/data_collection/compaction/test_schema_registry.py`
- FOUND: `.planning/phases/12-storage-optimization/12-01-SUMMARY.md`
- FOUND: commit `9113151` (test: add failing tests)
- FOUND: commit `c49978a` (feat: implement schema_registry module)
- FOUND: commit `9a37912` (refactor: clean up load_registry exception handler)
- FOUND: commit `dfe2688` (fix: cmd_compaction date filter and cmd_gaps metadata)

---
*Phase: 12-storage-optimization*
*Completed: 2026-02-27*
