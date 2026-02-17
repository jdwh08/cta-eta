"""Arrow IPC stream writer with time-based rotation and hive-style directory paths.

Provides append-friendly journaling for CTA train data collection: each poll
appends a record batch to the current journal file; after a configurable
rotation interval the journal closes and a new one opens.

Journal files live in a hive-style directory:
    {data_path}/{dataset_name}/year=YYYY/month=MM/day=DD/journal_HHMMSS.ipc

This eliminates the per-poll small-file problem (formerly ~5,760 files/day).
Cloud upload and compaction are handled separately in later phases.
"""

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.ipc


class JournalWriter:
    """Appends Arrow record batches to local IPC stream files with time-based rotation.

    Each call to append_batch() writes to the current open journal file.
    When the rotation interval elapses the journal rotates: the current file
    is closed cleanly and the next append opens a new file.

    Schema is inferred from the first batch and reused across rotations so
    all records produced by the same daemon share a consistent schema.
    """

    def __init__(
        self,
        data_path: str | Path,
        rotation_interval_seconds: int = 900,
        timezone: str = "America/Chicago",
    ) -> None:
        """Initialize JournalWriter.

        Args:
            data_path: Base directory where journal files are written.
            rotation_interval_seconds: Seconds before the current journal is
                rotated to a new file (default 900 = 15 minutes).
            timezone: IANA timezone name used to compute hive-style partition
                paths (default "America/Chicago").

        """
        self._data_path = Path(data_path)
        self._rotation_interval_seconds = rotation_interval_seconds
        self._timezone = ZoneInfo(timezone)

        self._writer: pa.ipc.RecordBatchWriter | None = None
        self._sink: pa.OSFile | None = None
        self._current_file: Path | None = None
        self._journal_start_time: datetime | None = None
        self._schema: pa.Schema | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append_batch(
        self,
        records: list[dict[str, Any]],
        dataset_name: str = "default",
    ) -> None:
        """Append a list of records to the current journal file.

        If no journal is open a new one is created.  If the rotation interval
        has elapsed the current journal is first closed and a new one opened.

        Args:
            records: Non-empty list of dicts to write as a record batch.
            dataset_name: Subdirectory name under data_path (default "default").

        Raises:
            ValueError: If records is empty.

        """
        if not records:
            msg = "Cannot write empty batch"
            raise ValueError(msg)

        batch = pa.RecordBatch.from_pylist(records)

        # Capture schema from the first batch; reuse on every subsequent call.
        if self._schema is None:
            self._schema = batch.schema

        # Rotate if the interval has elapsed.
        if self._writer is not None and self._journal_start_time is not None:
            now = datetime.now(self._timezone)
            elapsed = (now - self._journal_start_time).total_seconds()
            if elapsed >= self._rotation_interval_seconds:
                self.rotate()

        # Open a new journal if none is active.
        if self._writer is None:
            self._open_new_journal(dataset_name)

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

    def _open_new_journal(self, dataset_name: str) -> None:
        """Open a new journal file under the hive-style partition path.

        Args:
            dataset_name: Subdirectory name used in the hive path.

        """
        now = datetime.now(self._timezone)
        path = (
            self._data_path
            / dataset_name
            / f"year={now.year}"
            / f"month={now.month:02d}"
            / f"day={now.day:02d}"
            / f"journal_{now.strftime('%H%M%S_%f')}.ipc"
        )
        path.parent.mkdir(parents=True, exist_ok=True)

        sink = pa.OSFile(str(path), "wb")
        self._writer = pa.ipc.new_stream(sink, self._schema)
        self._sink = sink
        self._current_file = path
        self._journal_start_time = now


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_journal_writer(config: dict[str, dict[str, Any]]) -> JournalWriter:
    """Create a JournalWriter from application configuration.

    Args:
        config: Configuration dict from load_config() (e.g. config.toml).
            Reads ``config["storage"]["data_path"]`` (default "data") and
            ``config["storage"]["journal_rotation_minutes"]`` (default 15).

    Returns:
        Configured JournalWriter instance.

    """
    storage_config = config.get("storage", {})
    data_path: str = storage_config.get("data_path", "data")
    rotation_minutes: int = storage_config.get("journal_rotation_minutes", 15)
    rotation_seconds = rotation_minutes * 60
    return JournalWriter(data_path=data_path, rotation_interval_seconds=rotation_seconds)
