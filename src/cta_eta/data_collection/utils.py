"""Shared utilities for data collection (API parsing, validation)."""

# NOTE(jdwh08): once we get enough utils, break into separate files and organize them better

from __future__ import annotations

from typing import Final

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
        ValueError: If any key in the path is missing or data is not a dict

    """
    current: object = data
    path: list[str] = []

    for key in keys:
        if not isinstance(current, dict):
            msg = (
                f"{api_name} response parsing error: Expected dict at path "
                f"'{'.'.join(path)}', got {type(current).__name__}"
            )
            raise TypeError(msg)

        path.append(key)
        if key not in current:
            msg = (
                f"{api_name} response missing required field: "
                f"'{'.'.join(path)}'. Response structure may have changed."
            )
            raise TypeError(msg)

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
