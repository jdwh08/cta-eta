"""Cloud-agnostic storage abstraction layer for Parquet data files.

Provides unified interface for local filesystem and cloud object storage (S3/GCS),
with timezone-aware daily partitioning optimized for CTA train data collection.

Storage backends:
- LocalStorage: File-based storage using pathlib for development
- CloudStorage: Object storage via fsspec for production (S3/GCS)
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import fsspec


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
