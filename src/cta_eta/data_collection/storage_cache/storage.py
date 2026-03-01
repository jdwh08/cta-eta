"""Cloud-agnostic storage abstraction layer for Parquet data files.

Provides unified interface for local filesystem and cloud object storage (S3/GCS),
with timezone-aware daily partitioning optimized for CTA train data collection.

Storage backends:
- LocalStorage: File-based storage using pathlib for development
- CloudStorage: Object storage via fsspec for production (S3/GCS)

"""

from abc import ABC, abstractmethod
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Protocol, Self

import fsspec
import pyarrow as pa


class WritableFile(Protocol):
    """Protocol for append-capable binary write handles."""

    def write(self, data: bytes) -> int | None:
        """Write bytes to the handle."""
        ...

    def flush(self) -> None:
        """Flush buffered writes to storage."""
        ...

    def close(self) -> None:
        """Close the handle."""
        ...

    def __enter__(self) -> Self:
        """Enter the context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """Exit the context manager."""
        self.close()
        return exc_type is None


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

    @abstractmethod
    def open_writer(self, path: str) -> WritableFile:
        """Open a long-lived writable handle for streaming writes.

        Args:
            path: Relative path within storage backend

        Returns:
            Writable file-like handle

        Raises:
            NotImplementedError: If backend does not support streaming writes
            PermissionError: If write access denied
            OSError: If opening the writer fails

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

    def open_writer(self, path: str) -> WritableFile:
        """Open a binary file handle for long-lived streaming writes."""
        file_path = self.base_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        return pa.OSFile(str(file_path), "wb")


class CloudStorage(StorageBackend):
    """Cloud object storage backend using fsspec for S3/GCS access."""

    def __init__(
        self,
        filesystem_type: str,
        bucket: str,
        credentials: dict[str, Any] | None = None,
    ) -> None:
        """Initialize cloud storage backend.

        Args:
            filesystem_type: Type of filesystem ("s3" or "gcs" or "abfs")
            bucket: Bucket name for object storage
            credentials: Optional credentials dict (uses environment variables if None)
                - S3: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY; use
                  client_kwargs={"endpoint_url": "..."} for S3-compatible endpoints
                - GCS: GOOGLE_APPLICATION_CREDENTIALS (path to JSON key file)
                - ABFS: Azure Application Credentials

        Raises:
            ValueError: If filesystem_type is not supported

        """
        cloud_filesystem_types: Final[tuple[str, ...]] = ("s3", "gcs", "abfs")

        if filesystem_type not in cloud_filesystem_types:
            msg = f"Unsupported filesystem type: {filesystem_type}. Expected 's3' or 'gcs' or 'abfs'."
            raise ValueError(msg)

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
            m.removeprefix(bucket_prefix)
            for m in matches
            if m.startswith(bucket_prefix)
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

    def open_writer(self, path: str) -> WritableFile:
        """Open streaming writer for cloud storage (not supported)."""
        msg = (
            "CloudStorage does not support long-lived streaming writers. "
            "Use put() for complete object writes."
        )
        raise NotImplementedError(msg)


def create_storage_backend(config: dict[str, dict[str, Any]]) -> StorageBackend:
    """Create storage backend from configuration.

    Args:
        config: Configuration dict from load_config(). Reads from
            config["storage"]["compaction"]: backend, staging_path (for local),
            and bucket env keys for cloud.

    Returns:
        Configured StorageBackend instance

    Raises:
        ValueError: If backend type is unknown or required config missing

    """
    compaction = config.get("storage", {}).get("compaction", {})
    if not isinstance(compaction, dict):
        compaction = {}
    backend_type = str(compaction.get("backend", "local"))

    match backend_type:
        case "local":
            staging_path = str(compaction.get("staging_path", "data/compaction"))
            return LocalStorage(base_path=Path(staging_path))
        case "s3":
            bucket = compaction.get("s3_bucket")
            if not bucket:
                msg = "s3_bucket must be specified in config for S3 backend"
                raise ValueError(msg)
            credentials: dict[str, Any] | None = None
            endpoint_url = compaction.get("s3_endpoint_url")
            if endpoint_url:
                credentials = {"client_kwargs": {"endpoint_url": endpoint_url}}
            return CloudStorage(
                filesystem_type="s3", bucket=bucket, credentials=credentials
            )
        case "gcs":
            bucket = compaction.get("gcs_bucket")
            if not bucket:
                msg = "gcs_bucket must be specified in config for GCS backend"
                raise ValueError(msg)
            return CloudStorage(filesystem_type="gcs", bucket=bucket)
        case "abfs":
            bucket = compaction.get("azure_bucket")
            if not bucket:
                msg = "azure_bucket must be specified in config for Azure backend"
                raise ValueError(msg)
            return CloudStorage(filesystem_type="abfs", bucket=bucket)
        case _:
            msg = f"Unknown storage backend: {backend_type}. Expected 'local', 's3', or 'gcs', or 'abfs'."
            raise ValueError(msg)
