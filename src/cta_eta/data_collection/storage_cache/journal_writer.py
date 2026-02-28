"""Arrow IPC stream writer with time-based rotation and hive-style directory paths.

Provides append-friendly journaling for CTA train data collection: each poll
appends a record batch to the current journal file; after a configurable
rotation interval the journal closes and a new one opens.

Journal files live in a hive-style directory:
    {data_path}/{dataset_name}/year=YYYY/month=MM/day=DD/journal_HHMMSS.ipc

This eliminates the per-poll small-file problem (formerly ~5,760 files/day).
Cloud upload and compaction are handled separately in compaction.

Partitioning:
- Hive-style daily partitions (date=YYYY-MM-DD/)
- Timezone-aware split at 3:00 AM America/Chicago to minimize splitting active train runs
- Preserves all data points without deduplication (raw collection priority)
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.ipc

### OWN MODULES
from cta_eta.data_collection.storage_cache.storage import (
    LocalStorage,
    StorageBackend,
    WritableFile,
)
from cta_eta.data_collection.storage_cache.writer_protocol import RotatableWriter


class JournalWriter(RotatableWriter):
    """Appends Arrow record batches to local IPC stream files with time-based rotation.

    Each call to append_batch() writes to the current open journal file.
    When the rotation interval elapses the journal rotates: the current file
    is closed cleanly and the next append opens a new file.

    Schema is inferred from the first batch and reused across rotations so
    all records produced by the same daemon share a consistent schema.
    """

    def __init__(
        self,
        storage_backend: StorageBackend | None = None,
        partition_hour: int = 3,
        timezone: str = "America/Chicago",
        data_path: str | Path | None = None,
        rotation_interval_seconds: int = 900,
    ) -> None:
        """Initialize JournalWriter.

        Args:
            storage_backend: Storage backend providing append-capable file handles.
                When omitted, a LocalStorage backend is created from ``data_path``.
            partition_hour: Hour in timezone to split days (default 3 for 3:00 AM)
            timezone: IANA timezone name used to compute hive-style partition
                paths (default "America/Chicago").
            data_path: Base directory for local journaling. Used only when
                ``storage_backend`` is not provided.
            rotation_interval_seconds: Seconds before the current journal is
                rotated to a new file (default 900 = 15 minutes).

        """
        if storage_backend is not None and data_path is not None:
            msg = "Pass either storage_backend or data_path, not both."
            raise ValueError(msg)

        if storage_backend is None:
            resolved_data_path = (
                Path(data_path) if data_path is not None else Path("data")
            )
            storage_backend = LocalStorage(base_path=resolved_data_path)
        self._storage_backend = storage_backend
        self._partition_hour = partition_hour
        self._timezone = ZoneInfo(timezone)

        # Preserve local path visibility for diagnostics/tests.
        self._data_path = (
            self._storage_backend.base_path
            if isinstance(self._storage_backend, LocalStorage)
            else None
        )
        self._rotation_interval_seconds = rotation_interval_seconds

        self._writer: pa.ipc.RecordBatchStreamWriter | None = None
        self._sink: WritableFile | None = None
        self._current_relative_path: str | None = None
        self._current_file: Path | None = None
        self._journal_start_time: datetime | None = None
        self._schema: pa.Schema | None = None

    def _calculate_partition_date(self, timestamp: datetime) -> tuple[int, int, int]:
        """Calculate partition date based on timezone and partition hour.

        Args:
            timestamp: Datetime to partition (assumed UTC if naive)

        Returns:
            Partition date tuple (year, month, day)

        """
        # Convert to timezone-aware UTC if naive
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=ZoneInfo("UTC"))

        # Convert to target timezone
        local_time = timestamp.astimezone(self._timezone)

        # If before partition hour, use previous day
        if local_time.hour < self._partition_hour:
            partition_date = local_time.date() - timedelta(days=1)
        else:
            partition_date = local_time.date()

        year, month, day = partition_date.year, partition_date.month, partition_date.day
        return year, month, day

    # Public API
    def append_batch(
        self,
        records: list[dict[str, Any]],
        dataset_name: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append a list of records to the current journal file.

        If no journal is open a new one is created.  If the rotation interval
        has elapsed the current journal is first closed and a new one opened.

        Args:
            records: Non-empty list of dicts to write as a record batch.
            dataset_name: Subdirectory name under data_path (default "default").
            metadata: Optional metadata. Ignored. For interface parity with writer.

        Raises:
            ValueError: If records is empty.

        """
        if not records:
            msg = "Cannot write empty batch"
            raise ValueError(msg)

        _ = metadata

        # Use first record's timestamp to determine partition
        # (all records in a single write call should have same partition)
        current_timestamp = datetime.now(ZoneInfo("UTC"))
        first_timestamp = records[0].get("request_timestamp", current_timestamp)
        if isinstance(first_timestamp, str):
            first_timestamp = datetime.fromisoformat(first_timestamp)

        if not isinstance(first_timestamp, datetime):
            msg = f"Could not convert data timestamp {first_timestamp!s} into datetime."
            raise TypeError(msg)

        year, month, day = self._calculate_partition_date(first_timestamp)

        # Generate partition path and timestamp suffix
        now = datetime.now(self._timezone)
        partition_path = f"year={year}/month={month:02d}/day={day:02d}/journal_{now.strftime('%H%M%S_%f')}.ipc"
        if dataset_name != "default":
            partition_path = f"{dataset_name}/{partition_path}"

        batch = pa.RecordBatch.from_pylist(records)

        # Capture schema from the first batch; reuse on every subsequent call.
        if self._schema is None:
            self._schema = batch.schema

        # Rotate if the interval has elapsed OR if our partition hour has passed
        if self._writer is not None and self._journal_start_time is not None:
            now = datetime.now(self._timezone)
            elapsed = (now - self._journal_start_time).total_seconds()
            if elapsed >= self._rotation_interval_seconds or (
                now.hour >= self._partition_hour
                and self._journal_start_time.hour < self._partition_hour
            ):
                self.rotate()

        # Open a new journal if none is active.
        if self._writer is None:
            self._open_new_journal(partition_path)

        self._writer.write_batch(batch)  # type: ignore[union-attr]

    def rotate(self) -> None:
        """Close the current journal file.

        The next call to append_batch() will open a fresh journal file.
        This is a no-op if no journal is currently open.
        """
        if self._writer is None:
            return
        self._writer.close()
        if self._sink is not None:
            self._sink.close()
        self._writer = None
        self._sink = None
        self._current_relative_path = None
        self._current_file = None
        self._journal_start_time = None

    def close(self) -> None:
        """Flush and close the current journal file cleanly.

        Equivalent to rotate(); the IPC stream EOS marker is written so the
        file can be read back immediately with pa.ipc.open_stream().
        """
        self.rotate()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_new_journal(self, relative_path: str) -> None:
        """Open a new journal file under the hive-style partition path.

        Args:
            relative_path: Relative path used for the journal file.

        """
        now = datetime.now(self._timezone)
        sink = self._storage_backend.open_writer(relative_path)
        self._writer = pa.ipc.new_stream(sink, self._schema)
        self._sink = sink
        self._current_relative_path = relative_path
        if isinstance(self._storage_backend, LocalStorage):
            self._current_file = self._storage_backend.base_path / relative_path
        else:
            self._current_file = None
        self._journal_start_time = now


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_journal_writer(config: dict[str, dict[str, Any]]) -> JournalWriter:
    """Create a JournalWriter from application configuration.

    Args:
        config: Configuration dict from load_config() (e.g. config.toml).
            Reads from ``config["storage"]["immediate"]``: data_path (default
            "data/journals"), journal_rotation_minutes (default 15),
            partition_hour (default 3).

    Returns:
        Configured JournalWriter instance.

    """
    immediate = config.get("storage", {}).get("immediate", {})
    if not isinstance(immediate, dict):
        immediate = {}
    data_path: str = str(immediate.get("data_path", "data/journals"))
    rotation_minutes: int = int(immediate.get("journal_rotation_minutes", 15))
    rotation_seconds = rotation_minutes * 60
    partition_hour: int = int(immediate.get("partition_hour", 3))

    return JournalWriter(
        storage_backend=LocalStorage(base_path=Path(data_path)),
        rotation_interval_seconds=rotation_seconds,
        partition_hour=partition_hour,
    )
