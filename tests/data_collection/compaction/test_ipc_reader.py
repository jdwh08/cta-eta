"""TDD tests for IPC reader: file discovery + partial repair + schema validation.

Tests cover all 5 read_ipc_with_repair cases:
1. Normal closed file (EOS marker present) -> (all batches, True)
2. Crash file (no EOS marker, missing close()) -> (all batches, True)
3. File with corrupt trailing bytes after valid batches -> (valid batches, False)
4. File with corrupt header (schema unreadable) -> ([], False)
5. Empty file -> ([], False)

Tests cover all 4 discover_journals cases:
1. Existing directory with multiple journal files -> sorted list of paths
2. Non-existent directory -> []
3. Directory with no .ipc files -> []
4. Mixed files (.ipc + non-.ipc) -> only journal_*.ipc files

Tests cover schema validation:
1. TRAIN_POSITION_SCHEMA constant exists and is a valid pyarrow Schema
2. WEATHER_SCHEMA constant exists and is a valid pyarrow Schema
3. Schema validation correctly passes matching schemas
4. Schema validation correctly rejects mismatches
"""

from __future__ import annotations

import struct
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from cta_eta.data_collection.compaction.ipc_reader import (
    discover_journals,
    read_ipc_with_repair,
)
from cta_eta.data_collection.compaction.schemas import (
    TRAIN_POSITION_SCHEMA,
    WEATHER_SCHEMA,
)


# ---------------------------------------------------------------------------
# Helpers: IPC file factories
# ---------------------------------------------------------------------------


def _make_schema() -> pa.Schema:
    """Create a simple schema for testing."""
    return pa.schema(
        [
            ("name", pa.string()),
            ("value", pa.int64()),
        ]
    )


def _make_batch(n: int = 3) -> pa.RecordBatch:
    """Create a sample record batch."""
    schema = _make_schema()
    return pa.record_batch(
        {
            "name": [f"item_{i}" for i in range(n)],
            "value": list(range(n)),
        },
        schema=schema,
    )


def _write_clean_ipc(path: Path, n_batches: int = 2, rows_per_batch: int = 3) -> int:
    """Write a clean IPC file with EOS marker. Returns total row count."""
    schema = _make_schema()
    sink = pa.OSFile(str(path), "wb")
    writer = ipc.new_stream(sink, schema)
    total_rows = 0
    for _ in range(n_batches):
        batch = _make_batch(rows_per_batch)
        writer.write_batch(batch)
        total_rows += rows_per_batch
    writer.close()
    sink.close()
    return total_rows


def _write_crash_ipc(path: Path, n_batches: int = 2, rows_per_batch: int = 3) -> int:
    """Write an IPC file without close() (simulates daemon crash). Returns row count."""
    schema = _make_schema()
    sink = pa.OSFile(str(path), "wb")
    writer = ipc.new_stream(sink, schema)
    total_rows = 0
    for _ in range(n_batches):
        batch = _make_batch(rows_per_batch)
        writer.write_batch(batch)
        total_rows += rows_per_batch
    # Intentionally NOT calling writer.close() / sink.close() to simulate crash
    # Force flush by closing the underlying sink
    sink.close()
    return total_rows


def _write_corrupt_trailing_ipc(
    path: Path, n_batches: int = 2, rows_per_batch: int = 3
) -> int:
    """Write an IPC file with corrupt trailing bytes. Returns valid row count."""
    schema = _make_schema()
    sink = pa.OSFile(str(path), "wb")
    writer = ipc.new_stream(sink, schema)
    total_rows = 0
    for _ in range(n_batches):
        batch = _make_batch(rows_per_batch)
        writer.write_batch(batch)
        total_rows += rows_per_batch
    writer.close()
    sink.close()
    # Append corrupt bytes after the EOS marker
    with open(path, "ab") as f:
        f.write(b"\xff\xfe\x00corrupt\x00bytes\x00here")
    return total_rows


def _write_corrupt_header_ipc(path: Path) -> None:
    """Write a file with a corrupt IPC header (unreadable schema)."""
    # Write just garbage bytes — not a valid IPC stream header
    path.write_bytes(b"\x00\x01\x02\x03GARBAGE_NOT_IPC_FORMAT\xff\xfe")


# ---------------------------------------------------------------------------
# Tests: Schema constants
# ---------------------------------------------------------------------------


class TestSchemaConstants:
    """Tests for TRAIN_POSITION_SCHEMA and WEATHER_SCHEMA constants."""

    def test_train_position_schema_is_pa_schema(self) -> None:
        """TRAIN_POSITION_SCHEMA must be a pyarrow Schema instance."""
        assert isinstance(TRAIN_POSITION_SCHEMA, pa.Schema)

    def test_weather_schema_is_pa_schema(self) -> None:
        """WEATHER_SCHEMA must be a pyarrow Schema instance."""
        assert isinstance(WEATHER_SCHEMA, pa.Schema)

    def test_train_position_schema_has_required_fields(self) -> None:
        """TRAIN_POSITION_SCHEMA must include all train daemon record fields."""
        field_names = TRAIN_POSITION_SCHEMA.names
        required_fields = [
            "poll_timestamp",
            "api_timestamp",
            "route",
            "train_id",
            "lat",
            "lon",
            "heading",
            "next_station_id",
            "next_station_name",
            "destination_id",
            "destination_name",
            "prediction_time",
            "predicted_arrival_time",
            "is_approaching",
            "is_delayed",
        ]
        for field in required_fields:
            assert field in field_names, f"Missing field: {field}"

    def test_weather_schema_has_required_fields(self) -> None:
        """WEATHER_SCHEMA must include all weather daemon record fields."""
        field_names = WEATHER_SCHEMA.names
        required_fields = [
            "station_id",
            "nws_grid_id",
            "open_meteo_grid_id",
            "latitude",
            "longitude",
            "collection_timestamp",
            # NWS fields
            "temperature_f",
            "humidity_pct",
            "wind_speed_mph",
            # Open-Meteo fields
            "visibility_mi",
            "snow_depth_in",
            "surface_pressure_hpa",
            "wind_gusts_mph",
            "apparent_temp_f",
        ]
        for field in required_fields:
            assert field in field_names, f"Missing field: {field}"

    def test_train_position_schema_type_correctness(self) -> None:
        """Key fields in TRAIN_POSITION_SCHEMA must have correct pyarrow types."""
        schema = TRAIN_POSITION_SCHEMA
        # poll_timestamp should be timestamp type (not string)
        assert pa.types.is_timestamp(schema.field("poll_timestamp").type), (
            f"poll_timestamp should be timestamp, got {schema.field('poll_timestamp').type}"
        )
        # lat/lon must be float
        assert pa.types.is_floating(schema.field("lat").type), (
            f"lat should be float, got {schema.field('lat').type}"
        )
        assert pa.types.is_floating(schema.field("lon").type), (
            f"lon should be float, got {schema.field('lon').type}"
        )
        # is_approaching/is_delayed must be boolean
        assert pa.types.is_boolean(schema.field("is_approaching").type), (
            f"is_approaching should be bool, got {schema.field('is_approaching').type}"
        )
        assert pa.types.is_boolean(schema.field("is_delayed").type), (
            f"is_delayed should be bool, got {schema.field('is_delayed').type}"
        )
        # heading should be integer
        assert pa.types.is_integer(schema.field("heading").type), (
            f"heading should be int, got {schema.field('heading').type}"
        )

    def test_schema_matching_passes(self) -> None:
        """Schema equals check must pass for identical schemas."""
        schema_a = pa.schema([("x", pa.int64()), ("y", pa.string())])
        schema_b = pa.schema([("x", pa.int64()), ("y", pa.string())])
        assert schema_a.equals(schema_b)

    def test_schema_mismatch_detected(self) -> None:
        """Schema equals check must fail for different schemas."""
        schema_a = pa.schema([("x", pa.int64())])
        schema_b = pa.schema([("x", pa.string())])
        assert not schema_a.equals(schema_b)


# ---------------------------------------------------------------------------
# Tests: discover_journals
# ---------------------------------------------------------------------------


class TestDiscoverJournals:
    """Tests for discover_journals() file discovery function."""

    def test_returns_sorted_list_of_journal_files(self, tmp_path: Path) -> None:
        """Returns sorted list of journal_*.ipc files when they exist."""
        target_date = date(2026, 2, 17)
        day_dir = (
            tmp_path
            / "train_positions"
            / "year=2026"
            / "month=02"
            / "day=17"
        )
        day_dir.mkdir(parents=True)
        # Create 3 journal files in reverse order
        (day_dir / "journal_235900_000001.ipc").touch()
        (day_dir / "journal_000000_000001.ipc").touch()
        (day_dir / "journal_120000_000001.ipc").touch()

        result = discover_journals(tmp_path, "train_positions", target_date)

        assert len(result) == 3
        # Must be sorted by filename (chronological order)
        assert result[0].name == "journal_000000_000001.ipc"
        assert result[1].name == "journal_120000_000001.ipc"
        assert result[2].name == "journal_235900_000001.ipc"

    def test_returns_empty_list_for_missing_directory(self, tmp_path: Path) -> None:
        """Returns empty list when the target date directory does not exist."""
        target_date = date(2026, 2, 17)
        result = discover_journals(tmp_path, "train_positions", target_date)
        assert result == []

    def test_returns_empty_list_for_no_ipc_files(self, tmp_path: Path) -> None:
        """Returns empty list when directory exists but has no .ipc files."""
        target_date = date(2026, 2, 17)
        day_dir = (
            tmp_path
            / "train_positions"
            / "year=2026"
            / "month=02"
            / "day=17"
        )
        day_dir.mkdir(parents=True)
        # Create non-IPC files
        (day_dir / "notes.txt").touch()
        (day_dir / "README.md").touch()

        result = discover_journals(tmp_path, "train_positions", target_date)
        assert result == []

    def test_returns_only_journal_ipc_files(self, tmp_path: Path) -> None:
        """Returns only journal_*.ipc files, excluding other file types."""
        target_date = date(2026, 2, 17)
        day_dir = (
            tmp_path
            / "train_positions"
            / "year=2026"
            / "month=02"
            / "day=17"
        )
        day_dir.mkdir(parents=True)
        (day_dir / "journal_100000_000001.ipc").touch()
        (day_dir / "notes.txt").touch()
        (day_dir / "data.parquet").touch()
        (day_dir / "other.ipc").touch()  # .ipc but not journal_* prefix

        result = discover_journals(tmp_path, "train_positions", target_date)

        assert len(result) == 1
        assert result[0].name == "journal_100000_000001.ipc"

    def test_returns_paths_not_strings(self, tmp_path: Path) -> None:
        """Returns list of Path objects, not strings."""
        target_date = date(2026, 2, 17)
        day_dir = (
            tmp_path
            / "train_positions"
            / "year=2026"
            / "month=02"
            / "day=17"
        )
        day_dir.mkdir(parents=True)
        (day_dir / "journal_100000_000001.ipc").touch()

        result = discover_journals(tmp_path, "train_positions", target_date)

        assert len(result) == 1
        assert isinstance(result[0], Path)

    def test_uses_correct_hive_path_structure(self, tmp_path: Path) -> None:
        """Constructs path using year=YYYY/month=MM/day=DD hive format."""
        target_date = date(2026, 3, 5)  # Single-digit month and day
        day_dir = (
            tmp_path
            / "weather"
            / "year=2026"
            / "month=03"
            / "day=05"
        )
        day_dir.mkdir(parents=True)
        (day_dir / "journal_030000_000001.ipc").touch()

        result = discover_journals(tmp_path, "weather", target_date)

        assert len(result) == 1
        # Verify path is under the correct hive directory
        assert "year=2026" in str(result[0])
        assert "month=03" in str(result[0])
        assert "day=05" in str(result[0])


# ---------------------------------------------------------------------------
# Tests: read_ipc_with_repair
# ---------------------------------------------------------------------------


class TestReadIpcWithRepair:
    """Tests for read_ipc_with_repair() partial-repair reader."""

    def test_clean_file_returns_all_batches_and_true(self, tmp_path: Path) -> None:
        """Normal closed IPC file returns (all batches, True)."""
        ipc_file = tmp_path / "clean.ipc"
        expected_rows = _write_clean_ipc(ipc_file, n_batches=2, rows_per_batch=3)

        batches, was_clean = read_ipc_with_repair(ipc_file)

        assert was_clean is True
        assert len(batches) == 2
        total_rows = sum(b.num_rows for b in batches)
        assert total_rows == expected_rows

    def test_crash_file_returns_all_batches_and_true(self, tmp_path: Path) -> None:
        """IPC file written without close() (missing EOS marker) returns (all batches, True).

        Per RESEARCH.md verified behavior: pyarrow 22.0.0 raises StopIteration
        (not ArrowInvalid) for files missing the EOS marker from a daemon crash
        without actual corruption.
        """
        ipc_file = tmp_path / "crash.ipc"
        expected_rows = _write_crash_ipc(ipc_file, n_batches=2, rows_per_batch=3)

        batches, was_clean = read_ipc_with_repair(ipc_file)

        assert was_clean is True
        assert len(batches) == 2
        total_rows = sum(b.num_rows for b in batches)
        assert total_rows == expected_rows

    def test_corrupt_trailing_bytes_returns_valid_batches_and_false(
        self, tmp_path: Path
    ) -> None:
        """IPC file with corrupt trailing bytes returns (valid batches, False)."""
        ipc_file = tmp_path / "corrupt_trailing.ipc"
        expected_rows = _write_corrupt_trailing_ipc(
            ipc_file, n_batches=2, rows_per_batch=3
        )

        batches, was_clean = read_ipc_with_repair(ipc_file)

        assert was_clean is False
        # May have 0, 1, or 2 batches depending on where corruption is detected
        # but should not raise an exception
        assert isinstance(batches, list)
        # All returned batches must be valid RecordBatch objects
        for batch in batches:
            assert isinstance(batch, pa.RecordBatch)

    def test_corrupt_header_returns_empty_list_and_false(
        self, tmp_path: Path
    ) -> None:
        """IPC file with corrupt header returns ([], False) without raising."""
        ipc_file = tmp_path / "corrupt_header.ipc"
        _write_corrupt_header_ipc(ipc_file)

        batches, was_clean = read_ipc_with_repair(ipc_file)

        assert was_clean is False
        assert batches == []

    def test_empty_file_returns_empty_list_and_false(self, tmp_path: Path) -> None:
        """Empty file returns ([], False) without raising."""
        ipc_file = tmp_path / "empty.ipc"
        ipc_file.write_bytes(b"")

        batches, was_clean = read_ipc_with_repair(ipc_file)

        assert was_clean is False
        assert batches == []

    def test_batches_contain_valid_record_batches(self, tmp_path: Path) -> None:
        """Batches returned by repair reader are valid pa.RecordBatch objects."""
        ipc_file = tmp_path / "valid.ipc"
        _write_clean_ipc(ipc_file, n_batches=3, rows_per_batch=5)

        batches, was_clean = read_ipc_with_repair(ipc_file)

        assert was_clean is True
        assert len(batches) == 3
        for batch in batches:
            assert isinstance(batch, pa.RecordBatch)
            assert batch.num_rows == 5

    def test_return_type_is_tuple(self, tmp_path: Path) -> None:
        """Return type is a tuple of (list, bool)."""
        ipc_file = tmp_path / "test.ipc"
        _write_clean_ipc(ipc_file)

        result = read_ipc_with_repair(ipc_file)

        assert isinstance(result, tuple)
        assert len(result) == 2
        batches, was_clean = result
        assert isinstance(batches, list)
        assert isinstance(was_clean, bool)

    def test_single_batch_file(self, tmp_path: Path) -> None:
        """Single-batch IPC file is read correctly."""
        ipc_file = tmp_path / "single_batch.ipc"
        _write_clean_ipc(ipc_file, n_batches=1, rows_per_batch=10)

        batches, was_clean = read_ipc_with_repair(ipc_file)

        assert was_clean is True
        assert len(batches) == 1
        assert batches[0].num_rows == 10
