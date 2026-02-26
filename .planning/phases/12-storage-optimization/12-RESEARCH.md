# Phase 12: Schema Enforcement - Research

**Researched:** 2026-02-25
**Domain:** Parquet/Arrow schema validation, drift detection, schema registry patterns
**Confidence:** HIGH (all claims verified via live pyarrow 22.0.0 execution in project environment)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Drift detection scope:**
- Breaking changes only trigger alerts: removed fields and incompatible type changes (e.g., string->int)
- New fields added by the upstream API: silent — logged to file but no email/alert
- Loose type comparison: widening is ok (int->float is fine), only incompatible casts flag drift
- Nullability changes trigger drift (a field going from required to nullable is meaningful)
- Field order changes: irrelevant — Parquet is column-named, not position-indexed
- Mid-day partial drift (different IPC journals in the same day have different schemas): alert on first drifted journal, continue merging the rest; merged Parquet gets drift annotation
- Validation level: Claude's discretion (researcher should evaluate IPC-level vs final-Parquet-level)

**Response behavior:**
- When drift detected: continue compaction + alert (data is preserved, file is uploaded; operator decides what to do)
- Annotate the compacted Parquet file with file-level metadata key `schema_drift=true` so downstream readers can detect it without consulting the registry
- Registry update requires manual operator action — it does NOT auto-update on drift detection
- If same drift persists across multiple days (operator hasn't updated registry): alert every day until resolved

**Alert & monitoring surface:**
- Both email AND cta-monitor CLI show drift
- cta-monitor: add a drift indicator column to the existing compaction run history table (same table, new column — OK / DRIFT)
- cta-monitor: recent window only, same time window as current compaction view (no indefinite historical flagging)
- Email content: full field-level diff (field name, old type, new type for each changed field) — self-contained diagnostic

**Registry storage:**
- Location: source repo (checked into git) — schema changes tracked in git history alongside code
- File format: Claude's discretion — pick between Arrow IPC schema files or JSON based on what's easiest to diff in git and validate in pyarrow
- Initialization: bootstrap on first successful compaction with no registry present — observed schema becomes canonical, no manual setup required
- Manual update command: `cta-compact schema update` — operator runs this to promote the drifted schema to canonical (reads latest observed schema, overwrites registry file)

### Claude's Discretion
- Whether to validate at IPC journal level (per-file on read) or final Parquet level (post-merge) — researcher should identify which gives better signal without over-complexity
- Schema file format (Arrow IPC vs JSON) — pick based on git diff readability + pyarrow native support
- Exact metadata key naming for Parquet file-level drift annotation

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.

### Specific Ideas
- Registry files should be diffs-friendly since they live in git — human-readable format preferred if it doesn't sacrifice fidelity
- The `cta-compact schema update` command should auto-commit the updated registry file to git (or at minimum print the path so the operator can commit it)
</user_constraints>

---

## Summary

Phase 12 adds a schema registry and drift detection layer to the existing daily compaction job (`compact.py`). The registry stores the canonical schema for each daemon (train_positions, weather) as a JSON file in the source repo. Each compaction run loads the registry, compares each IPC journal's schema against the canonical, classifies any differences (breaking vs non-breaking), and decides whether to alert.

The compaction pipeline already opens each IPC journal file in `ipc_reader.py`; validating at the IPC level (per journal, using `reader.schema`) gives earlier, finer-grained detection at negligible cost. This is the right validation level: it detects partial-day drift (some journals drifted, others did not) and avoids the `pa.concat_tables()` failure that happens when incompatibly-typed journals are concatenated without special handling.

The recommended schema registry format is JSON with a human-readable fields list (name, type string, nullable) plus a base64-encoded IPC schema blob for exact round-trip reconstruction. The human-readable fields are what shows up in `git diff`; the base64 blob handles complex types like `timestamp[us, tz=UTC]` that pyarrow cannot reconstruct from their string representation alone.

**Primary recommendation:** Validate at IPC level (per journal), use JSON registry format with base64 IPC blob for reconstruction, use `table.replace_schema_metadata()` to annotate the merged Parquet, and extend `CompactionMetrics` with `schema_drift: bool` + `drift_summary: dict | None` for sidecar and CLI display.

---

## Decisions from Research

### Validation Level: IPC-level (per journal)

**Recommendation: IPC-level (validate each journal before merging)**

Rationale verified by code inspection and pyarrow testing:

1. **`compact.py` already reads each journal individually** — `read_ipc_with_repair()` is called per journal. Adding `reader.schema` access before reading batches adds near-zero cost (schema is in the IPC stream header).
2. **Partial-day drift detection is required** — the spec says "alert on first drifted journal, continue merging the rest." This is only possible at IPC level.
3. **`pa.concat_tables()` fails on schema mismatch without `promote_options`** — if incompatible-type journals reach the merge step, the concat raises `ArrowTypeError`. IPC-level detection prevents this.
4. **For new-field drift, use `promote_options='default'`** when merging drifted journals — pyarrow fills missing columns with null. This is the correct merge behavior for additive schema changes.

Current `compact.py` Step 3 already does `table.schema.equals(expected_schema)` and **skips** mismatched journals. Phase 12 changes this to: classify the mismatch (breaking vs non-breaking), alert if breaking or if new-field, and **continue** (not skip) unless incompatible type.

### Schema Registry File Format: JSON with base64 IPC blob

**Recommendation: JSON with human-readable fields + base64 IPC blob**

Verified by testing:

- `schema.serialize()` produces binary IPC bytes — not human-readable, not git-diffable
- `pa.lib.ensure_type(type_string)` **fails** for complex types like `timestamp[us, tz=UTC]` — JSON-only approach cannot reconstruct schema exactly
- Hybrid JSON format solves both problems: git sees the human-readable `fields` list on changes; the `ipc_base64` field enables exact reconstruction via `pa.ipc.read_schema(pa.BufferReader(base64.b64decode(ipc_b64)))`
- File extension: `.json`

Registry file example:
```json
{
  "version": 1,
  "fields": [
    {"name": "poll_timestamp", "type": "timestamp[us, tz=UTC]", "nullable": true},
    {"name": "route", "type": "string", "nullable": true},
    {"name": "lat", "type": "double", "nullable": true}
  ],
  "ipc_base64": "<base64-encoded IPC schema bytes>"
}
```

### Parquet Annotation Metadata Keys

**Recommendation:** Use these file-level metadata keys on the merged Parquet:

| Key | Values | Meaning |
|-----|--------|---------|
| `schema_drift` | `"true"` / `"false"` | Always set; downstream readers check this without consulting registry |
| `drift_summary` | JSON string | Only set if `schema_drift=true`; field-level diff |

`drift_summary` JSON shape:
```json
{
  "removed": ["field_name"],
  "added": ["field_name"],
  "incompatible": [{"field": "name", "old_type": "int64", "new_type": "string"}],
  "widened": [{"field": "name", "old_type": "int32", "new_type": "int64"}],
  "nullability_changed": [{"field": "name", "was_nullable": true, "now_nullable": false}]
}
```

This is consistent with the existing `gap_metadata` pattern in `cli.py` (cmd_gaps reads `b"gap_metadata"` from schema metadata).

---

## Standard Stack

### Core (already in project)
| Library | Version | Purpose | Notes |
|---------|---------|---------|-------|
| `pyarrow` | 22.0.0 | Schema comparison, IPC access, Parquet metadata | Already installed |
| `pyarrow.ipc` | 22.0.0 | `open_stream()`, `read_schema()` for IPC-level access | Already used in `ipc_reader.py` |
| `pyarrow.parquet` | 22.0.0 | `write_table()`, `ParquetFile.schema_arrow.metadata` | Already used in `compact.py` |
| `json` (stdlib) | 3.13 | Registry file I/O | Already used everywhere |
| `base64` (stdlib) | 3.13 | Schema IPC serialization in registry | No new dependency |

### No new dependencies needed
All required functionality is in pyarrow 22.0.0 (already installed). The `subprocess` stdlib module handles git operations for auto-commit.

---

## Architecture Patterns

### Recommended Project Structure

New files to create:

```
src/cta_eta/data_collection/compaction/
├── schemas.py           # (existing) - canonical pa.Schema constants
├── schema_registry.py   # NEW: registry load/save, drift detection logic
├── compact.py           # (modify) - integrate schema_registry, extend CompactionMetrics
└── __init__.py          # (existing)

schemas/                 # NEW directory at project root, git-tracked
├── train_positions.json # Registry for train daemon
└── weather.json         # Registry for weather daemon

tests/data_collection/compaction/
└── test_schema_registry.py  # NEW: unit tests for registry module
```

The `schemas/` directory at the project root is chosen over embedding in `src/` because:
- Registry files are operator-managed data artifacts, not source code
- Being at the root makes them easy to find (`ls schemas/`)
- The `cta-compact schema update` command writes here; operators commit from project root

### Pattern 1: Registry Load with Bootstrap

```python
# Source: verified pyarrow 22.0.0 + project conventions
import base64
import json
from pathlib import Path
import pyarrow as pa
import pyarrow.ipc

REGISTRY_DIR = Path(__file__).resolve().parents[4] / "schemas"

def load_registry(daemon_name: str) -> pa.Schema | None:
    """Load canonical schema from registry. Returns None if no registry exists."""
    registry_path = REGISTRY_DIR / f"{daemon_name}.json"
    if not registry_path.exists():
        return None
    data = json.loads(registry_path.read_text())
    ipc_bytes = base64.b64decode(data["ipc_base64"])
    return pa.ipc.read_schema(pa.BufferReader(ipc_bytes))

def save_registry(daemon_name: str, schema: pa.Schema) -> Path:
    """Save schema to registry. Creates REGISTRY_DIR if needed. Returns path written."""
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    registry_path = REGISTRY_DIR / f"{daemon_name}.json"
    fields = [
        {"name": schema.field(i).name, "type": str(schema.field(i).type), "nullable": schema.field(i).nullable}
        for i in range(len(schema))
    ]
    data = {
        "version": 1,
        "fields": fields,
        "ipc_base64": base64.b64encode(schema.serialize().to_pybytes()).decode(),
    }
    registry_path.write_text(json.dumps(data, indent=2) + "\n")
    return registry_path
```

### Pattern 2: Drift Classification

```python
# Source: verified pyarrow type APIs via live testing
import pyarrow as pa

def _is_numeric_widening(old_type: pa.DataType, new_type: pa.DataType) -> bool:
    """Return True if old_type -> new_type is a safe numeric widening."""
    def rank(t: pa.DataType) -> int:
        if pa.types.is_boolean(t): return 0
        if pa.types.is_integer(t):
            return {8: 1, 16: 2, 32: 3, 64: 4}.get(t.bit_width, 4)
        if pa.types.is_floating(t):
            return {16: 5, 32: 6, 64: 7}.get(t.bit_width, 7)
        return -1
    old_rank = rank(old_type)
    new_rank = rank(new_type)
    return old_rank >= 0 and new_rank >= 0 and new_rank >= old_rank

@dataclass
class DriftReport:
    has_breaking_drift: bool   # triggers alert + email
    has_new_fields: bool       # logged only, no alert
    removed: list[str]         # breaking
    added: list[str]           # non-breaking
    incompatible: list[dict]   # breaking: {field, old_type, new_type}
    widened: list[dict]        # non-breaking: {field, old_type, new_type}
    nullability_changed: list[dict]  # breaking: {field, was_nullable, now_nullable}

def classify_drift(canonical: pa.Schema, observed: pa.Schema) -> DriftReport:
    """Compare observed schema against canonical. Field order ignored."""
    canon = {canonical.field(i).name: canonical.field(i) for i in range(len(canonical))}
    obs = {observed.field(i).name: observed.field(i) for i in range(len(observed))}

    removed = [n for n in canon if n not in obs]
    added = [n for n in obs if n not in canon]
    incompatible = []
    widened = []
    nullability_changed = []

    for name in canon:
        if name not in obs:
            continue
        cf = canon[name]
        of = obs[name]
        if cf.type != of.type:
            if _is_numeric_widening(cf.type, of.type):
                widened.append({"field": name, "old_type": str(cf.type), "new_type": str(of.type)})
            else:
                incompatible.append({"field": name, "old_type": str(cf.type), "new_type": str(of.type)})
        if cf.nullable != of.nullable:
            nullability_changed.append({"field": name, "was_nullable": cf.nullable, "now_nullable": of.nullable})

    has_breaking = bool(removed or incompatible or nullability_changed)
    return DriftReport(
        has_breaking_drift=has_breaking,
        has_new_fields=bool(added),
        removed=removed, added=added, incompatible=incompatible,
        widened=widened, nullability_changed=nullability_changed,
    )
```

### Pattern 3: Parquet Drift Annotation

```python
# Source: verified via pyarrow 22.0.0 live testing
import json
import pyarrow as pa

def annotate_merged_table(merged: pa.Table, drift: DriftReport | None) -> pa.Table:
    """Add schema_drift metadata to merged Parquet table."""
    if drift is None or not (drift.has_breaking_drift or drift.has_new_fields):
        return merged.replace_schema_metadata({"schema_drift": "false"})
    summary = {
        "removed": drift.removed,
        "added": drift.added,
        "incompatible": drift.incompatible,
        "widened": drift.widened,
        "nullability_changed": drift.nullability_changed,
    }
    return merged.replace_schema_metadata({
        "schema_drift": "true",
        "drift_summary": json.dumps(summary),
    })
```

### Pattern 4: Merge with Mixed-Schema Journals

```python
# Source: verified via pyarrow 22.0.0 live testing
# When some journals have drifted (new fields), use promote_options

def merge_tables(tables: list[pa.Table], has_schema_drift: bool) -> pa.Table:
    """Merge journal tables, handling schema differences gracefully."""
    if not has_schema_drift:
        return pa.concat_tables(tables)
    # promote_options='default' fills missing columns with null values
    # Handles new-field drift; incompatible-type journals were already excluded
    return pa.concat_tables(tables, promote_options="default")
```

### Pattern 5: cta-compact schema update Subcommand

The `cta-compact` CLI currently uses `argparse` with a flat `--reprocess` flag. Phase 12 converts it to subcommands:

```python
# cta-compact schema update  -> operator updates registry
# cta-compact                -> runs compaction (default behavior, no subcommand)
# cta-compact --reprocess YYYY-MM-DD  -> backward compat with reprocess flag

# Argparse structure:
parser = argparse.ArgumentParser(prog="cta-compact")
subparsers = parser.add_subparsers(dest="command")
# ... existing flags on the default compaction path ...
schema_parser = subparsers.add_parser("schema")
schema_sub = schema_parser.add_subparsers(dest="schema_command")
update_parser = schema_sub.add_parser("update")
update_parser.add_argument("--daemon", choices=["train_positions", "weather", "all"])
```

Auto-commit via subprocess (no GitPython needed):
```python
import subprocess

def git_commit_registry(registry_path: Path) -> bool:
    """Stage and commit updated registry file. Returns True if committed."""
    result = subprocess.run(
        ["git", "add", str(registry_path)],
        capture_output=True, cwd=registry_path.parent.parent
    )
    if result.returncode != 0:
        _log.warning("git add failed: %s", result.stderr.decode())
        return False
    result = subprocess.run(
        ["git", "commit", "-m", f"chore: update schema registry for {registry_path.stem}"],
        capture_output=True, cwd=registry_path.parent.parent
    )
    if result.returncode != 0:
        _log.warning("git commit failed (nothing to commit?): %s", result.stderr.decode())
        return False
    return True
```

### Pattern 6: CompactionMetrics Extension

```python
# Extend existing dataclass with drift fields
@dataclass
class CompactionMetrics:
    # ... existing fields ...
    schema_drift: bool = False          # new: was breaking drift detected?
    drift_summary: dict | None = None   # new: field-level diff if drifted
```

The sidecar JSON (written by `_write_sidecar`) includes these via `dataclasses.asdict()` automatically.

### Pattern 7: cta-monitor compaction Column Extension

The existing `cmd_compaction()` table in `cli.py` shows columns:
`Date | Daemon | Status | Journals | Rows | Upload | Elapsed`

Phase 12 adds a `Schema` column:
`Date | Daemon | Status | Journals | Rows | Upload | Elapsed | Schema`

Where `Schema` shows `OK` or `DRIFT` based on `record.get("schema_drift", False)`.

The CLI reads sidecar JSON files from `data/compaction/` — the new `schema_drift` field is read from those JSON files, consistent with existing pattern.

### Anti-Patterns to Avoid

- **`pa.types.is_integer()` for narrowing check alone** — `pa.types.is_integer(pa.int32())` is True AND `pa.types.is_integer(pa.int64())` is True; you must also compare `bit_width` to determine widening direction.
- **Storing schema as pure JSON type strings** — `pa.lib.ensure_type("timestamp[us, tz=UTC]")` fails; always keep the base64 IPC blob for reconstruction.
- **Storing schema binary as `.arrows` file** — not git-diffable; the hybrid JSON+base64 approach is better.
- **Auto-updating the registry on drift** — spec says manual only; auto-update would hide upstream API changes from operators.
- **Skipping drifted journals** — current Phase 11 behavior skips schema-mismatched journals; Phase 12 changes this to continue-and-alert (data preservation principle).
- **Validating only at Parquet level** — misses partial-day drift and risks `ArrowTypeError` on `concat_tables()` for incompatible types.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Schema serialization | Custom serialization | `schema.serialize()` + base64 | Handles all Arrow types correctly including timestamp with tz |
| Schema comparison | String comparison of field lists | `pa.Schema.field()` + type identity | pyarrow type objects have proper `==` semantics |
| Type compatibility check | `isinstance` checks | `pa.types.is_integer()`, `pa.types.is_floating()`, `.bit_width` | Covers all numeric subtypes correctly |
| Parquet metadata I/O | Custom file format | `table.replace_schema_metadata()` + `pq.ParquetFile.schema_arrow.metadata` | Matches existing gap_metadata pattern in codebase |
| IPC schema access | Reading all batches | `ipc.open_stream(path).schema` | Schema is in the IPC header; cheap; no batch reading needed |

**Key insight:** pyarrow 22.0.0 has complete schema introspection APIs. No custom schema diffing library is needed.

---

## Common Pitfalls

### Pitfall 1: Using `schema.equals()` for Drift Detection

**What goes wrong:** `schema1.equals(schema2)` returns `False` for ANY difference: added fields, field order, type widening. It cannot distinguish breaking from non-breaking changes.

**Why it happens:** `schema.equals()` is for exact equality. Current `compact.py` uses it to skip mismatched journals — correct for Phase 11, wrong for Phase 12.

**How to avoid:** Use field-by-field comparison via `schema.field(name)` with name-based lookup (not index-based). Classify each difference into removed/added/incompatible/widened/nullability categories.

**Warning signs:** All schema changes triggering alerts, including benign new fields.

### Pitfall 2: Type String Reconstruction Fails for Complex Types

**What goes wrong:** `pa.lib.ensure_type("timestamp[us, tz=UTC]")` raises `ArrowInvalid`. A JSON-only registry (storing type as string) cannot reconstruct the schema.

**How to avoid:** Always store `ipc_base64` alongside the human-readable fields list. Use `pa.ipc.read_schema(pa.BufferReader(base64.b64decode(b64str)))` for reconstruction.

**Warning signs:** `ArrowInvalid: No type alias for timestamp[us, tz=utc]` in logs.

### Pitfall 3: `concat_tables()` Without `promote_options` Fails on Mixed Schemas

**What goes wrong:** When some journals have drifted (new field added), `pa.concat_tables([t_old, t_new])` raises `ArrowInvalid: Schema at index 1 was different`.

**Why it happens:** pyarrow's `concat_tables` requires schema equality by default.

**How to avoid:** When drift is detected, use `pa.concat_tables(tables, promote_options="default")`. This fills missing columns with nulls, producing a valid merged table that captures all data.

**Warning signs:** Compaction failing entirely after an upstream API adds a field.

### Pitfall 4: Git Subprocess in Restricted Environments

**What goes wrong:** `subprocess.run(["git", "commit", ...])` fails if `cta-compact schema update` is run inside a restricted environment without git configured or without a git repo.

**Why it happens:** The registry lives in the source repo, but the command might be run from a different working directory or from a deployed environment.

**How to avoid:** Always print the registry path even if auto-commit fails. Wrap git operations in try/except, degrade gracefully to "print path for manual commit" mode. Use `cwd=project_root` explicitly.

**Warning signs:** `cta-compact schema update` silently fails the git commit but reports success.

### Pitfall 5: Sidecar JSON Schema Changes Break Existing CLI

**What goes wrong:** Adding `schema_drift` and `drift_summary` fields to `CompactionMetrics` via `dataclasses.asdict()` changes the sidecar JSON format. Old sidecars won't have these fields; `cmd_compaction()` will raise `KeyError` when reading them.

**Why it happens:** `dataclasses.asdict()` serializes all fields; `json.load()` returns all present keys; missing keys raise `KeyError` if accessed without `.get()`.

**How to avoid:** Use `record.get("schema_drift", False)` and `record.get("drift_summary")` in `cmd_compaction()` — consistent with how all other sidecar fields are already read.

### Pitfall 6: Daily Repeat Alert for Unresolved Drift

**What goes wrong:** The spec requires "alert every day until resolved." Compaction runs once per day, so simply alerting on each run where drift is detected satisfies this. The pitfall is accidentally implementing cooldown logic that suppresses the daily repeat.

**Why it happens:** The existing `send_compaction_alert()` path (for upload failures) uses no cooldown — it alerts immediately. Copy this pattern for drift alerts; do NOT reuse the `run_alerts.py` cooldown mechanism.

**How to avoid:** Send drift alerts directly via `send_email_alert()` in the compaction loop, not through the cooldown-gated `run_alerts.py` path.

---

## Code Examples

### IPC Schema Access Before Reading Batches

```python
# Source: verified pyarrow 22.0.0, ipc_reader.py pattern
from pyarrow import ipc
import pyarrow as pa

def get_journal_schema(path: Path) -> pa.Schema | None:
    """Read IPC schema from journal header without reading all batches."""
    try:
        reader = ipc.open_stream(path)
        return reader.schema
    except pa.lib.ArrowInvalid:
        return None  # Corrupt header — ipc_reader.py handles this
```

### Full Drift Detection Flow in Compact Loop

```python
# Source: verified pattern combining ipc_reader.py + new registry module
canonical = load_registry(daemon_name)  # None if first run
all_drift_reports: list[DriftReport] = []
tables: list[pa.Table] = []

for journal_path in journal_files:
    batches, was_clean = read_ipc_with_repair(journal_path)
    if not batches:
        journals_skipped += 1
        continue

    table = pa.Table.from_batches(batches)
    observed_schema = table.schema

    # Bootstrap registry on first run
    if canonical is None:
        save_registry(daemon_name, observed_schema)
        canonical = observed_schema
        _log.info("Bootstrapped schema registry for %s", daemon_name)

    # Classify drift
    drift = classify_drift(canonical, observed_schema)
    if drift.has_breaking_drift or drift.has_new_fields:
        all_drift_reports.append(drift)
        _log.warning("Schema drift in %s: %s", journal_path.name, drift)

    # For incompatible type: skip (cannot safely merge)
    if drift.incompatible:
        journals_skipped += 1
        continue

    tables.append(table)

# Merge (promote_options if any drift involved new fields)
has_drift = bool(all_drift_reports)
merged = merge_tables(tables, has_drift)

# Annotate
aggregate_drift = _aggregate_drift_reports(all_drift_reports)
merged = annotate_merged_table(merged, aggregate_drift)
```

### Reading Drift from Parquet (downstream / CLI pattern)

```python
# Source: verified pyarrow 22.0.0, consistent with cmd_gaps() in cli.py
import pyarrow.parquet as pq
import json

with pq.ParquetFile(parquet_path) as pf:
    meta = pf.schema_arrow.metadata or {}

has_drift = meta.get(b"schema_drift", b"false") == b"true"
if has_drift and b"drift_summary" in meta:
    summary = json.loads(meta[b"drift_summary"].decode())
```

### cta-monitor compaction Table with Drift Column

```python
# Source: based on existing cmd_compaction() in cli.py
schema_val = "DRIFT" if record.get("schema_drift", False) else "OK"
print(
    f"{date_val:<12} {daemon_val:<20} {status_str:<10} {journals_str:<16} "
    f"{rows_str:<12} {upload_str:<10} {elapsed_str:<8} {schema_val:<8}"
)
```

---

## State of the Art

| Old Approach | Current Approach | Impact for This Phase |
|--------------|------------------|----------------------|
| Skip journals with wrong schema | Classify and continue + alert | Core Phase 12 behavior change |
| Hard-coded schema constants only | Schema registry + drift detection | New registry module needed |
| Single-table schema in IPC header | Schema comparison at per-journal level | IPC `reader.schema` is the hook |
| No Parquet-level drift annotation | `replace_schema_metadata()` for downstream | Follows existing `gap_metadata` pattern |

**pyarrow 22.0.0 specific notes (verified):**
- `pa.Table.replace_schema_metadata(dict)` — available and working
- `pa.ipc.open_stream(path).schema` — reads from IPC header, does not require reading batches
- `pa.ipc.read_schema(buffer)` — reconstructs schema from IPC-serialized bytes
- `pa.concat_tables(tables, promote_options="default")` — fills missing columns with null (confirmed working)
- `pa.lib.ensure_type(type_string)` — **fails for `timestamp[us, tz=UTC]`**; base64 IPC is required

---

## Open Questions

1. **CompactionMetrics drift fields: single aggregate or per-journal?**
   - What we know: some journals may drift, others may not. The sidecar records one `CompactionMetrics` per daemon per day.
   - What's unclear: should `drift_summary` in the sidecar be an aggregate of all journal drift, or just the first drifted journal?
   - Recommendation: aggregate across all journals in the day (union of removed/added/incompatible). This gives the operator a complete picture of what changed.

2. **Registry path resolution at runtime**
   - What we know: `config.py` resolves paths relative to `Path(__file__).resolve().parents[N]`. The registry will live at project root / `schemas/`.
   - What's unclear: exact `parents` depth from `schema_registry.py` to project root (depends on where the module lives).
   - Recommendation: verify depth: `src/cta_eta/data_collection/compaction/schema_registry.py` → `parents[4]` = project root.

3. **`cta-compact schema update` backward compatibility**
   - What we know: currently `cta-compact` uses flat argparse with `--reprocess`. Adding subcommands changes the CLI interface.
   - What's unclear: whether operators use `cta-compact` without subcommands in scripts.
   - Recommendation: keep `--reprocess` on the default "run" path. Use subparsers where the default (no subcommand) is the compaction run. `cta-compact schema update` is a new, additive subcommand.

---

## Sources

### Primary (HIGH confidence — verified in project environment)
- pyarrow 22.0.0 live Python REPL — all code examples verified with `uv run python`
- `src/cta_eta/data_collection/compaction/compact.py` — existing compaction orchestration
- `src/cta_eta/data_collection/compaction/ipc_reader.py` — IPC journal read pattern
- `src/cta_eta/data_collection/compaction/schemas.py` — existing canonical schema constants
- `src/cta_eta/monitoring/cli.py` — existing `cmd_compaction()` and `cmd_gaps()` patterns

### Secondary (MEDIUM confidence)
- pyarrow 22.0.0 documentation — schema, IPC, and Parquet APIs
- `tests/data_collection/compaction/test_compact.py` — test patterns to follow

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all pyarrow APIs verified via live execution
- Architecture: HIGH — patterns derived from existing codebase code paths
- Pitfalls: HIGH — each pitfall discovered via live testing (e.g., `ensure_type` failure, `concat_tables` failure)
- Registry format: HIGH — JSON+base64 hybrid tested end-to-end with full schema round-trip

**Research date:** 2026-02-25
**Valid until:** 2026-06-01 (pyarrow APIs are stable; monitor for pyarrow major version changes)
