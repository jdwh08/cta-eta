"""Unit and integration tests for JournalWriter IPC stream writer with time-based rotation.

Tests cover:
- Init: defaults, custom rotation, storage_backend vs data_path mutual exclusion,
  partition_hour and timezone.
- _calculate_partition_date: before/at/after partition hour, naive vs aware, timezone.
- append_batch: hive path, readable IPC, rotation interval, empty records,
  request_timestamp (str/datetime), invalid timestamp type, rotation at partition hour.
- rotate/close: idempotent close, rotate then append.
- create_journal_writer: config defaults, journal_rotation_minutes, partition_hour.

All tests use tmp_path for isolation. Time-dependent behaviour uses pytest-mock
to patch datetime for deterministic partition-hour rotation.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.ipc
import pytest

from cta_eta.data_collection.storage_cache.journal_writer import (
    JournalWriter,
    create_journal_writer,
)
from cta_eta.data_collection.storage_cache.storage import LocalStorage

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


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

    def test_journal_writer_init_with_storage_backend(self, tmp_path: Path) -> None:
        """Test JournalWriter accepts a pre-built storage backend."""
        backend = LocalStorage(base_path=tmp_path)
        writer = JournalWriter(storage_backend=backend)
        assert writer._storage_backend is backend
        assert writer._data_path == tmp_path

    def test_journal_writer_init_both_storage_and_data_path_raises(
        self, tmp_path: Path
    ) -> None:
        """Passing both storage_backend and data_path raises ValueError."""
        backend = LocalStorage(base_path=tmp_path)
        with pytest.raises(
            ValueError, match="Pass either storage_backend or data_path"
        ):
            JournalWriter(storage_backend=backend, data_path=tmp_path)

    def test_journal_writer_init_partition_hour_and_timezone(
        self, tmp_path: Path
    ) -> None:
        """Partition hour and timezone are stored and used for partitioning."""
        writer = JournalWriter(
            data_path=tmp_path,
            partition_hour=5,
            timezone="America/Los_Angeles",
        )
        assert writer._partition_hour == 5
        assert writer._timezone.key == "America/Los_Angeles"


class TestCalculatePartitionDate:
    """Unit tests for _calculate_partition_date (partition hour and timezone)."""

    def test_before_partition_hour_uses_previous_day(self, tmp_path: Path) -> None:
        """Local time 02:59 with partition_hour=3 yields previous calendar day."""
        writer = JournalWriter(
            data_path=tmp_path, partition_hour=3, timezone="America/Chicago"
        )
        # 2026-02-17 08:59 UTC = 02:59 Chicago
        ts = datetime(2026, 2, 17, 8, 59, 0, tzinfo=UTC)
        year, month, day = writer._calculate_partition_date(ts)
        assert (year, month, day) == (2026, 2, 16)

    def test_at_partition_hour_uses_current_day(self, tmp_path: Path) -> None:
        """Local time 03:00 with partition_hour=3 yields current calendar day."""
        writer = JournalWriter(
            data_path=tmp_path, partition_hour=3, timezone="America/Chicago"
        )
        ts = datetime(2026, 2, 17, 9, 0, 0, tzinfo=UTC)
        year, month, day = writer._calculate_partition_date(ts)
        assert (year, month, day) == (2026, 2, 17)

    def test_after_partition_hour_uses_current_day(self, tmp_path: Path) -> None:
        """Local time after partition_hour uses current day."""
        writer = JournalWriter(
            data_path=tmp_path, partition_hour=3, timezone="America/Chicago"
        )
        ts = datetime(2026, 2, 17, 15, 0, 0, tzinfo=UTC)
        year, month, day = writer._calculate_partition_date(ts)
        assert (year, month, day) == (2026, 2, 17)

    def test_naive_timestamp_assumed_utc(self, tmp_path: Path) -> None:
        """Naive datetime is treated as UTC then converted to local."""
        writer = JournalWriter(
            data_path=tmp_path, partition_hour=3, timezone="America/Chicago"
        )
        ts_naive = datetime(2026, 2, 17, 8, 0, 0)  # noqa: DTZ001 — testing naive input
        year, month, day = writer._calculate_partition_date(ts_naive)
        assert (year, month, day) == (2026, 2, 16)

    def test_custom_partition_hour(self, tmp_path: Path) -> None:
        """Custom partition_hour shifts the boundary."""
        writer = JournalWriter(
            data_path=tmp_path, partition_hour=6, timezone="America/Chicago"
        )
        ts_before = datetime(2026, 2, 17, 11, 0, 0, tzinfo=UTC)
        ts_after = datetime(2026, 2, 17, 12, 0, 0, tzinfo=UTC)
        assert writer._calculate_partition_date(ts_before) == (2026, 2, 16)
        assert writer._calculate_partition_date(ts_after) == (2026, 2, 17)


class TestJournalWriterAppendBatch:
    """Test cases for JournalWriter.append_batch()."""

    def test_append_batch_creates_hive_path(self, tmp_path: Path) -> None:
        """Test append_batch creates file at correct hive-style path."""
        # Arrange
        writer = JournalWriter(data_path=tmp_path)
        records = [{"train_id": "123", "lat": 41.0}]

        # Act
        writer.append_batch(records, dataset_name="train_positions")
        file_ref = writer._current_file
        writer.close()

        # Assert: file must exist under hive path
        assert file_ref is not None
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

    def test_append_batch_ignores_metadata_for_api_parity(self, tmp_path: Path) -> None:
        """Test metadata argument is accepted to match DataWriter protocol."""
        writer = JournalWriter(data_path=tmp_path)
        writer.append_batch(
            [{"a": 1}],
            dataset_name="test_data",
            metadata={"cycle_id": "abc123"},
        )
        writer.close()
        ipc_files = list(tmp_path.glob("test_data/year=*/month=*/day=*/journal_*.ipc"))
        assert len(ipc_files) == 1

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

    def test_append_batch_uses_request_timestamp_datetime_for_partition(
        self, tmp_path: Path
    ) -> None:
        """Partition path is derived from first record's request_timestamp when datetime."""
        writer = JournalWriter(
            data_path=tmp_path, partition_hour=3, timezone="America/Chicago"
        )
        ts = datetime(2026, 3, 10, 10, 0, 0, tzinfo=UTC)
        records = [{"a": 1, "request_timestamp": ts}]
        writer.append_batch(records, dataset_name="train_positions")
        writer.close()
        ipc_files = list(
            tmp_path.glob("train_positions/year=2026/month=03/day=10/journal_*.ipc")
        )
        assert len(ipc_files) == 1

    def test_append_batch_uses_request_timestamp_iso_string_for_partition(
        self, tmp_path: Path
    ) -> None:
        """Partition path is derived from first record's request_timestamp when ISO str."""
        writer = JournalWriter(
            data_path=tmp_path, partition_hour=3, timezone="America/Chicago"
        )
        records = [{"a": 1, "request_timestamp": "2026-04-15T14:00:00+00:00"}]
        writer.append_batch(records, dataset_name="weather")
        writer.close()
        ipc_files = list(
            tmp_path.glob("weather/year=2026/month=04/day=15/journal_*.ipc")
        )
        assert len(ipc_files) == 1

    def test_append_batch_invalid_timestamp_type_raises(self, tmp_path: Path) -> None:
        """Non-datetime, non-parseable request_timestamp raises TypeError."""
        writer = JournalWriter(data_path=tmp_path)
        records = [{"a": 1, "request_timestamp": 12345}]
        with pytest.raises(TypeError, match="Could not convert data timestamp"):
            writer.append_batch(records, dataset_name="test_data")

    def test_append_batch_default_dataset_name_no_prefix(self, tmp_path: Path) -> None:
        """dataset_name='default' does not add a 'default/' prefix to path."""
        writer = JournalWriter(data_path=tmp_path)
        records = [{"a": 1}]
        writer.append_batch(records, dataset_name="default")
        current_path = writer._current_relative_path
        writer.close()
        assert current_path is not None
        assert not current_path.startswith("default/")
        ipc_files = list(tmp_path.glob("year=*/month=*/day=*/journal_*.ipc"))
        assert len(ipc_files) == 1

    def test_append_batch_rotates_when_crossing_partition_hour(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """When clock crosses partition_hour (e.g. 2:59 -> 3:01), journal rotates."""
        chicago = ZoneInfo("America/Chicago")
        now_utc_before = datetime(2026, 2, 17, 8, 59, 0, tzinfo=UTC)
        now_local_before = datetime(2026, 2, 17, 2, 59, 0, tzinfo=chicago)
        now_utc_after = datetime(2026, 2, 17, 9, 1, 0, tzinfo=UTC)
        now_local_after = datetime(2026, 2, 17, 3, 1, 0, tzinfo=chicago)
        now_sequence = [
            now_utc_before,
            now_local_before,
            now_local_before,
            now_utc_after,
            now_local_after,
            now_local_after,
            now_local_after,
        ]

        class DatetimeMock(type):
            """Mock datetime type so isinstance(real_dt, mock) is True and now() is controllable."""

            def __instancecheck__(cls, inst: object) -> bool:
                return isinstance(inst, datetime)

            @classmethod
            def now(cls, _tz: tzinfo | None = None) -> datetime:
                return now_sequence.pop(0)

            fromisoformat = staticmethod(datetime.fromisoformat)

        fake_datetime = DatetimeMock("datetime", (), {})
        mocker.patch(
            "cta_eta.data_collection.storage_cache.journal_writer.datetime",
            fake_datetime,
        )
        writer = JournalWriter(
            data_path=tmp_path,
            partition_hour=3,
            timezone="America/Chicago",
            rotation_interval_seconds=3600,
        )
        writer.append_batch(
            [{"a": 1, "request_timestamp": "2026-02-17T08:59:00+00:00"}],
            dataset_name="test_data",
        )
        file_first = writer._current_file
        writer.append_batch(
            [{"a": 2, "request_timestamp": "2026-02-17T09:01:00+00:00"}],
            dataset_name="test_data",
        )
        file_second = writer._current_file
        writer.close()
        assert file_first is not None
        assert file_second is not None
        assert file_first != file_second
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
        config: dict[str, dict[str, Any]] = {
            "storage": {
                "immediate": {"data_path": str(tmp_path), "journal_rotation_minutes": 15, "partition_hour": 3},
            }
        }

        # Act
        writer = create_journal_writer(config)

        # Assert
        assert isinstance(writer, JournalWriter)
        assert isinstance(writer._storage_backend, LocalStorage)
        assert writer._data_path == tmp_path
        assert writer._rotation_interval_seconds == 15 * 60  # 900 seconds

    def test_create_journal_writer_custom_rotation(self, tmp_path: Path) -> None:
        """Test create_journal_writer reads journal_rotation_minutes from config."""
        # Arrange
        config: dict[str, dict[str, Any]] = {
            "storage": {
                "immediate": {
                    "data_path": str(tmp_path),
                    "journal_rotation_minutes": 30,
                    "partition_hour": 3,
                },
            }
        }

        # Act
        writer = create_journal_writer(config)

        # Assert
        assert writer._rotation_interval_seconds == 30 * 60

    def test_create_journal_writer_missing_storage_section(self) -> None:
        """Test create_journal_writer uses defaults when storage.immediate missing."""
        # Arrange
        config: dict[str, dict[str, Any]] = {}

        # Act
        writer = create_journal_writer(config)

        # Assert
        assert isinstance(writer, JournalWriter)
        assert writer._data_path == Path("data/journals")
        assert writer._rotation_interval_seconds == 15 * 60

    def test_create_journal_writer_partition_hour_from_config(
        self, tmp_path: Path
    ) -> None:
        """create_journal_writer reads partition_hour from storage.immediate."""
        config: dict[str, dict[str, Any]] = {
            "storage": {
                "immediate": {
                    "data_path": str(tmp_path),
                    "journal_rotation_minutes": 15,
                    "partition_hour": 5,
                },
            }
        }
        writer = create_journal_writer(config)
        assert writer._partition_hour == 5

    def test_create_journal_writer_integration_append_and_partition(
        self, tmp_path: Path
    ) -> None:
        """Integration: create_journal_writer produces writer that partitions by request_timestamp."""
        config: dict[str, dict[str, Any]] = {
            "storage": {
                "immediate": {
                    "data_path": str(tmp_path),
                    "partition_hour": 3,
                    "journal_rotation_minutes": 15,
                },
            }
        }
        writer = create_journal_writer(config)
        ts = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)
        writer.append_batch(
            [{"station_id": "KORD", "temp": 72.0, "request_timestamp": ts}],
            dataset_name="weather",
        )
        writer.close()
        ipc_files = list(
            tmp_path.glob("weather/year=2026/month=05/day=20/journal_*.ipc")
        )
        assert len(ipc_files) == 1
        with pa.ipc.open_stream(str(ipc_files[0])) as reader:
            table = reader.read_all()
        assert "station_id" in table.column_names
        assert table["station_id"].to_pylist() == ["KORD"]
