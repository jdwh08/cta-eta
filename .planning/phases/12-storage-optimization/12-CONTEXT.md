# Phase 12: Schema Enforcement - Context

**Gathered:** 2026-02-25
**Status:** Ready for planning

<domain>
## Phase Boundary

Parquet schema registry/validation with drift detection and alerting on schema changes from CTA train position or weather API updates. Runs as part of the existing daily compaction job (3am Chicago time). Schema registry is separate per daemon (train vs weather). The registry lives in the source repo so changes are tracked in git history.

</domain>

<decisions>
## Implementation Decisions

### Drift detection scope
- Breaking changes only trigger alerts: removed fields and incompatible type changes (e.g., string→int)
- New fields added by the upstream API: silent — logged to file but no email/alert
- Loose type comparison: widening is ok (int→float is fine), only incompatible casts flag drift
- Nullability changes trigger drift (a field going from required to nullable is meaningful)
- Field order changes: irrelevant — Parquet is column-named, not position-indexed
- Mid-day partial drift (different IPC journals in the same day have different schemas): alert on first drifted journal, continue merging the rest; merged Parquet gets drift annotation
- Validation level: Claude's discretion (researcher should evaluate IPC-level vs final-Parquet-level)

### Response behavior
- When drift detected: continue compaction + alert (data is preserved, file is uploaded; operator decides what to do)
- Annotate the compacted Parquet file with file-level metadata key `schema_drift=true` so downstream readers can detect it without consulting the registry
- Registry update requires manual operator action — it does NOT auto-update on drift detection
- If same drift persists across multiple days (operator hasn't updated registry): alert every day until resolved

### Alert & monitoring surface
- Both email AND cta-monitor CLI show drift
- cta-monitor: add a drift indicator column to the existing compaction run history table (same table, new column — OK / DRIFT)
- cta-monitor: recent window only, same time window as current compaction view (no indefinite historical flagging)
- Email content: full field-level diff (field name, old type, new type for each changed field) — self-contained diagnostic

### Registry storage
- Location: source repo (checked into git) — schema changes tracked in git history alongside code
- File format: Claude's discretion — pick between Arrow IPC schema files or JSON based on what's easiest to diff in git and validate in pyarrow
- Initialization: bootstrap on first successful compaction with no registry present — observed schema becomes canonical, no manual setup required
- Manual update command: `cta-compact schema update` — operator runs this to promote the drifted schema to canonical (reads latest observed schema, overwrites registry file)

### Claude's Discretion
- Whether to validate at IPC journal level (per-file on read) or final Parquet level (post-merge) — researcher should identify which gives better signal without over-complexity
- Schema file format (Arrow IPC vs JSON) — pick based on git diff readability + pyarrow native support
- Exact metadata key naming for Parquet file-level drift annotation

</decisions>

<specifics>
## Specific Ideas

- Registry files should be diffs-friendly since they live in git — human-readable format preferred if it doesn't sacrifice fidelity
- The `cta-compact schema update` command should auto-commit the updated registry file to git (or at minimum print the path so the operator can commit it)

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 12-storage-optimization*
*Context gathered: 2026-02-25*
