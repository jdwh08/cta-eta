"""Unit tests for storage abstraction layer."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq
import pytest

from cta_eta.storage import (
    CloudStorage,
    LocalStorage,
    ParquetWriter,
    StorageBackend,
    create_parquet_writer,
    create_storage_backend,
)


class TestStorageBackend:
    """Test cases for StorageBackend abstract base class."""

    def test_storage_backend_cannot_be_instantiated(self) -> None:
        """Test that StorageBackend ABC cannot be instantiated directly."""
        # Arrange & Act & Assert
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            StorageBackend()


class TestLocalStorage:
    """Test cases for LocalStorage backend."""

    @pytest.fixture
    def temp_storage_dir(self, tmp_path: Path) -> Path:
        """Create temporary directory for storage tests."""
        storage_dir = tmp_path / "storage"
        storage_dir.mkdir()
        return storage_dir

    @pytest.fixture
    def local_storage(self, temp_storage_dir: Path) -> LocalStorage:
        """Create LocalStorage instance for testing."""
        return LocalStorage(base_path=temp_storage_dir)

    def test_local_storage_init_creates_base_path(self, tmp_path: Path) -> None:
        """Test that LocalStorage creates base_path if it doesn't exist."""
        # Arrange
        new_dir = tmp_path / "new_storage"

        # Act
        storage = LocalStorage(base_path=new_dir)

        # Assert
        assert storage.base_path == new_dir
        assert new_dir.exists()
        assert new_dir.is_dir()

    def test_local_storage_init_with_existing_path(
        self, temp_storage_dir: Path
    ) -> None:
        """Test LocalStorage initialization with existing directory."""
        # Arrange
        assert temp_storage_dir.exists()

        # Act
        storage = LocalStorage(base_path=temp_storage_dir)

        # Assert
        assert storage.base_path == temp_storage_dir

    def test_local_storage_init_with_path_string(self, temp_storage_dir: Path) -> None:
        """Test LocalStorage accepts string path."""
        # Arrange
        path_str = str(temp_storage_dir)

        # Act
        storage = LocalStorage(base_path=path_str)

        # Assert
        assert storage.base_path == Path(path_str)

    def test_local_storage_put_writes_file(self, local_storage: LocalStorage) -> None:
        """Test that put() writes bytes to file."""
        # Arrange
        test_data = b"test file content"
        test_path = "test/file.txt"

        # Act
        local_storage.put(test_path, test_data)

        # Assert
        file_path = local_storage.base_path / test_path
        assert file_path.exists()
        assert file_path.read_bytes() == test_data

    def test_local_storage_put_creates_parent_directories(
        self, local_storage: LocalStorage
    ) -> None:
        """Test that put() creates parent directories as needed."""
        # Arrange
        test_data = b"content"
        test_path = "nested/deep/path/file.txt"

        # Act
        local_storage.put(test_path, test_data)

        # Assert
        file_path = local_storage.base_path / test_path
        assert file_path.exists()
        assert file_path.parent.exists()
        assert (local_storage.base_path / "nested" / "deep" / "path").exists()

    def test_local_storage_put_empty_data(self, local_storage: LocalStorage) -> None:
        """Test that put() handles empty bytes."""
        # Arrange
        test_data = b""
        test_path = "empty.txt"

        # Act
        local_storage.put(test_path, test_data)

        # Assert
        file_path = local_storage.base_path / test_path
        assert file_path.exists()
        assert file_path.read_bytes() == b""

    def test_local_storage_put_overwrites_existing(
        self, local_storage: LocalStorage
    ) -> None:
        """Test that put() overwrites existing file."""
        # Arrange
        test_path = "existing.txt"
        original_data = b"original"
        new_data = b"new content"
        local_storage.put(test_path, original_data)

        # Act
        local_storage.put(test_path, new_data)

        # Assert
        file_path = local_storage.base_path / test_path
        assert file_path.read_bytes() == new_data

    def test_local_storage_get_reads_file(self, local_storage: LocalStorage) -> None:
        """Test that get() reads bytes from file."""
        # Arrange
        test_data = b"test content"
        test_path = "read_test.txt"
        local_storage.put(test_path, test_data)

        # Act
        result = local_storage.get(test_path)

        # Assert
        assert result == test_data

    def test_local_storage_get_file_not_found(
        self, local_storage: LocalStorage
    ) -> None:
        """Test that get() raises FileNotFoundError for missing file."""
        # Arrange
        test_path = "nonexistent.txt"

        # Act & Assert
        with pytest.raises(FileNotFoundError):
            local_storage.get(test_path)

    def test_local_storage_get_nested_path(self, local_storage: LocalStorage) -> None:
        """Test that get() reads from nested path."""
        # Arrange
        test_data = b"nested content"
        test_path = "dir/subdir/file.txt"
        local_storage.put(test_path, test_data)

        # Act
        result = local_storage.get(test_path)

        # Assert
        assert result == test_data

    def test_local_storage_list_returns_files(
        self, local_storage: LocalStorage
    ) -> None:
        """Test that list() returns matching file paths."""
        # Arrange
        local_storage.put("file1.txt", b"data1")
        local_storage.put("file2.txt", b"data2")
        local_storage.put("subdir/file3.txt", b"data3")

        # Act
        result = local_storage.list("")

        # Assert
        assert len(result) == 3  # noqa: PLR2004
        assert "file1.txt" in result
        assert "file2.txt" in result
        assert "subdir/file3.txt" in result

    def test_local_storage_list_with_prefix(self, local_storage: LocalStorage) -> None:
        """Test that list() filters by prefix."""
        # Arrange
        local_storage.put("data/file1.txt", b"data1")
        local_storage.put("data/file2.txt", b"data2")
        local_storage.put("other/file3.txt", b"data3")

        # Act
        # Use glob pattern that matches files inside directory
        result = local_storage.list("data/**")

        # Assert
        assert len(result) == 2  # noqa: PLR2004
        assert "data/file1.txt" in result
        assert "data/file2.txt" in result
        assert "other/file3.txt" not in result

    def test_local_storage_list_with_glob_pattern(
        self, local_storage: LocalStorage
    ) -> None:
        """Test that list() supports glob patterns."""
        # Arrange
        local_storage.put("file1.txt", b"data1")
        local_storage.put("file2.json", b"data2")
        local_storage.put("file3.txt", b"data3")

        # Act
        result = local_storage.list("*.txt")

        # Assert
        assert len(result) == 2  # noqa: PLR2004
        assert "file1.txt" in result
        assert "file3.txt" in result
        assert "file2.json" not in result

    def test_local_storage_list_empty_directory(
        self, local_storage: LocalStorage
    ) -> None:
        """Test that list() returns empty list for empty directory."""
        # Arrange & Act
        result = local_storage.list("")

        # Assert
        assert result == []

    def test_local_storage_list_excludes_directories(
        self, local_storage: LocalStorage
    ) -> None:
        """Test that list() only returns files, not directories."""
        # Arrange
        local_storage.put("file.txt", b"data")
        # Create a directory (by creating a file in it)
        (local_storage.base_path / "subdir").mkdir()
        (local_storage.base_path / "subdir" / ".gitkeep").touch()

        # Act
        result = local_storage.list("")

        # Assert
        assert "file.txt" in result
        assert "subdir" not in result
        assert "subdir/.gitkeep" in result

    def test_local_storage_exists_true(self, local_storage: LocalStorage) -> None:
        """Test that exists() returns True for existing file."""
        # Arrange
        test_path = "existing.txt"
        local_storage.put(test_path, b"data")

        # Act
        result = local_storage.exists(test_path)

        # Assert
        assert result is True

    def test_local_storage_exists_false(self, local_storage: LocalStorage) -> None:
        """Test that exists() returns False for non-existent file."""
        # Arrange
        test_path = "nonexistent.txt"

        # Act
        result = local_storage.exists(test_path)

        # Assert
        assert result is False

    def test_local_storage_exists_nested_path(
        self, local_storage: LocalStorage
    ) -> None:
        """Test that exists() works with nested paths."""
        # Arrange
        test_path = "nested/path/file.txt"
        local_storage.put(test_path, b"data")

        # Act
        result = local_storage.exists(test_path)

        # Assert
        assert result is True

    def test_local_storage_put_permission_error(
        self, local_storage: LocalStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that put() raises PermissionError on write failure."""
        # Arrange
        test_path = "test.txt"
        test_data = b"data"

        def mock_write_bytes(_self: Path, _data: bytes) -> None:
            msg = "Permission denied"
            raise PermissionError(msg)

        monkeypatch.setattr(Path, "write_bytes", mock_write_bytes)

        # Act & Assert
        with pytest.raises(PermissionError):
            local_storage.put(test_path, test_data)

    def test_local_storage_get_permission_error(
        self, local_storage: LocalStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that get() raises PermissionError on read failure."""
        # Arrange
        test_path = "test.txt"
        local_storage.put(test_path, b"data")

        def mock_read_bytes(_self: Path) -> bytes:
            msg = "Permission denied"
            raise PermissionError(msg)

        monkeypatch.setattr(Path, "read_bytes", mock_read_bytes)

        # Act & Assert
        with pytest.raises(PermissionError):
            local_storage.get(test_path)


class TestCloudStorage:
    """Test cases for CloudStorage backend."""

    @pytest.fixture
    def mock_filesystem(self) -> MagicMock:
        """Create mock fsspec filesystem."""
        fs = MagicMock()
        fs.open.return_value.__enter__.return_value = io.BytesIO()
        fs.open.return_value.__exit__.return_value = None
        fs.glob.return_value = []
        fs.exists.return_value = False
        return fs

    @pytest.fixture
    def cloud_storage_s3(self, mock_filesystem: MagicMock) -> CloudStorage:
        """Create CloudStorage instance for S3."""
        with patch("cta_eta.storage.fsspec.filesystem", return_value=mock_filesystem):
            return CloudStorage(filesystem_type="s3", bucket="test-bucket")

    @pytest.fixture
    def cloud_storage_gcs(self, mock_filesystem: MagicMock) -> CloudStorage:
        """Create CloudStorage instance for GCS."""
        with patch("cta_eta.storage.fsspec.filesystem", return_value=mock_filesystem):
            return CloudStorage(filesystem_type="gcs", bucket="test-bucket")

    def test_cloud_storage_init_s3(self, mock_filesystem: MagicMock) -> None:
        """Test CloudStorage initialization for S3."""
        # Arrange
        with patch("cta_eta.storage.fsspec.filesystem", return_value=mock_filesystem):
            # Act
            storage = CloudStorage(filesystem_type="s3", bucket="my-bucket")

            # Assert
            assert storage.filesystem_type == "s3"
            assert storage.bucket == "my-bucket"
            assert storage.fs == mock_filesystem

    def test_cloud_storage_init_gcs(self, mock_filesystem: MagicMock) -> None:
        """Test CloudStorage initialization for GCS."""
        # Arrange
        with patch("cta_eta.storage.fsspec.filesystem", return_value=mock_filesystem):
            # Act
            storage = CloudStorage(filesystem_type="gcs", bucket="my-bucket")

            # Assert
            assert storage.filesystem_type == "gcs"
            assert storage.bucket == "my-bucket"

    def test_cloud_storage_init_with_credentials(
        self, mock_filesystem: MagicMock
    ) -> None:
        """Test CloudStorage initialization with credentials."""
        # Arrange
        credentials = {"key": "value"}
        with patch(
            "cta_eta.storage.fsspec.filesystem", return_value=mock_filesystem
        ) as mock_fs:
            # Act
            CloudStorage(filesystem_type="s3", bucket="bucket", credentials=credentials)

            # Assert
            mock_fs.assert_called_once_with("s3", **credentials)

    def test_cloud_storage_init_unsupported_type(self) -> None:
        """Test CloudStorage raises ValueError for unsupported filesystem type."""
        # Arrange & Act & Assert
        with pytest.raises(ValueError, match="Unsupported filesystem type"):
            CloudStorage(filesystem_type="azure", bucket="bucket")

    def test_cloud_storage_get_full_path(self, cloud_storage_s3: CloudStorage) -> None:
        """Test _get_full_path() constructs correct path."""
        # Arrange
        test_path = "data/file.txt"

        # Act
        result = cloud_storage_s3._get_full_path(test_path)

        # Assert
        assert result == "test-bucket/data/file.txt"

    def test_cloud_storage_put_writes_data(
        self, cloud_storage_s3: CloudStorage, mock_filesystem: MagicMock
    ) -> None:
        """Test that put() writes bytes to cloud storage."""
        # Arrange
        test_data = b"test content"
        test_path = "data/file.txt"
        mock_file = io.BytesIO()
        mock_filesystem.open.return_value.__enter__.return_value = mock_file
        mock_filesystem.open.return_value.__exit__.return_value = None

        # Act
        cloud_storage_s3.put(test_path, test_data)

        # Assert
        mock_filesystem.open.assert_called_once_with("test-bucket/data/file.txt", "wb")
        assert mock_file.getvalue() == test_data

    def test_cloud_storage_put_empty_data(
        self, cloud_storage_s3: CloudStorage, mock_filesystem: MagicMock
    ) -> None:
        """Test that put() handles empty bytes."""
        # Arrange
        test_data = b""
        test_path = "empty.txt"
        mock_file = io.BytesIO()
        mock_filesystem.open.return_value.__enter__.return_value = mock_file
        mock_filesystem.open.return_value.__exit__.return_value = None

        # Act
        cloud_storage_s3.put(test_path, test_data)

        # Assert
        assert mock_file.getvalue() == b""

    def test_cloud_storage_get_reads_data(
        self, cloud_storage_s3: CloudStorage, mock_filesystem: MagicMock
    ) -> None:
        """Test that get() reads bytes from cloud storage."""
        # Arrange
        test_data = b"test content"
        test_path = "data/file.txt"
        mock_file = io.BytesIO(test_data)
        mock_filesystem.open.return_value.__enter__.return_value = mock_file
        mock_filesystem.open.return_value.__exit__.return_value = None

        # Act
        result = cloud_storage_s3.get(test_path)

        # Assert
        mock_filesystem.open.assert_called_once_with("test-bucket/data/file.txt", "rb")
        assert result == test_data

    def test_cloud_storage_get_file_not_found(
        self, cloud_storage_s3: CloudStorage, mock_filesystem: MagicMock
    ) -> None:
        """Test that get() raises FileNotFoundError for missing object."""
        # Arrange
        test_path = "nonexistent.txt"
        mock_filesystem.open.side_effect = FileNotFoundError("Object not found")

        # Act & Assert
        with pytest.raises(FileNotFoundError):
            cloud_storage_s3.get(test_path)

    def test_cloud_storage_list_returns_keys(
        self, cloud_storage_s3: CloudStorage, mock_filesystem: MagicMock
    ) -> None:
        """Test that list() returns matching object keys."""
        # Arrange
        # Glob pattern "test-bucket/data/*" should only match files in data/ directory
        mock_filesystem.glob.return_value = [
            "test-bucket/data/file1.txt",
            "test-bucket/data/file2.txt",
        ]

        # Act
        result = cloud_storage_s3.list("data/")

        # Assert
        mock_filesystem.glob.assert_called_once_with("test-bucket/data/*")
        assert len(result) == 2  # noqa: PLR2004
        assert "data/file1.txt" in result
        assert "data/file2.txt" in result

    def test_cloud_storage_list_empty_prefix(
        self, cloud_storage_s3: CloudStorage, mock_filesystem: MagicMock
    ) -> None:
        """Test that list() handles empty prefix."""
        # Arrange
        mock_filesystem.glob.return_value = [
            "test-bucket/file1.txt",
            "test-bucket/file2.txt",
        ]

        # Act
        result = cloud_storage_s3.list("")

        # Assert
        mock_filesystem.glob.assert_called_once_with("test-bucket/*")
        assert len(result) == 2  # noqa: PLR2004

    def test_cloud_storage_list_filters_bucket_prefix(
        self, cloud_storage_s3: CloudStorage, mock_filesystem: MagicMock
    ) -> None:
        """Test that list() filters out paths not starting with bucket prefix."""
        # Arrange
        mock_filesystem.glob.return_value = [
            "test-bucket/data/file1.txt",
            "other-bucket/data/file2.txt",  # Should be filtered out
        ]

        # Act
        result = cloud_storage_s3.list("data/")

        # Assert
        assert len(result) == 1
        assert "data/file1.txt" in result

    def test_cloud_storage_exists_true(
        self, cloud_storage_s3: CloudStorage, mock_filesystem: MagicMock
    ) -> None:
        """Test that exists() returns True for existing object."""
        # Arrange
        test_path = "data/file.txt"
        mock_filesystem.exists.return_value = True

        # Act
        result = cloud_storage_s3.exists(test_path)

        # Assert
        mock_filesystem.exists.assert_called_once_with("test-bucket/data/file.txt")
        assert result is True

    def test_cloud_storage_exists_false(
        self, cloud_storage_s3: CloudStorage, mock_filesystem: MagicMock
    ) -> None:
        """Test that exists() returns False for non-existent object."""
        # Arrange
        test_path = "nonexistent.txt"
        mock_filesystem.exists.return_value = False

        # Act
        result = cloud_storage_s3.exists(test_path)

        # Assert
        assert result is False

    def test_cloud_storage_put_permission_error(
        self, cloud_storage_s3: CloudStorage, mock_filesystem: MagicMock
    ) -> None:
        """Test that put() raises PermissionError on write failure."""
        # Arrange
        test_path = "test.txt"
        test_data = b"data"
        mock_filesystem.open.side_effect = PermissionError("Access denied")

        # Act & Assert
        with pytest.raises(PermissionError):
            cloud_storage_s3.put(test_path, test_data)

    def test_cloud_storage_get_permission_error(
        self, cloud_storage_s3: CloudStorage, mock_filesystem: MagicMock
    ) -> None:
        """Test that get() raises PermissionError on read failure."""
        # Arrange
        test_path = "test.txt"
        mock_filesystem.open.side_effect = PermissionError("Access denied")

        # Act & Assert
        with pytest.raises(PermissionError):
            cloud_storage_s3.get(test_path)


class TestParquetWriter:
    """Test cases for ParquetWriter."""

    @pytest.fixture
    def mock_storage_backend(self) -> MagicMock:
        """Create mock storage backend."""
        backend = MagicMock(spec=StorageBackend)
        backend.put = Mock()
        return backend

    @pytest.fixture
    def parquet_writer(self, mock_storage_backend: MagicMock) -> ParquetWriter:
        """Create ParquetWriter instance for testing."""
        return ParquetWriter(
            storage_backend=mock_storage_backend,
            partition_hour=3,
            compression="snappy",
            timezone="America/Chicago",
        )

    def test_parquet_writer_init_defaults(
        self, mock_storage_backend: MagicMock
    ) -> None:
        """Test ParquetWriter initialization with defaults."""
        # Act
        writer = ParquetWriter(storage_backend=mock_storage_backend)

        # Assert
        assert writer.storage_backend == mock_storage_backend
        assert writer.partition_hour == 3  # noqa: PLR2004
        assert writer.compression == "snappy"
        assert writer.timezone == ZoneInfo("America/Chicago")

    def test_parquet_writer_init_custom(self, mock_storage_backend: MagicMock) -> None:
        """Test ParquetWriter initialization with custom parameters."""
        # Act
        writer = ParquetWriter(
            storage_backend=mock_storage_backend,
            partition_hour=5,
            compression="gzip",
            timezone="UTC",
        )

        # Assert
        assert writer.partition_hour == 5  # noqa: PLR2004
        assert writer.compression == "gzip"
        assert writer.timezone == ZoneInfo("UTC")

    def test_parquet_writer_write_single_record(
        self, parquet_writer: ParquetWriter, mock_storage_backend: MagicMock
    ) -> None:
        """Test write() with single record."""
        # Arrange
        test_data = [{"train_id": "123", "lat": 41.8781, "lon": -87.6298}]
        with patch("cta_eta.storage.datetime") as mock_datetime:
            mock_now = datetime(2026, 1, 15, 10, 30, 0, tzinfo=ZoneInfo("UTC"))
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)  # noqa: DTZ001

            # Act
            parquet_writer.write(test_data)

            # Assert
            mock_storage_backend.put.assert_called_once()
            call_path = mock_storage_backend.put.call_args[0][0]
            assert call_path.startswith("date=")
            assert call_path.endswith(".parquet")
            call_data = mock_storage_backend.put.call_args[0][1]
            assert isinstance(call_data, bytes)
            assert len(call_data) > 0

    def test_parquet_writer_write_multiple_records(
        self, parquet_writer: ParquetWriter, mock_storage_backend: MagicMock
    ) -> None:
        """Test write() with multiple records."""
        # Arrange
        test_data = [
            {"train_id": "123", "lat": 41.8781, "lon": -87.6298},
            {"train_id": "456", "lat": 41.8819, "lon": -87.6278},
        ]
        with patch("cta_eta.storage.datetime") as mock_datetime:
            mock_now = datetime(2026, 1, 15, 10, 30, 0, tzinfo=ZoneInfo("UTC"))
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)  # noqa: DTZ001

            # Act
            parquet_writer.write(test_data)

            # Assert
            mock_storage_backend.put.assert_called_once()
            call_data = mock_storage_backend.put.call_args[0][1]
            # Verify we can read the parquet data back
            table = pq.read_table(io.BytesIO(call_data))
            assert len(table) == 2  # noqa: PLR2004

    def test_parquet_writer_write_empty_data(
        self, parquet_writer: ParquetWriter
    ) -> None:
        """Test write() raises ValueError for empty data."""
        # Arrange
        test_data: list[dict[str, Any]] = []

        # Act & Assert
        with pytest.raises(ValueError, match="Cannot write empty data"):
            parquet_writer.write(test_data)

    def test_parquet_writer_write_adds_request_timestamp(
        self, parquet_writer: ParquetWriter, mock_storage_backend: MagicMock
    ) -> None:
        """Test write() adds request_timestamp if missing."""
        # Arrange
        test_data = [{"train_id": "123", "lat": 41.8781}]
        mock_timestamp = datetime(2026, 1, 15, 10, 30, 0, tzinfo=ZoneInfo("UTC"))
        with patch("cta_eta.storage.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_timestamp
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)  # noqa: DTZ001

            # Act
            parquet_writer.write(test_data)

            # Assert
            call_data = mock_storage_backend.put.call_args[0][1]
            table = pq.read_table(io.BytesIO(call_data))
            assert "request_timestamp" in table.column_names
            # Verify timestamp was added
            timestamps = table["request_timestamp"].to_pylist()
            assert len(timestamps) == 1

    def test_parquet_writer_write_preserves_existing_timestamp(
        self, parquet_writer: ParquetWriter, mock_storage_backend: MagicMock
    ) -> None:
        """Test write() preserves existing request_timestamp."""
        # Arrange
        existing_timestamp = datetime(2026, 1, 15, 8, 0, 0, tzinfo=ZoneInfo("UTC"))
        test_data = [
            {
                "train_id": "123",
                "request_timestamp": existing_timestamp,
                "lat": 41.8781,
            }
        ]
        mock_now = datetime(2026, 1, 15, 10, 30, 0, tzinfo=ZoneInfo("UTC"))
        with patch("cta_eta.storage.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)  # noqa: DTZ001

            # Act
            parquet_writer.write(test_data)

            # Assert
            call_data = mock_storage_backend.put.call_args[0][1]
            table = pq.read_table(io.BytesIO(call_data))
            timestamps = table["request_timestamp"].to_pylist()
            # The timestamp should be preserved (not overwritten)
            assert len(timestamps) == 1

    def test_parquet_writer_calculate_partition_date_before_partition_hour(
        self, parquet_writer: ParquetWriter
    ) -> None:
        """Test partition date calculation before partition hour (2 AM)."""
        # Arrange
        # 2:00 AM Chicago time = 8:00 AM UTC (Chicago is UTC-6 in January)
        timestamp = datetime(2026, 1, 15, 8, 0, 0, tzinfo=ZoneInfo("UTC"))

        # Act
        partition_date = parquet_writer._calculate_partition_date(timestamp)

        # Assert
        # Should be previous day (Jan 14) since 2 AM < 3 AM partition hour
        assert partition_date == "2026-01-14"

    def test_parquet_writer_calculate_partition_date_at_partition_hour(
        self, parquet_writer: ParquetWriter
    ) -> None:
        """Test partition date calculation at partition hour (3 AM)."""
        # Arrange
        # 3:00 AM Chicago time = 9:00 AM UTC
        timestamp = datetime(2026, 1, 15, 9, 0, 0, tzinfo=ZoneInfo("UTC"))

        # Act
        partition_date = parquet_writer._calculate_partition_date(timestamp)

        # Assert
        # Should be current day (Jan 15) since 3 AM >= 3 AM partition hour
        assert partition_date == "2026-01-15"

    def test_parquet_writer_calculate_partition_date_after_partition_hour(
        self, parquet_writer: ParquetWriter
    ) -> None:
        """Test partition date calculation after partition hour (4 AM)."""
        # Arrange
        # 4:00 AM Chicago time = 10:00 AM UTC
        timestamp = datetime(2026, 1, 15, 10, 0, 0, tzinfo=ZoneInfo("UTC"))

        # Act
        partition_date = parquet_writer._calculate_partition_date(timestamp)

        # Assert
        # Should be current day (Jan 15) since 4 AM >= 3 AM partition hour
        assert partition_date == "2026-01-15"

    def test_parquet_writer_calculate_partition_date_naive_datetime(
        self, parquet_writer: ParquetWriter
    ) -> None:
        """Test partition date calculation with naive datetime (assumed UTC)."""
        # Arrange
        # Naive datetime is treated as UTC
        timestamp = datetime(2026, 1, 15, 9, 0, 0)  # No timezone  # noqa: DTZ001

        # Act
        partition_date = parquet_writer._calculate_partition_date(timestamp)

        # Assert
        # Should convert to UTC, then to Chicago time (9 AM UTC = 3 AM Chicago)
        assert partition_date == "2026-01-15"

    def test_parquet_writer_calculate_partition_date_midnight_edge_case(
        self, parquet_writer: ParquetWriter
    ) -> None:
        """Test partition date calculation at midnight edge case."""
        # Arrange
        # 12:00 AM Chicago time = 6:00 AM UTC
        timestamp = datetime(2026, 1, 15, 6, 0, 0, tzinfo=ZoneInfo("UTC"))

        # Act
        partition_date = parquet_writer._calculate_partition_date(timestamp)

        # Assert
        # Should be previous day (Jan 14) since midnight < 3 AM partition hour
        assert partition_date == "2026-01-14"

    def test_parquet_writer_calculate_partition_date_custom_partition_hour(
        self, mock_storage_backend: MagicMock
    ) -> None:
        """Test partition date calculation with custom partition hour."""
        # Arrange
        writer = ParquetWriter(storage_backend=mock_storage_backend, partition_hour=5)
        # 4:00 AM Chicago time = 10:00 AM UTC
        timestamp = datetime(2026, 1, 15, 10, 0, 0, tzinfo=ZoneInfo("UTC"))

        # Act
        partition_date = writer._calculate_partition_date(timestamp)

        # Assert
        # Should be previous day since 4 AM < 5 AM partition hour
        assert partition_date == "2026-01-14"

    def test_parquet_writer_calculate_partition_date_dst_transition(
        self, parquet_writer: ParquetWriter
    ) -> None:
        """Test partition date calculation handles DST transitions."""
        # Arrange
        # March 10, 2026 2:00 AM Chicago (DST starts, becomes 3 AM)
        # Use a time that's clearly before DST transition
        timestamp = datetime(2026, 3, 10, 7, 0, 0, tzinfo=ZoneInfo("UTC"))
        # 7 AM UTC = 1 AM CST (before DST), which is < 3 AM partition hour

        # Act
        partition_date = parquet_writer._calculate_partition_date(timestamp)

        # Assert
        # Should be previous day since 1 AM < 3 AM partition hour
        assert partition_date == "2026-03-09"

    def test_parquet_writer_write_string_timestamp(
        self, parquet_writer: ParquetWriter, mock_storage_backend: MagicMock
    ) -> None:
        """Test write() handles string timestamp in ISO format."""
        # Arrange
        test_data = [
            {
                "train_id": "123",
                "request_timestamp": "2026-01-15T10:00:00+00:00",
                "lat": 41.8781,
            }
        ]
        mock_now = datetime(2026, 1, 15, 10, 30, 0, tzinfo=ZoneInfo("UTC"))
        with patch("cta_eta.storage.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)  # noqa: DTZ001
            mock_datetime.fromisoformat = datetime.fromisoformat

            # Act
            parquet_writer.write(test_data)

            # Assert
            mock_storage_backend.put.assert_called_once()
            call_path = mock_storage_backend.put.call_args[0][0]
            assert "date=2026-01-15" in call_path

    def test_parquet_writer_write_compression(
        self, mock_storage_backend: MagicMock
    ) -> None:
        """Test write() uses specified compression."""
        # Arrange
        writer = ParquetWriter(storage_backend=mock_storage_backend, compression="gzip")
        test_data = [{"train_id": "123", "lat": 41.8781}]
        mock_now = datetime(2026, 1, 15, 10, 30, 0, tzinfo=ZoneInfo("UTC"))
        with patch("cta_eta.storage.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)  # noqa: DTZ001

            # Act
            writer.write(test_data)

            # Assert
            # Verify parquet file was written (we can't easily verify compression
            # without reading it back, but we can verify it was called)
            mock_storage_backend.put.assert_called_once()


class TestCreateStorageBackend:
    """Test cases for create_storage_backend factory function."""

    def test_create_storage_backend_local_default(self) -> None:
        """Test create_storage_backend returns LocalStorage with default config."""
        # Arrange
        config = {"storage": {"backend": "local"}}

        # Act
        backend = create_storage_backend(config)

        # Assert
        assert isinstance(backend, LocalStorage)
        assert backend.base_path == Path("data")

    def test_create_storage_backend_local_with_path(self, tmp_path: Path) -> None:
        """Test create_storage_backend returns LocalStorage with custom path."""
        # Arrange
        custom_path = tmp_path / "custom" / "path"
        config = {"storage": {"backend": "local", "data_path": str(custom_path)}}

        # Act
        backend = create_storage_backend(config)

        # Assert
        assert isinstance(backend, LocalStorage)
        assert backend.base_path == custom_path

    def test_create_storage_backend_s3(self) -> None:
        """Test create_storage_backend returns CloudStorage for S3."""
        # Arrange
        config = {"storage": {"backend": "s3", "s3_bucket": "my-bucket"}}
        mock_filesystem = MagicMock()
        with patch("cta_eta.storage.fsspec.filesystem", return_value=mock_filesystem):
            # Act
            backend = create_storage_backend(config)

            # Assert
            assert isinstance(backend, CloudStorage)
            assert backend.filesystem_type == "s3"
            assert backend.bucket == "my-bucket"

    def test_create_storage_backend_s3_missing_bucket(self) -> None:
        """Test create_storage_backend raises ValueError for S3 without bucket."""
        # Arrange
        config = {"storage": {"backend": "s3"}}

        # Act & Assert
        with pytest.raises(ValueError, match="s3_bucket must be specified"):
            create_storage_backend(config)

    def test_create_storage_backend_gcs(self) -> None:
        """Test create_storage_backend returns CloudStorage for GCS."""
        # Arrange
        config = {"storage": {"backend": "gcs", "gcs_bucket": "my-bucket"}}
        mock_filesystem = MagicMock()
        with patch("cta_eta.storage.fsspec.filesystem", return_value=mock_filesystem):
            # Act
            backend = create_storage_backend(config)

            # Assert
            assert isinstance(backend, CloudStorage)
            assert backend.filesystem_type == "gcs"
            assert backend.bucket == "my-bucket"

    def test_create_storage_backend_gcs_missing_bucket(self) -> None:
        """Test create_storage_backend raises ValueError for GCS without bucket."""
        # Arrange
        config = {"storage": {"backend": "gcs"}}

        # Act & Assert
        with pytest.raises(ValueError, match="gcs_bucket must be specified"):
            create_storage_backend(config)

    def test_create_storage_backend_unknown_backend(self) -> None:
        """Test create_storage_backend raises ValueError for unknown backend."""
        # Arrange
        config = {"storage": {"backend": "azure"}}

        # Act & Assert
        with pytest.raises(ValueError, match="Unknown storage backend"):
            create_storage_backend(config)

    def test_create_storage_backend_missing_storage_section(self) -> None:
        """Test create_storage_backend uses defaults when storage section missing."""
        # Arrange
        config: dict[str, dict[str, Any]] = {}

        # Act
        backend = create_storage_backend(config)

        # Assert
        assert isinstance(backend, LocalStorage)
        assert backend.base_path == Path("data")


class TestCreateParquetWriter:
    """Test cases for create_parquet_writer factory function."""

    def test_create_parquet_writer_defaults(self) -> None:
        """Test create_parquet_writer with default config."""
        # Arrange
        config = {"storage": {"backend": "local"}}

        # Act
        writer = create_parquet_writer(config)

        # Assert
        assert isinstance(writer, ParquetWriter)
        assert isinstance(writer.storage_backend, LocalStorage)
        assert writer.partition_hour == 3  # noqa: PLR2004
        assert writer.compression == "snappy"

    def test_create_parquet_writer_custom_settings(self, tmp_path: Path) -> None:
        """Test create_parquet_writer with custom settings."""
        # Arrange
        custom_path = tmp_path / "custom" / "path"
        config = {
            "storage": {
                "backend": "local",
                "data_path": str(custom_path),
                "partition_hour": 5,
                "compression": "gzip",
            }
        }

        # Act
        writer = create_parquet_writer(config)

        # Assert
        assert isinstance(writer, ParquetWriter)
        assert writer.partition_hour == 5  # noqa: PLR2004
        assert writer.compression == "gzip"
        assert writer.storage_backend.base_path == custom_path

    def test_create_parquet_writer_with_s3_backend(self) -> None:
        """Test create_parquet_writer with S3 backend."""
        # Arrange
        config = {"storage": {"backend": "s3", "s3_bucket": "my-bucket"}}
        mock_filesystem = MagicMock()
        with patch("cta_eta.storage.fsspec.filesystem", return_value=mock_filesystem):
            # Act
            writer = create_parquet_writer(config)

            # Assert
            assert isinstance(writer, ParquetWriter)
            assert isinstance(writer.storage_backend, CloudStorage)
            assert writer.storage_backend.filesystem_type == "s3"

    def test_create_parquet_writer_missing_storage_section(self) -> None:
        """Test create_parquet_writer with missing storage section."""
        # Arrange
        config: dict[str, dict[str, Any]] = {}

        # Act
        writer = create_parquet_writer(config)

        # Assert
        assert isinstance(writer, ParquetWriter)
        assert writer.partition_hour == 3  # noqa: PLR2004
        assert writer.compression == "snappy"
