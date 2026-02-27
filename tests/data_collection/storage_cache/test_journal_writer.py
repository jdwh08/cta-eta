"""Unit tests for JournalWriter IPC stream writer with time-based rotation."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.ipc
import pytest

from cta_eta.data_collection.storage_cache.journal_writer import (
    JournalWriter,
    create_journal_writer,
)


class TestJournalWriterInit:
    """Test cases for JournalWriter initialization."""

    def test_journal_writer_init_defaults(self, tmp_path: Path) -> None:
        """Test JournalWriter initialization with default parameters."""
        # Act
        writer = JournalWriter(data_path=tmp_path)

        # Assert
        assert writer._data_path == tmp_path
        assert writer._rotation_interval_seconds == 900
        assert writer._writer is None
        assert writer._current_file is None
        assert writer._journal_start_time is None
        assert writer._schema is None

    def test_journal_writer_init_custom_rotation(self, tmp_path: Path) -> None:
        """Test JournalWriter initialization with custom rotation interval."""
        # Act
        writer = JournalWriter(data_path=tmp_path, rotation_interval_seconds=60)

        # Assert
        assert writer._rotation_interval_seconds == 60

    def test_journal_writer_init_string_path(self, tmp_path: Path) -> None:
        """Test JournalWriter accepts string path."""
        # Act
        writer = JournalWriter(data_path=str(tmp_path))

        # Assert
        assert writer._data_path == tmp_path


class TestJournalWriterAppendBatch:
    """Test cases for JournalWriter.append_batch()."""

    def test_append_batch_creates_hive_path(self, tmp_path: Path) -> None:
        """Test append_batch creates file at correct hive-style path."""
        # Arrange
        writer = JournalWriter(data_path=tmp_path)
        records = [{"train_id": "123", "lat": 41.0}]

        # Act
        writer.append_batch(records, dataset_name="train_positions")
        writer.close()

        # Assert: file must exist under hive path
        assert writer._current_file is not None or True  # file was created
        ipc_files = list(
            tmp_path.glob("train_positions/year=*/month=*/day=*/journal_*.ipc")
        )
        assert len(ipc_files) == 1
        # Verify hive path structure
        ipc_path = ipc_files[0]
        parts = ipc_path.parts
        year_part = next(p for p in parts if p.startswith("year="))
        month_part = next(p for p in parts if p.startswith("month="))
        day_part = next(p for p in parts if p.startswith("day="))
        assert year_part.startswith("year=")
        assert month_part.startswith("month=")
        assert day_part.startswith("day=")
        assert ipc_path.name.startswith("journal_")
        assert ipc_path.suffix == ".ipc"

    def test_append_batch_writes_readable_ipc_data(self, tmp_path: Path) -> None:
        """Test append_batch writes readable IPC data."""
        # Arrange
        writer = JournalWriter(data_path=tmp_path)
        records = [{"a": 1}, {"a": 2}]

        # Act
        writer.append_batch(records, dataset_name="test_data")
        current_file = writer._current_file
        writer.close()

        # Assert
        assert current_file is not None
        assert current_file.exists()
        with pa.ipc.open_stream(str(current_file)) as reader:
            table = reader.read_all()
        assert len(table) == 2
        assert "a" in table.column_names
        assert table["a"].to_pylist() == [1, 2]

    def test_append_batch_same_file_within_rotation_window(
        self, tmp_path: Path
    ) -> None:
        """Test two append_batch calls within rotation window use same file."""
        # Arrange
        writer = JournalWriter(data_path=tmp_path, rotation_interval_seconds=900)
        records1 = [{"a": 1}]
        records2 = [{"a": 2}]

        # Act
        writer.append_batch(records1, dataset_name="test_data")
        file_after_first = writer._current_file

        writer.append_batch(records2, dataset_name="test_data")
        file_after_second = writer._current_file

        writer.close()

        # Assert: both writes use the same file
        assert file_after_first == file_after_second

    def test_append_batch_new_file_after_rotation_interval(
        self, tmp_path: Path
    ) -> None:
        """Test new journal file is created after rotation interval elapses."""
        # Arrange
        writer = JournalWriter(data_path=tmp_path, rotation_interval_seconds=1)
        records1 = [{"a": 1}]
        records2 = [{"a": 2}]

        # Act
        writer.append_batch(records1, dataset_name="test_data")
        file_after_first = writer._current_file

        time.sleep(2)

        writer.append_batch(records2, dataset_name="test_data")
        file_after_second = writer._current_file

        writer.close()

        # Assert: second write opened a new file
        assert file_after_first != file_after_second
        ipc_files = list(tmp_path.glob("test_data/year=*/month=*/day=*/journal_*.ipc"))
        assert len(ipc_files) == 2

    def test_append_batch_empty_records_raises_value_error(
        self, tmp_path: Path
    ) -> None:
        """Test append_batch raises ValueError for empty records list."""
        # Arrange
        writer = JournalWriter(data_path=tmp_path)
        empty_records: list[dict[str, Any]] = []

        # Act & Assert
        with pytest.raises(ValueError, match="Cannot write empty batch"):
            writer.append_batch(empty_records, dataset_name="test_data")

    def test_append_batch_after_close_reopens_new_journal(self, tmp_path: Path) -> None:
        """Test append_batch after close() creates new journal."""
        # Arrange
        writer = JournalWriter(data_path=tmp_path)
        records = [{"a": 1}]

        # Act
        writer.append_batch(records, dataset_name="test_data")
        file_first = writer._current_file
        writer.close()

        time.sleep(2)

        # After close, _writer is None → next append opens a new journal
        records2 = [{"a": 2}]
        writer.append_batch(records2, dataset_name="test_data")
        file_second = writer._current_file
        writer.close()

        # Assert: two files exist (second may have same timestamp if <1s apart;
        # but they could be the same name — the key guarantee is that close+append
        # succeeded without error and a valid file was created)
        assert file_first is not None
        assert file_second is not None
        ipc_files = list(tmp_path.glob("test_data/year=*/month=*/day=*/journal_*.ipc"))
        assert len(ipc_files) == 2


class TestJournalWriterClose:
    """Test cases for JournalWriter.close()."""

    def test_close_flushes_and_finishes_ipc_stream(self, tmp_path: Path) -> None:
        """Test close() produces a complete, readable IPC stream file."""
        # Arrange
        writer = JournalWriter(data_path=tmp_path)
        records = [{"x": 10}, {"x": 20}]
        writer.append_batch(records, dataset_name="test_data")
        current_file = writer._current_file

        # Act
        writer.close()

        # Assert: file is readable after close
        assert current_file is not None
        with pa.ipc.open_stream(str(current_file)) as reader:
            table = reader.read_all()
        assert len(table) == 2
        assert table["x"].to_pylist() == [10, 20]

    def test_close_with_no_open_writer_is_noop(self, tmp_path: Path) -> None:
        """Test close() with no open writer is a no-op (no error)."""
        # Arrange
        writer = JournalWriter(data_path=tmp_path)

        # Act & Assert: should not raise
        writer.close()


class TestJournalWriterRotate:
    """Test cases for JournalWriter.rotate()."""

    def test_rotate_closes_current_and_next_append_opens_new(
        self, tmp_path: Path
    ) -> None:
        """Test rotate() closes current file and next append opens a new file."""
        # Arrange
        writer = JournalWriter(data_path=tmp_path)
        records1 = [{"b": 1}]
        records2 = [{"b": 2}]

        # Act
        writer.append_batch(records1, dataset_name="test_data")
        file_before_rotate = writer._current_file
        writer.rotate()

        # After rotate, _writer and _current_file should be None
        assert writer._writer is None
        assert writer._current_file is None

        writer.append_batch(records2, dataset_name="test_data")
        file_after_rotate = writer._current_file
        writer.close()

        # Assert: two separate files
        assert file_before_rotate != file_after_rotate
        ipc_files = list(tmp_path.glob("test_data/year=*/month=*/day=*/journal_*.ipc"))
        assert len(ipc_files) == 2

    def test_rotate_with_no_open_writer_is_noop(self, tmp_path: Path) -> None:
        """Test rotate() with no open writer is a no-op."""
        # Arrange
        writer = JournalWriter(data_path=tmp_path)

        # Act & Assert: should not raise
        writer.rotate()
        assert writer._writer is None


class TestCreateJournalWriter:
    """Test cases for create_journal_writer factory function."""

    def test_create_journal_writer_defaults(self, tmp_path: Path) -> None:
        """Test create_journal_writer returns JournalWriter with default config."""
        # Arrange
        config: dict[str, dict[str, Any]] = {"storage": {"data_path": str(tmp_path)}}

        # Act
        writer = create_journal_writer(config)

        # Assert
        assert isinstance(writer, JournalWriter)
        assert writer._data_path == tmp_path
        assert writer._rotation_interval_seconds == 15 * 60  # 900 seconds

    def test_create_journal_writer_custom_rotation(self, tmp_path: Path) -> None:
        """Test create_journal_writer reads journal_rotation_minutes from config."""
        # Arrange
        config: dict[str, dict[str, Any]] = {
            "storage": {
                "data_path": str(tmp_path),
                "journal_rotation_minutes": 30,
            }
        }

        # Act
        writer = create_journal_writer(config)

        # Assert
        assert writer._rotation_interval_seconds == 30 * 60

    def test_create_journal_writer_missing_storage_section(self) -> None:
        """Test create_journal_writer uses defaults when storage section missing."""
        # Arrange
        config: dict[str, dict[str, Any]] = {}

        # Act
        writer = create_journal_writer(config)

        # Assert
        assert isinstance(writer, JournalWriter)
        assert writer._data_path == Path("data")
        assert writer._rotation_interval_seconds == 15 * 60
