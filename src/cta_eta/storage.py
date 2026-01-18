"""Cloud-agnostic storage abstraction layer for Parquet data files.

Provides unified interface for local filesystem and cloud object storage (S3/GCS),
with timezone-aware daily partitioning optimized for CTA train data collection.

Storage backends:
- LocalStorage: File-based storage using pathlib for development
- CloudStorage: Object storage via fsspec for production (S3/GCS)

Partitioning:
- Hive-style daily partitions (date=YYYY-MM-DD/)
- Timezone-aware split at 3:00 AM America/Chicago to minimize splitting active train runs
- Preserves all data points without deduplication (raw collection priority)
"""

import io
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import fsspec
import pyarrow as pa
import pyarrow.parquet as pq


class StorageBackend(ABC):
    """Abstract base class for storage backends supporting local and cloud storage."""

    @abstractmethod
    def put(self, path: str, data: bytes) -> None:
        """Write bytes to storage at the given path.

        Args:
            path: Relative path within storage backend
            data: Bytes to write

        Raises:
            PermissionError: If write access denied
            OSError: If write operation fails
        """
        ...

    @abstractmethod
    def get(self, path: str) -> bytes:
        """Read bytes from storage at the given path.

        Args:
            path: Relative path within storage backend

        Returns:
            Bytes read from storage

        Raises:
            FileNotFoundError: If path does not exist
            PermissionError: If read access denied
            OSError: If read operation fails
        """
        ...

    @abstractmethod
    def list(self, prefix: str) -> list[str]:
        """List all paths matching the given prefix.

        Args:
            prefix: Path prefix to filter results

        Returns:
            List of matching paths

        Raises:
            PermissionError: If list access denied
            OSError: If list operation fails
        """
        ...

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Check if a path exists in storage.

        Args:
            path: Path to check

        Returns:
            True if path exists, False otherwise
        """
        ...


class LocalStorage(StorageBackend):
    """Local filesystem storage backend using pathlib."""

    def __init__(self, base_path: str | Path) -> None:
        """Initialize local storage backend.

        Args:
            base_path: Base directory for all storage operations
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def put(self, path: str, data: bytes) -> None:
        """Write bytes to local file, creating parent directories as needed.

        Args:
            path: Relative path within base_path
            data: Bytes to write

        Raises:
            PermissionError: If write access denied
            OSError: If write operation fails
        """
        file_path = self.base_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(data)

    def get(self, path: str) -> bytes:
        """Read bytes from local file.

        Args:
            path: Relative path within base_path

        Returns:
            Bytes read from file

        Raises:
            FileNotFoundError: If file does not exist
            PermissionError: If read access denied
            OSError: If read operation fails
        """
        file_path = self.base_path / path
        return file_path.read_bytes()

    def list(self, prefix: str) -> list[str]:
        """List all files matching the prefix using glob pattern.

        Args:
            prefix: Path prefix to filter results (supports glob patterns)

        Returns:
            List of matching file paths relative to base_path
        """
        pattern = prefix if prefix else "**/*"
        matches = self.base_path.glob(pattern)
        return [str(p.relative_to(self.base_path)) for p in matches if p.is_file()]

    def exists(self, path: str) -> bool:
        """Check if a file exists in local storage.

        Args:
            path: Relative path within base_path

        Returns:
            True if file exists, False otherwise
        """
        file_path = self.base_path / path
        return file_path.exists()


class CloudStorage(StorageBackend):
    """Cloud object storage backend using fsspec for S3/GCS access."""

    def __init__(
        self, filesystem_type: str, bucket: str, credentials: dict[str, Any] | None = None
    ) -> None:
        """Initialize cloud storage backend.

        Args:
            filesystem_type: Type of filesystem ("s3" or "gcs")
            bucket: Bucket name for object storage
            credentials: Optional credentials dict (uses environment variables if None)
                - S3: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
                - GCS: GOOGLE_APPLICATION_CREDENTIALS (path to JSON key file)

        Raises:
            ValueError: If filesystem_type is not supported
        """
        if filesystem_type not in ("s3", "gcs"):
            raise ValueError(
                f"Unsupported filesystem type: {filesystem_type}. "
                f"Expected 's3' or 'gcs'."
            )

        self.filesystem_type = filesystem_type
        self.bucket = bucket

        # Create fsspec filesystem instance
        # fsspec automatically handles environment variable credentials
        self.fs = fsspec.filesystem(filesystem_type, **(credentials or {}))

    def _get_full_path(self, path: str) -> str:
        """Get full object storage path including bucket.

        Args:
            path: Relative path within bucket

        Returns:
            Full path: bucket/path
        """
        return f"{self.bucket}/{path}"

    def put(self, path: str, data: bytes) -> None:
        """Write bytes to cloud object storage.

        Args:
            path: Relative path within bucket
            data: Bytes to write

        Raises:
            PermissionError: If write access denied
            OSError: If write operation fails
        """
        full_path = self._get_full_path(path)
        with self.fs.open(full_path, "wb") as f:
            f.write(data)

    def get(self, path: str) -> bytes:
        """Read bytes from cloud object storage.

        Args:
            path: Relative path within bucket

        Returns:
            Bytes read from object

        Raises:
            FileNotFoundError: If object does not exist
            PermissionError: If read access denied
            OSError: If read operation fails
        """
        full_path = self._get_full_path(path)
        with self.fs.open(full_path, "rb") as f:
            return f.read()

    def list(self, prefix: str) -> list[str]:
        """List all object keys matching the prefix.

        Args:
            prefix: Path prefix to filter results

        Returns:
            List of matching object keys relative to bucket
        """
        full_prefix = self._get_full_path(prefix)
        matches = self.fs.glob(f"{full_prefix}*")
        # Remove bucket prefix from results
        bucket_prefix = f"{self.bucket}/"
        return [
            m.removeprefix(bucket_prefix) for m in matches if m.startswith(bucket_prefix)
        ]

    def exists(self, path: str) -> bool:
        """Check if an object exists in cloud storage.

        Args:
            path: Relative path within bucket

        Returns:
            True if object exists, False otherwise
        """
        full_path = self._get_full_path(path)
        return self.fs.exists(full_path)


class ParquetWriter:
    """Writes train position data to Parquet files with timezone-aware partitioning.

    Partitioning strategy:
    - Daily partitions split at partition_hour (default 3:00 AM) in America/Chicago timezone
    - Times before partition_hour assigned to previous calendar day
    - Times at or after partition_hour assigned to current calendar day
    - Hive-style path format: date=YYYY-MM-DD/data_{timestamp}.parquet
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

    def write(self, data: list[dict[str, Any]]) -> None:
        """Write train position records to Parquet with timezone-aware partitioning.

        Args:
            data: List of train position records as dictionaries

        Raises:
            ValueError: If data is empty
            OSError: If write operation fails
        """
        if not data:
            raise ValueError("Cannot write empty data")

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

        partition_date = self._calculate_partition_date(first_timestamp)

        # Generate partition path with timestamp suffix
        timestamp_suffix = current_timestamp.strftime("%Y%m%d_%H%M%S_%f")[:-3]
        partition_path = f"date={partition_date}/data_{timestamp_suffix}.parquet"

        # Convert data to PyArrow Table
        table = pa.Table.from_pylist(data)

        # Write table to bytes using BytesIO
        buffer = io.BytesIO()
        pq.write_table(table, buffer, compression=self.compression)
        parquet_bytes = buffer.getvalue()

        # Write to storage backend
        self.storage_backend.put(partition_path, parquet_bytes)
