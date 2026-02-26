"""Unit tests for schema_registry.py — drift classification, registry I/O.

TDD: tests written first (RED), then implementation (GREEN).

Tests cover:
- classify_drift: removed fields (breaking), incompatible type changes (breaking),
  widening type changes (none), nullability changes (breaking), added fields (additive),
  field order ignored, identical schemas (none)
- Registry round-trip: complex types like timestamp[us, tz=UTC] survive JSON serialization
- bootstrap_registry: creates file if missing, no-op if exists
- load_registry: returns None for missing path
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pytest

from cta_eta.data_collection.compaction.schema_registry import (
    DriftResult,
    bootstrap_registry,
    classify_drift,
    load_registry,
    registry_dict_to_schema,
    save_registry,
    schema_to_registry_dict,
)


# ---------------------------------------------------------------------------
# classify_drift: breaking changes
# ---------------------------------------------------------------------------


class TestClassifyDriftBreakingRemoved:
    """Field removed from observed schema → breaking drift."""

    def test_removed_field_is_breaking(self) -> None:
        registry_schema = pa.schema(
            [
                pa.field("id", pa.int64()),
                pa.field("value", pa.string()),
            ]
        )
        observed_schema = pa.schema(
            [
                pa.field("id", pa.int64()),
                # "value" removed
            ]
        )

        result = classify_drift(registry_schema, observed_schema)

        assert result.drift_type == "breaking"
        assert "value" in result.removed_fields

    def test_removed_field_is_in_removed_fields_list(self) -> None:
        registry_schema = pa.schema(
            [
                pa.field("a", pa.int64()),
                pa.field("b", pa.float64()),
                pa.field("c", pa.string()),
            ]
        )
        observed_schema = pa.schema(
            [
                pa.field("a", pa.int64()),
                # b and c removed
            ]
        )

        result = classify_drift(registry_schema, observed_schema)

        assert result.drift_type == "breaking"
        assert set(result.removed_fields) == {"b", "c"}
        assert result.added_fields == []
        assert result.breaking_fields == []


class TestClassifyDriftBreakingTypeIncompatible:
    """Incompatible type change (int64 → utf8) → breaking drift."""

    def test_int64_to_utf8_is_breaking(self) -> None:
        registry_schema = pa.schema(
            [
                pa.field("count", pa.int64()),
            ]
        )
        observed_schema = pa.schema(
            [
                pa.field("count", pa.utf8()),
            ]
        )

        result = classify_drift(registry_schema, observed_schema)

        assert result.drift_type == "breaking"
        assert len(result.breaking_fields) == 1
        assert result.breaking_fields[0].name == "count"
        assert result.breaking_fields[0].old_type == "int64"
        assert result.breaking_fields[0].new_type == "string"

    def test_float64_to_int64_is_breaking(self) -> None:
        """Narrowing (float64 → int64) is not in WIDENING_PAIRS → breaking."""
        registry_schema = pa.schema(
            [
                pa.field("x", pa.float64()),
            ]
        )
        observed_schema = pa.schema(
            [
                pa.field("x", pa.int64()),
            ]
        )

        result = classify_drift(registry_schema, observed_schema)

        assert result.drift_type == "breaking"
        assert result.breaking_fields[0].name == "x"


# ---------------------------------------------------------------------------
# classify_drift: widening (silent/none)
# ---------------------------------------------------------------------------


class TestClassifyDriftWideningNone:
    """Widening type changes (int32 → int64) produce no drift."""

    def test_int32_to_int64_is_none(self) -> None:
        registry_schema = pa.schema(
            [
                pa.field("count", pa.int32()),
            ]
        )
        observed_schema = pa.schema(
            [
                pa.field("count", pa.int64()),
            ]
        )

        result = classify_drift(registry_schema, observed_schema)

        assert result.drift_type == "none"
        assert result.breaking_fields == []
        assert result.removed_fields == []

    def test_int8_to_int64_is_none(self) -> None:
        registry_schema = pa.schema(
            [
                pa.field("small", pa.int8()),
            ]
        )
        observed_schema = pa.schema(
            [
                pa.field("small", pa.int64()),
            ]
        )

        result = classify_drift(registry_schema, observed_schema)

        assert result.drift_type == "none"

    def test_float32_to_float64_is_none(self) -> None:
        registry_schema = pa.schema(
            [
                pa.field("ratio", pa.float32()),
            ]
        )
        observed_schema = pa.schema(
            [
                pa.field("ratio", pa.float64()),
            ]
        )

        result = classify_drift(registry_schema, observed_schema)

        assert result.drift_type == "none"

    def test_int32_to_float64_is_none(self) -> None:
        registry_schema = pa.schema(
            [
                pa.field("value", pa.int32()),
            ]
        )
        observed_schema = pa.schema(
            [
                pa.field("value", pa.float64()),
            ]
        )

        result = classify_drift(registry_schema, observed_schema)

        assert result.drift_type == "none"


# ---------------------------------------------------------------------------
# classify_drift: nullability changes (breaking)
# ---------------------------------------------------------------------------


class TestClassifyDriftNullabilityBreaking:
    """Nullability changes → breaking drift."""

    def test_required_to_nullable_is_breaking(self) -> None:
        registry_schema = pa.schema(
            [
                pa.field("id", pa.int64(), nullable=False),
            ]
        )
        observed_schema = pa.schema(
            [
                pa.field("id", pa.int64(), nullable=True),
            ]
        )

        result = classify_drift(registry_schema, observed_schema)

        assert result.drift_type == "breaking"
        assert len(result.nullability_changes) == 1
        assert result.nullability_changes[0].name == "id"
        assert result.nullability_changes[0].old_nullable is False
        assert result.nullability_changes[0].new_nullable is True

    def test_nullable_to_required_is_breaking(self) -> None:
        registry_schema = pa.schema(
            [
                pa.field("name", pa.string(), nullable=True),
            ]
        )
        observed_schema = pa.schema(
            [
                pa.field("name", pa.string(), nullable=False),
            ]
        )

        result = classify_drift(registry_schema, observed_schema)

        assert result.drift_type == "breaking"
        assert result.nullability_changes[0].old_nullable is True
        assert result.nullability_changes[0].new_nullable is False


# ---------------------------------------------------------------------------
# classify_drift: additive (new field)
# ---------------------------------------------------------------------------


class TestClassifyDriftAdditiveNewField:
    """New field in observed but not registry → additive drift."""

    def test_new_field_is_additive(self) -> None:
        registry_schema = pa.schema(
            [
                pa.field("id", pa.int64()),
            ]
        )
        observed_schema = pa.schema(
            [
                pa.field("id", pa.int64()),
                pa.field("extra", pa.string()),
            ]
        )

        result = classify_drift(registry_schema, observed_schema)

        assert result.drift_type == "additive"
        assert len(result.added_fields) == 1
        assert result.added_fields[0].name == "extra"
        assert result.added_fields[0].type == "string"
        assert result.removed_fields == []
        assert result.breaking_fields == []

    def test_additive_does_not_override_breaking(self) -> None:
        """If breaking change exists alongside new field, drift_type is breaking."""
        registry_schema = pa.schema(
            [
                pa.field("id", pa.int64()),
                pa.field("old_field", pa.string()),
            ]
        )
        observed_schema = pa.schema(
            [
                pa.field("id", pa.int64()),
                pa.field("new_field", pa.float64()),
                # old_field removed (breaking)
            ]
        )

        result = classify_drift(registry_schema, observed_schema)

        assert result.drift_type == "breaking"
        assert "old_field" in result.removed_fields
        assert result.added_fields[0].name == "new_field"


# ---------------------------------------------------------------------------
# classify_drift: field order ignored
# ---------------------------------------------------------------------------


class TestClassifyDriftFieldOrderIgnored:
    """Field order differences produce no drift."""

    def test_reversed_field_order_is_none(self) -> None:
        registry_schema = pa.schema(
            [
                pa.field("a", pa.int64()),
                pa.field("b", pa.string()),
                pa.field("c", pa.float64()),
            ]
        )
        observed_schema = pa.schema(
            [
                pa.field("c", pa.float64()),
                pa.field("a", pa.int64()),
                pa.field("b", pa.string()),
            ]
        )

        result = classify_drift(registry_schema, observed_schema)

        assert result.drift_type == "none"
        assert result.removed_fields == []
        assert result.added_fields == []


# ---------------------------------------------------------------------------
# classify_drift: identical schemas
# ---------------------------------------------------------------------------


class TestClassifyDriftNoneIdentical:
    """Identical schemas → drift_type=none, all lists empty."""

    def test_identical_schemas_produce_no_drift(self) -> None:
        schema = pa.schema(
            [
                pa.field("poll_timestamp", pa.timestamp("us", tz="UTC")),
                pa.field("route", pa.string()),
                pa.field("lat", pa.float64()),
            ]
        )

        result = classify_drift(schema, schema)

        assert result.drift_type == "none"
        assert result.breaking_fields == []
        assert result.added_fields == []
        assert result.removed_fields == []
        assert result.nullability_changes == []

    def test_empty_schemas_produce_no_drift(self) -> None:
        schema = pa.schema([])

        result = classify_drift(schema, schema)

        assert result.drift_type == "none"


# ---------------------------------------------------------------------------
# Registry round-trip: complex types
# ---------------------------------------------------------------------------


class TestRegistryRoundTripComplexTypes:
    """timestamp[us, tz=UTC] + int64 + utf8 round-trips through JSON registry."""

    def test_complex_schema_round_trips_exactly(self, tmp_path: Path) -> None:
        original_schema = pa.schema(
            [
                pa.field("poll_timestamp", pa.timestamp("us", tz="UTC")),
                pa.field("count", pa.int64()),
                pa.field("label", pa.utf8()),
            ]
        )

        registry_dict = schema_to_registry_dict(original_schema, "test_daemon")
        reconstructed = registry_dict_to_schema(registry_dict)

        assert reconstructed.equals(original_schema)

    def test_registry_dict_has_required_keys(self) -> None:
        schema = pa.schema(
            [
                pa.field("x", pa.int32()),
            ]
        )

        d = schema_to_registry_dict(schema, "my_daemon")

        assert "version" in d
        assert d["version"] == 1
        assert "daemon" in d
        assert d["daemon"] == "my_daemon"
        assert "updated" in d
        assert "fields" in d
        assert "schema_ipc_b64" in d

    def test_registry_dict_fields_are_human_readable(self) -> None:
        schema = pa.schema(
            [
                pa.field("ts", pa.timestamp("us", tz="UTC"), nullable=True),
                pa.field("val", pa.int64(), nullable=False),
            ]
        )

        d = schema_to_registry_dict(schema, "test")

        assert len(d["fields"]) == 2
        # Check human-readable format
        ts_field = next(f for f in d["fields"] if f["name"] == "ts")
        assert ts_field["type"] == "timestamp[us, tz=UTC]"
        assert ts_field["nullable"] is True

        val_field = next(f for f in d["fields"] if f["name"] == "val")
        assert val_field["type"] == "int64"
        assert val_field["nullable"] is False

    def test_registry_dict_to_schema_uses_ipc_not_fields_list(
        self, tmp_path: Path
    ) -> None:
        """registry_dict_to_schema must use schema_ipc_b64, not the fields list."""
        original = pa.schema(
            [
                pa.field("ts", pa.timestamp("us", tz="UTC")),
            ]
        )
        d = schema_to_registry_dict(original, "test")

        # Corrupt the human-readable fields list (should be ignored)
        d["fields"] = [{"name": "ts", "type": "string", "nullable": True}]

        # Should still reconstruct correctly from ipc_b64
        reconstructed = registry_dict_to_schema(d)
        assert reconstructed.equals(original)


# ---------------------------------------------------------------------------
# bootstrap_registry
# ---------------------------------------------------------------------------


class TestBootstrapRegistry:
    """bootstrap_registry creates file if missing, no-op if exists."""

    def test_bootstrap_creates_file_returns_true(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "schemas" / "test_daemon.json"
        schema = pa.schema(
            [
                pa.field("id", pa.int64()),
                pa.field("name", pa.string()),
            ]
        )

        result = bootstrap_registry(registry_path, schema, "test_daemon")

        assert result is True
        assert registry_path.exists()

    def test_bootstrap_created_file_is_valid_json(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "test.json"
        schema = pa.schema([pa.field("x", pa.float64())])

        bootstrap_registry(registry_path, schema, "my_daemon")

        data = json.loads(registry_path.read_text())
        assert data["version"] == 1
        assert data["daemon"] == "my_daemon"
        assert "schema_ipc_b64" in data

    def test_bootstrap_noop_if_exists_returns_false(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "existing.json"
        registry_path.write_text('{"version": 1, "existing": true}')
        schema = pa.schema([pa.field("x", pa.float64())])

        result = bootstrap_registry(registry_path, schema, "my_daemon")

        assert result is False
        # File should not be modified
        data = json.loads(registry_path.read_text())
        assert data.get("existing") is True

    def test_bootstrap_creates_parent_directories(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "a" / "b" / "c" / "schema.json"
        schema = pa.schema([pa.field("id", pa.int64())])

        result = bootstrap_registry(registry_path, schema, "daemon")

        assert result is True
        assert registry_path.exists()


# ---------------------------------------------------------------------------
# load_registry
# ---------------------------------------------------------------------------


class TestLoadRegistry:
    """load_registry returns None for missing path, pa.Schema for valid file."""

    def test_load_returns_none_for_missing_path(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.json"

        result = load_registry(missing)

        assert result is None

    def test_load_returns_schema_for_valid_file(self, tmp_path: Path) -> None:
        original = pa.schema(
            [
                pa.field("poll_timestamp", pa.timestamp("us", tz="UTC")),
                pa.field("route", pa.string()),
            ]
        )
        registry_path = tmp_path / "train_positions.json"
        save_registry(registry_path, original, "train_positions")

        result = load_registry(registry_path)

        assert result is not None
        assert result.equals(original)

    def test_load_raises_value_error_on_corrupt_json(self, tmp_path: Path) -> None:
        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("not valid json {{{")

        with pytest.raises(ValueError):
            load_registry(corrupt)


# ---------------------------------------------------------------------------
# save_registry
# ---------------------------------------------------------------------------


class TestSaveRegistry:
    """save_registry creates parent dirs and writes valid registry JSON."""

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "deep" / "nested" / "dir" / "schema.json"
        schema = pa.schema([pa.field("id", pa.int64())])

        save_registry(path, schema, "test")

        assert path.exists()

    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        original = pa.schema(
            [
                pa.field("ts", pa.timestamp("us", tz="UTC")),
                pa.field("val", pa.float64()),
                pa.field("label", pa.string()),
            ]
        )
        path = tmp_path / "registry.json"

        save_registry(path, original, "test_daemon")
        loaded = load_registry(path)

        assert loaded is not None
        assert loaded.equals(original)
