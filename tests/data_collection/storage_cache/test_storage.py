"""Unit tests for storage abstraction layer."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cta_eta.data_collection.storage_cache.storage import (
    CloudStorage,
    LocalStorage,
    StorageBackend,
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
        assert len(result) == 3
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
        assert len(result) == 2
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
        assert len(result) == 2
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

    def test_local_storage_open_writer_creates_parent_and_writes(
        self, local_storage: LocalStorage
    ) -> None:
        """Test open_writer() creates directories and writes stream bytes."""
        # Arrange
        test_path = "stream/nested/data.bin"
        test_data = b"stream-content"

        # Act
        with local_storage.open_writer(test_path) as writer:
            writer.write(test_data)

        # Assert
        file_path = local_storage.base_path / test_path
        assert file_path.exists()
        assert file_path.read_bytes() == test_data

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
        with patch(
            "cta_eta.data_collection.storage_cache.storage.fsspec.filesystem",
            return_value=mock_filesystem,
        ):
            return CloudStorage(filesystem_type="s3", bucket="test-bucket")

    @pytest.fixture
    def cloud_storage_gcs(self, mock_filesystem: MagicMock) -> CloudStorage:
        """Create CloudStorage instance for GCS."""
        with patch(
            "cta_eta.data_collection.storage_cache.storage.fsspec.filesystem",
            return_value=mock_filesystem,
        ):
            return CloudStorage(filesystem_type="gcs", bucket="test-bucket")

    def test_cloud_storage_init_s3(self, mock_filesystem: MagicMock) -> None:
        """Test CloudStorage initialization for S3."""
        # Arrange
        with patch(
            "cta_eta.data_collection.storage_cache.storage.fsspec.filesystem",
            return_value=mock_filesystem,
        ):
            # Act
            storage = CloudStorage(filesystem_type="s3", bucket="my-bucket")

            # Assert
            assert storage.filesystem_type == "s3"
            assert storage.bucket == "my-bucket"
            assert storage.fs == mock_filesystem

    def test_cloud_storage_init_gcs(self, mock_filesystem: MagicMock) -> None:
        """Test CloudStorage initialization for GCS."""
        # Arrange
        with patch(
            "cta_eta.data_collection.storage_cache.storage.fsspec.filesystem",
            return_value=mock_filesystem,
        ):
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
            "cta_eta.data_collection.storage_cache.storage.fsspec.filesystem",
            return_value=mock_filesystem,
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
        assert len(result) == 2
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
        assert len(result) == 2

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

    def test_cloud_storage_open_writer_unsupported(
        self, cloud_storage_s3: CloudStorage
    ) -> None:
        """Test open_writer() raises for backends without streaming support."""
        with pytest.raises(NotImplementedError, match="does not support"):
            cloud_storage_s3.open_writer("journals/data.ipc")
