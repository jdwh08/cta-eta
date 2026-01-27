"""Shared utilities for data collection (API parsing, validation)."""

# NOTE(jdwh08): once we get enough utils, break into separate files and organize them better

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Final

from cta_eta.data_collection.exceptions import APIResponseError

if TYPE_CHECKING:
    from pathlib import Path

MIN_LAT: Final[float] = -90.0
MAX_LAT: Final[float] = 90.0
MIN_LON: Final[float] = -180.0
MAX_LON: Final[float] = 180.0


def safe_get_nested(
    data: dict[str, object], *keys: str, api_name: str = "API"
) -> object:
    """Safely access nested dictionary keys with descriptive error messages.

    Args:
        data: Dictionary to access
        *keys: Variable number of keys to traverse (e.g., "properties", "forecastHourly")
        api_name: Name of API for error messages

    Returns:
        Value at nested key path

    Raises:
        APIResponseError: If any key in the path is missing or data is not a dict

    """
    current: object = data
    path: list[str] = []

    for key in keys:
        if not isinstance(current, dict):
            msg = (
                f"{api_name} response parsing error: Expected dict at path "
                f"'{'.'.join(path)}', got {type(current).__name__}"
            )
            raise APIResponseError(msg)

        path.append(key)
        if key not in current:
            msg = (
                f"{api_name} response missing required field: "
                f"'{'.'.join(path)}'. Response structure may have changed."
            )
            raise APIResponseError(msg)

        current = current[key]

    return current


def validate_lat_lon(lat: float | object, lon: float | object) -> None:
    """Validate latitude and longitude are within geographic bounds.

    Args:
        lat: Latitude (degrees)
        lon: Longitude (degrees)

    Raises:
        ValueError: If lat not in [-90, 90] or lon not in [-180, 180]

    """
    # Try to convert to float
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        try:
            lat = float(lat)  # ty:ignore[invalid-argument-type]
            lon = float(lon)  # ty:ignore[invalid-argument-type]
        except ValueError as e:
            msg = f"Invalid latitude or longitude: {lat}, {lon}. Must be numeric."
            raise TypeError(msg) from e

    if not (MIN_LAT <= lat <= MAX_LAT):
        msg = f"Invalid latitude: {lat}. Must be between -90 and 90 degrees."
        raise ValueError(msg)
    if not (MIN_LON <= lon <= MAX_LON):
        msg = f"Invalid longitude: {lon}. Must be between -180 and 180 degrees."
        raise ValueError(msg)


def convert_celsius_to_fahrenheit(celsius: float) -> float:
    """Convert Celsius to Fahrenheit.

    Args:
        celsius: Temperature in Celsius

    Returns:
        Temperature in Fahrenheit

    """
    return celsius * 9 / 5 + 32


def percentile(sorted_samples: list[float], pct: int) -> float:
    """Calculate a percentile of a list of samples.

    Args:
        sorted_samples: The list of samples to calculate the percentile of.
        pct: The percentile to calculate.

    Returns:
        The percentile of the list of samples.

    """
    if not sorted_samples:
        return 0.0
    if pct <= 0:
        return round(sorted_samples[0], 2)
    if pct >= 100:  # noqa: PLR2004
        return round(sorted_samples[-1], 2)
    k = (len(sorted_samples) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_samples) - 1)
    if f == c:
        return round(sorted_samples[f], 2)
    d0 = sorted_samples[f] * (c - k)
    d1 = sorted_samples[c] * (k - f)
    return round(d0 + d1, 2)


def rotate_file_if_needed(path: Path, *, max_bytes: int, backups: int) -> None:
    """Rotate a file if it exceeds a maximum size.

    Args:
        path: The path to the file to rotate.
        max_bytes: The maximum size of the file in bytes.
        backups: The number of backups to keep.

    """
    if backups <= 0:
        return
    try:
        st = path.stat()
    except FileNotFoundError:
        return
    except OSError:
        return

    if st.st_size < max_bytes:
        return

    # Rotate: file -> .1, .1 -> .2, ... oldest dropped.
    for idx in range(backups, 0, -1):
        src = path.with_suffix(path.suffix + f".{idx}")
        dst = path.with_suffix(path.suffix + f".{idx + 1}")
        if idx == backups:
            with suppress(FileNotFoundError):
                dst.unlink()
        if src.exists():
            with suppress(OSError):
                src.replace(dst)

    with suppress(OSError):
        path.replace(path.with_suffix(path.suffix + ".1"))
