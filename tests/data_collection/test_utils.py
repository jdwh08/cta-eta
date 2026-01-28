"""Unit tests for utils module."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cta_eta.data_collection.exceptions import APIResponseError
from cta_eta.data_collection.utils import (
    convert_celsius_to_fahrenheit,
    percentile,
    rotate_file_if_needed,
    safe_get_nested,
    validate_lat_lon,
)

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


class TestSafeGetNested:
    """Tests for safe_get_nested."""

    def test_success_single_key(self) -> None:
        """Returns value for single existing key."""
        # Arrange
        data: dict[str, object] = {"a": 1}

        # Act
        result = safe_get_nested(data, "a")

        # Assert
        assert result == 1

    def test_success_nested_keys(self) -> None:
        """Returns value for nested key path."""
        # Arrange
        data: dict[str, object] = {"a": {"b": {"c": "value"}}}

        # Act
        result = safe_get_nested(data, "a", "b", "c")

        # Assert
        assert result == "value"

    def test_success_custom_api_name(self) -> None:
        """Uses custom api_name in error messages when key missing."""
        # Arrange
        data: dict[str, object] = {"a": {}}

        # Act & Assert
        with pytest.raises(
            APIResponseError, match=r"NWS response missing required field: 'a\.b'"
        ):
            safe_get_nested(data, "a", "b", api_name="NWS")

    def test_missing_key_raises_type_error(self) -> None:
        """Raises TypeError when required key is missing."""
        # Arrange
        data: dict[str, object] = {"a": 1}

        # Act & Assert
        with pytest.raises(
            APIResponseError, match="API response missing required field: 'b'"
        ):
            safe_get_nested(data, "b")

    def test_missing_nested_key_raises_type_error(self) -> None:
        """Raises TypeError when nested key is missing."""
        # Arrange
        data: dict[str, object] = {"a": {"b": 1}}

        # Act & Assert
        with pytest.raises(APIResponseError, match=r"'a\.c'") as exc_info:
            safe_get_nested(data, "a", "c")
        assert "missing required field" in str(exc_info.value)

    def test_non_dict_at_path_raises_type_error(self) -> None:
        """Raises TypeError when intermediate value is not a dict."""
        # Arrange
        data: dict[str, object] = {"a": [1, 2, 3]}

        # Act & Assert
        with pytest.raises(
            APIResponseError, match=r"Expected dict at path 'a', got list"
        ):
            safe_get_nested(data, "a", "b")

    def test_non_dict_at_nested_path_raises_type_error(self) -> None:
        """Raises TypeError when deeper intermediate value is not a dict."""
        # Arrange
        data: dict[str, object] = {"a": {"b": "string"}}

        # Act & Assert
        with pytest.raises(
            APIResponseError, match=r"Expected dict at path 'a\.b', got str"
        ):
            safe_get_nested(data, "a", "b", "c")


class TestValidateLatLon:
    """Tests for validate_lat_lon."""

    def test_valid_float(self) -> None:
        """Accepts valid lat/lon as floats."""
        # Act & Assert - no raise
        validate_lat_lon(41.88, -87.63)

    def test_valid_int(self) -> None:
        """Accepts valid lat/lon as ints."""
        # Act & Assert - no raise
        validate_lat_lon(0, 0)

    def test_valid_boundary_lat_90(self) -> None:
        """Accepts latitude at 90."""
        validate_lat_lon(90.0, 0.0)

    def test_valid_boundary_lat_minus_90(self) -> None:
        """Accepts latitude at -90."""
        validate_lat_lon(-90.0, 0.0)

    def test_valid_boundary_lon_180(self) -> None:
        """Accepts longitude at 180."""
        validate_lat_lon(0.0, 180.0)

    def test_valid_boundary_lon_minus_180(self) -> None:
        """Accepts longitude at -180."""
        validate_lat_lon(0.0, -180.0)

    def test_invalid_lat_above_90_raises(self) -> None:
        """Raises ValueError when latitude > 90."""
        with pytest.raises(
            ValueError, match=r"Invalid latitude: 91\.0. Must be between -90 and 90"
        ):
            validate_lat_lon(91.0, 0.0)

    def test_invalid_lat_below_minus_90_raises(self) -> None:
        """Raises ValueError when latitude < -90."""
        with pytest.raises(ValueError, match=r"Invalid latitude: -91\.0"):
            validate_lat_lon(-91.0, 0.0)

    def test_invalid_lon_above_180_raises(self) -> None:
        """Raises ValueError when longitude > 180."""
        with pytest.raises(
            ValueError, match=r"Invalid longitude: 181\.0. Must be between -180 and 180"
        ):
            validate_lat_lon(0.0, 181.0)

    def test_invalid_lon_below_minus_180_raises(self) -> None:
        """Raises ValueError when longitude < -180."""
        with pytest.raises(ValueError, match=r"Invalid longitude: -181\.0"):
            validate_lat_lon(0.0, -181.0)

    def test_non_numeric_raises_type_error(self) -> None:
        """Raises TypeError when lat or lon is not numeric and cannot be converted."""
        with pytest.raises(
            TypeError, match=r"Invalid latitude or longitude.*Must be numeric"
        ):
            validate_lat_lon("41.88", "not_a_number")

    def test_string_numeric_converts_and_validates(self) -> None:
        """Accepts string numerals that convert to float."""
        # Act & Assert - no raise
        validate_lat_lon("41.88", "-87.63")


class TestConvertCelsiusToFahrenheit:
    """Tests for convert_celsius_to_fahrenheit."""

    def test_freezing(self) -> None:
        """0 C is 32 F."""
        assert convert_celsius_to_fahrenheit(0.0) == 32.0

    def test_boiling(self) -> None:
        """100 C is 212 F."""
        assert convert_celsius_to_fahrenheit(100.0) == 212.0

    def test_negative(self) -> None:
        """-40 C is -40 F."""
        assert convert_celsius_to_fahrenheit(-40.0) == -40.0

    def test_typical_weather(self) -> None:
        """Typical weather 20 C is 68 F."""
        assert convert_celsius_to_fahrenheit(20.0) == 68.0


class TestPercentile:
    """Tests for percentile helper function."""

    def test_percentile_empty_list(self) -> None:
        """Percentile returns 0.0 for empty list."""
        # Arrange
        samples: list[float] = []

        # Act
        result = percentile(samples, 50)

        # Assert
        assert result == 0.0

    def test_percentile_single_value(self) -> None:
        """Percentile returns the value for single-element list."""
        # Arrange
        samples = [42.0]

        # Act
        result = percentile(samples, 50)

        # Assert
        assert result == 42.0

    def test_percentile_p50(self) -> None:
        """Percentile calculates 50th percentile correctly."""
        # Arrange
        samples = [10.0, 20.0, 30.0, 40.0, 50.0]

        # Act
        result = percentile(samples, 50)

        # Assert
        assert result == 30.0

    def test_percentile_p95(self) -> None:
        """Percentile calculates 95th percentile correctly."""
        # Arrange
        samples = [i * 10.0 for i in range(1, 21)]  # 10, 20, ..., 200

        # Act
        result = percentile(samples, 95)

        # Assert - uses interpolation, so 95th percentile of 20 values is 190.5
        assert result == 190.5

    def test_percentile_p0(self) -> None:
        """Percentile returns first value for 0th percentile."""
        # Arrange
        samples = [10.0, 20.0, 30.0]

        # Act
        result = percentile(samples, 0)

        # Assert
        assert result == 10.0

    def test_percentile_p100(self) -> None:
        """Percentile returns last value for 100th percentile."""
        # Arrange
        samples = [10.0, 20.0, 30.0]

        # Act
        result = percentile(samples, 100)

        # Assert
        assert result == 30.0

    def test_percentile_negative_percentile(self) -> None:
        """Percentile handles negative percentile as 0."""
        # Arrange
        samples = [10.0, 20.0, 30.0]

        # Act
        result = percentile(samples, -10)

        # Assert
        assert result == 10.0

    def test_percentile_over_100_percentile(self) -> None:
        """Percentile handles percentile > 100 as 100."""
        # Arrange
        samples = [10.0, 20.0, 30.0]

        # Act
        result = percentile(samples, 150)

        # Assert
        assert result == 30.0

    def test_percentile_rounds_result(self) -> None:
        """Percentile rounds result to 2 decimal places."""
        # Arrange
        samples = [1.111, 2.222, 3.333]

        # Act
        result = percentile(samples, 50)

        # Assert
        assert result == 2.22


class TestRotateIfNeeded:
    """Tests for rotate_file_if_needed helper function."""

    def test_rotate_if_needed_no_rotation_when_small(self, tmp_path: Path) -> None:
        """rotate_file_if_needed does not rotate when file is small."""
        # Arrange
        log_path = tmp_path / "test.jsonl"
        log_path.write_text("small content")
        max_bytes = 1024

        # Act
        rotate_file_if_needed(log_path, max_bytes=max_bytes, backups=3)

        # Assert
        assert log_path.exists()
        assert not (tmp_path / "test.jsonl.1").exists()

    def test_rotate_if_needed_rotates_when_large(self, tmp_path: Path) -> None:
        """rotate_file_if_needed rotates file when it exceeds max_bytes."""
        # Arrange
        log_path = tmp_path / "test.jsonl"
        large_content = "x" * 2048
        log_path.write_text(large_content)
        max_bytes = 1024

        # Act
        rotate_file_if_needed(log_path, max_bytes=max_bytes, backups=2)

        # Assert
        assert (tmp_path / "test.jsonl.1").exists()
        assert (tmp_path / "test.jsonl.1").read_text() == large_content

    def test_rotate_if_needed_rotates_existing_backups(self, tmp_path: Path) -> None:
        """rotate_file_if_needed rotates existing backup files."""
        # Arrange
        log_path = tmp_path / "test.jsonl"
        log_path.write_text("x" * 2048)
        (tmp_path / "test.jsonl.1").write_text("backup1")
        (tmp_path / "test.jsonl.2").write_text("backup2")
        max_bytes = 1024

        # Act
        rotate_file_if_needed(log_path, max_bytes=max_bytes, backups=2)

        # Assert
        assert (tmp_path / "test.jsonl.1").read_text() == "x" * 2048
        assert (tmp_path / "test.jsonl.2").read_text() == "backup1"
        # backup2 should be dropped (only 2 backups)

    def test_rotate_if_needed_drops_oldest_backup(self, tmp_path: Path) -> None:
        """rotate_file_if_needed rotates files when at limit."""
        # Arrange
        log_path = tmp_path / "test.jsonl"
        log_path.write_text("x" * 2048)
        (tmp_path / "test.jsonl.1").write_text("backup1")
        (tmp_path / "test.jsonl.2").write_text("backup2")
        (tmp_path / "test.jsonl.3").write_text("backup3")
        max_bytes = 1024

        # Act
        rotate_file_if_needed(log_path, max_bytes=max_bytes, backups=2)

        # Assert
        # Rotation: .3 is unlinked, then .2 -> .3, .1 -> .2, main -> .1
        # So .3 will exist with backup2 content (was recreated from .2)
        assert (tmp_path / "test.jsonl.3").read_text() == "backup2"
        assert (tmp_path / "test.jsonl.2").read_text() == "backup1"
        assert (tmp_path / "test.jsonl.1").read_text() == "x" * 2048

    def test_rotate_if_needed_noop_when_backups_zero(self, tmp_path: Path) -> None:
        """rotate_file_if_needed does nothing when backups=0."""
        # Arrange
        log_path = tmp_path / "test.jsonl"
        log_path.write_text("x" * 2048)
        max_bytes = 1024

        # Act
        rotate_file_if_needed(log_path, max_bytes=max_bytes, backups=0)

        # Assert
        assert log_path.exists()
        assert not (tmp_path / "test.jsonl.1").exists()

    def test_rotate_if_needed_handles_missing_file(self, tmp_path: Path) -> None:
        """rotate_file_if_needed handles missing file gracefully."""
        # Arrange
        log_path = tmp_path / "nonexistent.jsonl"
        max_bytes = 1024

        # Act - should not raise
        rotate_file_if_needed(log_path, max_bytes=max_bytes, backups=3)

        # Assert
        assert not log_path.exists()

    def test_rotate_if_needed_handles_stat_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rotate_file_if_needed handles stat errors gracefully."""
        # Arrange
        log_path = tmp_path / "test.jsonl"
        original_content = "content"
        log_path.write_text(original_content)
        # Monkeypatch Path.stat to raise OSError for this specific path
        original_stat = Path.stat
        target_path_str = str(log_path)
        call_count = [0]

        def failing_stat(self: Path) -> object:
            if str(self) == target_path_str and call_count[0] == 0:
                call_count[0] += 1
                err = OSError()
                err.args = ("stat failed",)
                raise err
            return original_stat(self)

        monkeypatch.setattr(Path, "stat", failing_stat)
        max_bytes = 1024

        # Act - should not raise (OSError is caught and function returns early)
        rotate_file_if_needed(log_path, max_bytes=max_bytes, backups=3)

        # Assert - file content should be unchanged (function returned early)
        assert log_path.read_text() == original_content

    def test_rotate_if_needed_handles_replace_error(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """rotate_file_if_needed handles replace errors gracefully."""
        # Arrange
        log_path = tmp_path / "test.jsonl"
        log_path.write_text("x" * 2048)
        original_replace = Path.replace

        def failing_replace(self: Path, target: Path) -> Path:
            if self == log_path:
                msg = "replace failed"
                raise OSError(msg)
            return original_replace(self, target)

        mocker.patch("pathlib.Path.replace", failing_replace)
        max_bytes = 1024

        # Act - should not raise (OSError is suppressed)
        rotate_file_if_needed(log_path, max_bytes=max_bytes, backups=3)

        # Assert - original file still there (final path.replace failed and was suppressed)
        assert log_path.exists()
