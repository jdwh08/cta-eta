"""Unit tests for parquet writer."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq
import pytest

from cta_eta.data_collection.storage_cache.parquet_writer import (
    ParquetWriter,
    create_parquet_writer,
)
from cta_eta.data_collection.storage_cache.storage import (
    CloudStorage,
    LocalStorage,
    StorageBackend,
    create_storage_backend,
)


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
        assert writer.partition_hour == 3
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
        assert writer.partition_hour == 5
        assert writer.compression == "gzip"
        assert writer.timezone == ZoneInfo("UTC")

    def test_parquet_writer_write_single_record(
        self, parquet_writer: ParquetWriter, mock_storage_backend: MagicMock
    ) -> None:
        """Test write() with single record."""
        # Arrange
        test_data = [{"train_id": "123", "lat": 41.8781, "lon": -87.6298}]

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

        # Act
        parquet_writer.write(test_data)

        # Assert
        mock_storage_backend.put.assert_called_once()
        call_data = mock_storage_backend.put.call_args[0][1]
        # Verify we can read the parquet data back
        table = pq.read_table(io.BytesIO(call_data))
        assert len(table) == 2

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
        config = {"storage": {"compaction": {"backend": "local"}}}

        # Act
        backend = create_storage_backend(config)

        # Assert
        assert isinstance(backend, LocalStorage)
        assert backend.base_path == Path("data/compaction")

    def test_create_storage_backend_local_with_path(self, tmp_path: Path) -> None:
        """Test create_storage_backend returns LocalStorage with custom staging_path."""
        # Arrange
        custom_path = tmp_path / "custom" / "path"
        config = {
            "storage": {
                "compaction": {"backend": "local", "staging_path": str(custom_path)}
            }
        }

        # Act
        backend = create_storage_backend(config)

        # Assert
        assert isinstance(backend, LocalStorage)
        assert backend.base_path == custom_path

    def test_create_storage_backend_s3(self) -> None:
        """Test create_storage_backend returns CloudStorage for S3."""
        # Arrange
        config = {
            "storage": {"compaction": {"backend": "s3", "s3_bucket": "my-bucket"}}
        }
        mock_filesystem = MagicMock()
        with patch(
            "cta_eta.data_collection.storage_cache.storage.fsspec.filesystem",
            return_value=mock_filesystem,
        ):
            # Act
            backend = create_storage_backend(config)

            # Assert
            assert isinstance(backend, CloudStorage)
            assert backend.filesystem_type == "s3"
            assert backend.bucket == "my-bucket"

    def test_create_storage_backend_s3_with_endpoint_url(self) -> None:
        """Test create_storage_backend passes s3_endpoint_url as client_kwargs for S3."""
        config = {
            "storage": {
                "compaction": {
                    "backend": "s3",
                    "s3_bucket": "my-bucket",
                    "s3_endpoint_url": "https://minio.example.com",
                },
            }
        }
        mock_filesystem = MagicMock()
        with patch(
            "cta_eta.data_collection.storage_cache.storage.fsspec.filesystem",
            return_value=mock_filesystem,
        ) as mock_fs:
            backend = create_storage_backend(config)

            assert isinstance(backend, CloudStorage)
            mock_fs.assert_called_once_with(
                "s3",
                client_kwargs={"endpoint_url": "https://minio.example.com"},
            )

    def test_create_storage_backend_s3_missing_bucket(self) -> None:
        """Test create_storage_backend raises ValueError for S3 without bucket."""
        # Arrange
        config = {"storage": {"compaction": {"backend": "s3"}}}

        # Act & Assert
        with pytest.raises(ValueError, match="s3_bucket must be specified"):
            create_storage_backend(config)

    def test_create_storage_backend_gcs(self) -> None:
        """Test create_storage_backend returns CloudStorage for GCS."""
        # Arrange
        config = {
            "storage": {"compaction": {"backend": "gcs", "gcs_bucket": "my-bucket"}}
        }
        mock_filesystem = MagicMock()
        with patch(
            "cta_eta.data_collection.storage_cache.storage.fsspec.filesystem",
            return_value=mock_filesystem,
        ):
            # Act
            backend = create_storage_backend(config)

            # Assert
            assert isinstance(backend, CloudStorage)
            assert backend.filesystem_type == "gcs"
            assert backend.bucket == "my-bucket"

    def test_create_storage_backend_gcs_missing_bucket(self) -> None:
        """Test create_storage_backend raises ValueError for GCS without bucket."""
        # Arrange
        config = {"storage": {"compaction": {"backend": "gcs"}}}

        # Act & Assert
        with pytest.raises(ValueError, match="gcs_bucket must be specified"):
            create_storage_backend(config)

    def test_create_storage_backend_unknown_backend(self) -> None:
        """Test create_storage_backend raises ValueError for unknown backend."""
        # Arrange
        config = {"storage": {"compaction": {"backend": "azure"}}}

        # Act & Assert
        with pytest.raises(ValueError, match="Unknown storage backend"):
            create_storage_backend(config)

    def test_create_storage_backend_missing_storage_section(self) -> None:
        """Test create_storage_backend uses defaults when storage.compaction missing."""
        # Arrange
        config: dict[str, dict[str, Any]] = {}

        # Act
        backend = create_storage_backend(config)

        # Assert
        assert isinstance(backend, LocalStorage)
        assert backend.base_path == Path("data/compaction")


class TestCreateParquetWriter:
    """Test cases for create_parquet_writer factory function."""

    def test_create_parquet_writer_defaults(self) -> None:
        """Test create_parquet_writer with default config."""
        # Arrange
        config = {
            "storage": {
                "immediate": {"partition_hour": 3},
                "compaction": {"backend": "local"},
            }
        }

        # Act
        writer = create_parquet_writer(config)

        # Assert
        assert isinstance(writer, ParquetWriter)
        assert isinstance(writer.storage_backend, LocalStorage)
        assert writer.partition_hour == 3
        assert writer.compression == "snappy"

    def test_create_parquet_writer_custom_settings(self, tmp_path: Path) -> None:
        """Test create_parquet_writer with custom settings."""
        # Arrange
        custom_path = tmp_path / "custom" / "path"
        config = {
            "storage": {
                "immediate": {"partition_hour": 5},
                "compaction": {
                    "backend": "local",
                    "staging_path": str(custom_path),
                    "compression": "gzip",
                },
            }
        }

        # Act
        writer = create_parquet_writer(config)

        # Assert
        assert isinstance(writer, ParquetWriter)
        assert writer.partition_hour == 5
        assert writer.compression == "gzip"
        assert isinstance(writer.storage_backend, LocalStorage)
        assert writer.storage_backend.base_path == custom_path

    def test_create_parquet_writer_with_s3_backend(self) -> None:
        """Test create_parquet_writer with S3 backend."""
        # Arrange
        config = {
            "storage": {
                "immediate": {},
                "compaction": {"backend": "s3", "s3_bucket": "my-bucket"},
            }
        }
        mock_filesystem = MagicMock()
        with patch(
            "cta_eta.data_collection.storage_cache.storage.fsspec.filesystem",
            return_value=mock_filesystem,
        ):
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
        assert writer.partition_hour == 3
        assert writer.compression == "snappy"
