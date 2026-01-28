# Minimal WAL/Queue Specification (Parquet-Compatible)

## Summary
Implement a minimal, durable write-ahead log (WAL) / queue that decouples polling from
Parquet writes. The WAL must accept JSON-like records (dicts) for train positions and
weather snapshots, persist them on local disk with bounded growth, and reliably drain
into the existing Parquet writer. This avoids data loss when Parquet writes fail while
keeping the system lightweight for a small VM.

This is a minimal, best-practices design: one writer (polling daemon), one drainer
(flush loop), and a local disk buffer with bounded size and explicit backpressure.

## Goals
- Never drop data when the API is healthy and local disk is healthy.
- Allow polling to continue through transient Parquet or cloud write failures.
- Keep implementation simple and robust on low-resource VMs.
- Provide deterministic ordering and exactly-once persistence into Parquet files.
- Enable recovery after crash with minimal complexity.

## Non-Goals
- Distributed queueing or cross-host replication.
- High-throughput batch processing beyond current polling volumes.
- Multi-writer concurrency from multiple processes.

## Constraints
- Python 3.13+.
- Local disk only for buffer; cloud storage is optional and can be added later.
- Must integrate with existing `ParquetWriter.append_batch()`.
- Must tolerate abrupt process termination (SIGKILL).

## High-Level Architecture
1. **WAL Writer (polling loop)**:
   - Serialize each cycle’s records into a WAL segment on local disk.
   - fsync to ensure durability before acknowledging the cycle.
2. **WAL Drainer (background task)**:
   - Read WAL segments in order.
   - Convert segment records to a batch and call `append_batch()`.
   - On success, mark segment as committed and delete it.
3. **Backpressure**:
   - If WAL exceeds a configured size threshold, pause polling until drained.

## Data Model
Each WAL segment file represents one polling cycle (or a small batch of cycles).

### Segment file naming
```
{wal_root}/train_positions/YYYY/MM/DD/
  segment_{unix_ms}_{sequence}.jsonl
```

### File format
JSON Lines (one record per line), UTF-8, newline-delimited.
Each record is a JSON object consistent with the normalized records currently written
to Parquet.

### Segment metadata
Sidecar metadata file with the same basename:
```
segment_{unix_ms}_{sequence}.meta.json
```
Fields:
- `created_at`: ISO timestamp
- `record_count`: int
- `payload_bytes`: int
- `poll_timestamp`: float
- `gap_metadata`: dict | null (copied from daemon)
- `schema_version`: int

## WAL State Machine
Segments move through these states by filename or directory:
- `pending/`: newly written, ready to drain
- `inflight/`: being drained (optional, for crash safety)
- `committed/`: drained, safe to delete
- `failed/`: drain attempted but failed (for inspection)

Minimal implementation can use only `pending/` and delete on success, but `inflight/`
adds crash safety (avoid partial drain duplication).

## Ordering and Idempotence
- Drain in lexicographic order of segments (timestamp + sequence).
- Each segment drains atomically: load all records, append to Parquet once.
- If drain fails, segment stays in `pending/` (or `failed/`) and will retry later.
- No deduplication needed because `append_batch()` is the single commit point.

## Backpressure Policy
Config:
- `wal_max_bytes`: total bytes allowed under `wal_root` for a dataset.
- `wal_pause_threshold_bytes`: at or above this, polling pauses until drained.
- `wal_resume_threshold_bytes`: resume when below this.

Behavior:
- On each poll, check size.
- If above pause threshold, sleep and retry until below resume threshold.

## Failure Scenarios
- **Parquet write fails**: WAL remains; polling continues unless WAL is full.
- **Disk full**: WAL writer fails, trigger backoff and pause polling immediately.
- **Process crash**: on restart, drain any pending segments before polling.
- **Partial segment write**: write to temp file, fsync, then rename to final name.

## Atomicity and Durability
Use atomic file writes:
1. Write to `segment.tmp`.
2. `fsync()` file and parent directory.
3. `rename()` to final name.

This ensures segment is either fully present or not visible.

## Integration Points
### In `TrainPositionDaemon`
- Replace direct `storage.append_batch()` with `wal.enqueue_batch(...)`.
- Introduce a `wal.flush()` method called periodically or in a background task.
- On shutdown, call `wal.flush()` and `wal.close()`.

### In `storage_cache.storage`
- Provide a `WalQueue` class that wraps `ParquetWriter`.
- The WAL is independent of the Parquet writer and does not use fsspec directly.

## Suggested API (Python)
```
class WalQueue:
    def __init__(self, *, root: Path, dataset: str, writer: ParquetWriter, config: WalConfig)

    def enqueue_batch(
        self,
        records: list[dict[str, Any]],
        *,
        poll_timestamp: float,
        gap_metadata: dict[str, Any] | None,
    ) -> None

    def flush_once(self) -> bool
        # Returns True if a segment was drained successfully.

    def drain_until_empty(self, *, max_seconds: float | None = None) -> None

    def size_bytes(self) -> int
```

## Configuration (config.toml)
```
[collection.wal]
wal_enabled = true
wal_root = ".wal"
wal_max_bytes = 2_000_000_000
wal_pause_threshold_bytes = 1_500_000_000
wal_resume_threshold_bytes = 1_200_000_000
wal_flush_interval_seconds = 10
wal_segment_max_records = 500
wal_segment_max_bytes = 5_000_000
```

## Implementation Steps (Minimal)
1. Implement `WalQueue` with JSONL segment writing and drain logic.
2. Add a background task in the daemon to call `flush_once()` every N seconds.
3. Gate polling on `wal_pause_threshold_bytes`.
4. Ensure restart drains pending segments before polling.
5. Update tests: WAL enqueue, drain success, drain retry, crash recovery.

## Testing Requirements
- WAL writes segment with correct metadata.
- Crash recovery: segments remain and drain on restart.
- Drain failure retains segment and retries.
- Backpressure pauses polling when WAL size exceeds threshold.
- Drain order is deterministic.

## Validation Criteria
- No data loss when Parquet writes fail transiently.
- Polling resumes automatically after storage recovery.
- WAL size does not grow without bound.
