"""Parquet writer with time-based rotation and hive-style paths.

An alternative to JournalWriter for writing Parquet files with timezone-aware partitioning.
This is a simpler implementation which results in many small parquet files,
as opposed to the JournalWriter path which creates many ipc files which are compacted into a single parquet file.

Partitioning:
- Hive-style daily partitions (date=YYYY-MM-DD/)
- Timezone-aware split at partition_hour (default 3:00 AM) in America/Chicago timezone
- Times before partition_hour assigned to previous calendar day
- Times at or after partition_hour assigned to current calendar day
- Hive-style path format: dataset_name/date=YYYY-MM-DD/data_{timestamp}.parquet
"""

import io
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pyarrow as pa
from pyarrow import parquet as pq

from cta_eta.data_collection.storage_cache.storage import (
    StorageBackend,
    create_storage_backend,
)
from cta_eta.data_collection.storage_cache.writer_protocol import DataWriter


class ParquetWriter(DataWriter):
    """Writes train position data to Parquet files with timezone-aware partitioning.

    Partitioning strategy:
    - Daily partitions split at partition_hour (default 3:00 AM) in America/Chicago timezone
    - Times before partition_hour assigned to previous calendar day
    - Times at or after partition_hour assigned to current calendar day
    - Hive-style path format: dataset_name/date=YYYY-MM-DD/data_{timestamp}.parquet
    """

    def __init__(
        self,
        storage_backend: StorageBackend,
        partition_hour: int = 3,
        compression: str = "snappy",
        timezone: str = "America/Chicago",
    ) -> None:
        """Initialize Parquet writer with storage backend and partitioning settings.

        Args:
            storage_backend: Storage backend to write Parquet files to
            partition_hour: Hour in timezone to split days (default 3 for 3:00 AM)
            compression: Parquet compression codec (default "snappy")
            timezone: Timezone for partition calculation (default "America/Chicago")

        """
        self.storage_backend = storage_backend
        self.partition_hour = partition_hour
        self.compression = compression
        self.timezone = ZoneInfo(timezone)

    def _calculate_partition_date(self, timestamp: datetime) -> str:
        """Calculate partition date based on timezone and partition hour.

        Args:
            timestamp: Datetime to partition (assumed UTC if naive)

        Returns:
            Partition date string in YYYY-MM-DD format

        """
        # Convert to timezone-aware UTC if naive
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=ZoneInfo("UTC"))

        # Convert to target timezone
        local_time = timestamp.astimezone(self.timezone)

        # If before partition hour, use previous day
        if local_time.hour < self.partition_hour:
            partition_date = local_time.date() - timedelta(days=1)
        else:
            partition_date = local_time.date()

        return partition_date.isoformat()

    def write(
        self,
        data: list[dict[str, object]],
        dataset_name: str = "default",
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Write records to Parquet with timezone-aware partitioning and optional metadata.

        Args:
            data: List of records as dictionaries
            dataset_name: Name of dataset for organizing files (default "default")
            metadata: Optional metadata dict to attach to Parquet file (stored in schema metadata)

        Raises:
            ValueError: If data is empty
            OSError: If write operation fails

        """
        if not data:
            msg = "Cannot write empty data"
            raise ValueError(msg)

        # Add request timestamp if not present
        current_timestamp = datetime.now(ZoneInfo("UTC"))
        for record in data:
            if "request_timestamp" not in record:
                record["request_timestamp"] = current_timestamp

        # Use first record's timestamp to determine partition
        # (all records in a single write call should have same partition)
        first_timestamp = data[0].get("request_timestamp", current_timestamp)
        if isinstance(first_timestamp, str):
            first_timestamp = datetime.fromisoformat(first_timestamp)

        if not isinstance(first_timestamp, datetime):
            msg = f"Could not convert data timestamp {first_timestamp!s} into datetime."
            raise TypeError(msg)

        partition_date = self._calculate_partition_date(first_timestamp)

        # Generate partition path and timestamp suffix
        timestamp_suffix = current_timestamp.strftime("%Y%m%d_%H%M%S_%f")[:-3]
        if dataset_name == "default":
            partition_path = f"date={partition_date}/data_{timestamp_suffix}.parquet"
        else:
            partition_path = (
                f"{dataset_name}/date={partition_date}/data_{timestamp_suffix}.parquet"
            )

        # Convert data to PyArrow Table
        table = pa.Table.from_pylist(data)

        # Attach metadata to schema if provided
        if metadata is not None:
            # Convert metadata dict to bytes for storage in Parquet metadata
            # PyArrow expects metadata values to be bytes
            metadata_bytes = {
                key: json.dumps(value).encode("utf-8")
                for key, value in metadata.items()
            }
            # Get existing schema metadata or create new
            existing_metadata = table.schema.metadata or {}
            # Merge with new metadata
            combined_metadata = {**existing_metadata, **metadata_bytes}
            # Create new schema with metadata
            table = table.replace_schema_metadata(combined_metadata)

        # Write table to bytes using BytesIO
        buffer = io.BytesIO()
        pq.write_table(table, buffer, compression=self.compression)
        parquet_bytes = buffer.getvalue()

        # Write to storage backend
        self.storage_backend.put(partition_path, parquet_bytes)

    def append_batch(
        self,
        records: list[dict[str, object]],
        dataset_name: str = "default",
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Append a batch of records to Parquet storage with optional metadata.

        Convenience method that wraps write() with a clearer name for batch appending.

        Args:
            records: List of records as dictionaries
            dataset_name: Name of dataset for organizing files (default "default")
            metadata: Optional metadata dict to attach to Parquet file

        Raises:
            ValueError: If records is empty
            OSError: If write operation fails

        """
        self.write(records, dataset_name=dataset_name, metadata=metadata)

    def close(self) -> None:
        """No-op close to align with the shared DataWriter protocol."""
        return


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_parquet_writer(config: dict[str, dict[str, object]]) -> ParquetWriter:
    """Create configured ParquetWriter from configuration.

    Args:
        config: Configuration dict from load_config()

    Returns:
        Configured ParquetWriter instance

    """
    compaction = config.get("storage", {}).get("compaction", {})
    if not isinstance(compaction, dict):
        compaction = {}
    immediate = config.get("storage", {}).get("immediate", {})
    if not isinstance(immediate, dict):
        immediate = {}

    backend = create_storage_backend(config)

    partition_hour_raw = immediate.get("partition_hour", 3)
    partition_hour = (
        partition_hour_raw
        if isinstance(partition_hour_raw, int)
        else int(str(partition_hour_raw))
    )
    compression_raw = compaction.get("compression", "snappy")
    compression = (
        compression_raw if isinstance(compression_raw, str) else str(compression_raw)
    )

    return ParquetWriter(
        storage_backend=backend,
        partition_hour=partition_hour,
        compression=compression,
    )
