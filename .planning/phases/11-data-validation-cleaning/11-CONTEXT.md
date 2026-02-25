# Phase 11: Data Compaction - Context

**Gathered:** 2026-02-24
**Status:** Ready for planning

<domain>
## Phase Boundary

Daily batch job that merges yesterday's Arrow IPC journal files (written by Phase 10's JournalWriter) into a single validated, Snappy-compressed Parquet file per daemon per day, then uploads to cloud storage as immutable raw data. Schema enforcement and statistical drift detection are Phase 12.

</domain>

<decisions>
## Implementation Decisions

### Job scheduling & recovery
- Systemd timer — consistent with existing daemon deployment pattern
- Persistent/catch-up timer: if machine is offline at 3am, run immediately on next boot
- Support a `--reprocess <date>` flag to re-compact a past day (overwrites existing Parquet)
- After successful upload, move local journal files to an archive folder with configurable retention period (not deleted immediately, not kept indefinitely)

### Cloud storage target
- Use a pluggable/abstract interface (fsspec-compatible) — specific cloud provider configured at runtime, not hardcoded
- Hive-style partitioning path: e.g., `raw/train-positions/date=2026-02-17/data.parquet`
- Credentials via environment variables (provider SDK auto-discovery: AWS_*, GCS_*, etc.)
- After upload, verify row count matches local total before marking journals for archival

### Validation & error handling
- Missing journal files (daemon was down): compact what exists, log which time windows had no data — don't block on gaps
- Corrupt journal files: attempt partial repair (truncate to last valid IPC record) before skipping
- Upload failure: retry 3 times with exponential backoff, then fail and send alert; leave journals in place for next run
- Pre-upload validation: schema validation only — confirm columns and types match expected schema (statistical validation is Phase 12)

### Monitoring integration
- Add compaction section to existing `cta-monitor` CLI (not a separate command)
- Failed compaction triggers the existing Phase 9 email alerting system
- Core metrics tracked: journal files processed, rows written, upload size, elapsed time, final status
- Metrics persisted as JSON sidecar file (e.g., `compaction-2026-02-17.json`) alongside local compacted output — cta-monitor reads from there

### Claude's Discretion
- Exact retention period default for journal archive folder
- Internal IPC repair implementation details
- Systemd timer unit configuration specifics
- Row group size and other Parquet write options (Snappy compression is fixed)

</decisions>

<specifics>
## Specific Ideas

- The archive-then-delete pattern (configurable retention) gives a safety window in case upload verification fails or bugs are discovered post-upload
- Row count verification before journal archival is the key safety gate — don't archive until the cloud copy is confirmed correct
- The compaction section in cta-monitor should show last run time, status, and key stats at a glance

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 11-data-compaction*
*Context gathered: 2026-02-24*
