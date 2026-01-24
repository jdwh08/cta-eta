"""Multi-source weather data merger with precedence rules.

Merges weather data from National Weather Service (NWS), Open-Meteo (OM),
and OpenWeatherMap (OWM) into unified weather records.

Precedence for overlapping fields: NWS > Open-Meteo > OpenWeatherMap
"""

from __future__ import annotations

import math

import numpy as np


def _convert_to_python_type(value: object) -> object:
    """Convert numpy/pandas types to Python native types.

    Args:
        value: Value to convert (may be numpy/pandas type or Python type)

    Returns:
        Python native type (int, float, str, etc.)

    """
    # Convert numpy integer types to Python int
    if isinstance(value, np.integer):
        return int(value)
    # Convert numpy float types to Python float
    if isinstance(value, np.floating):
        return float(value)
    # Convert numpy string types to Python str
    if isinstance(value, np.str_):
        return str(value)
    # Return as-is if already a Python native type
    return value


def merge_weather_sources(
    nws_data: dict[str, object] | None,
    om_data: dict[str, object] | None,
    owm_data: dict[str, object] | None = None,
) -> dict[str, object] | None:
    """Merge weather data from multiple sources into a unified record.

    Combines data from NWS, Open-Meteo, and OpenWeatherMap sources, using
    precedence rules for overlapping fields: NWS takes priority over Open-Meteo,
    which takes priority over OpenWeatherMap.

    Args:
        nws_data: Weather data from National Weather Service API (preferred source)
        om_data: Weather data from Open-Meteo API (supplementary variables)
        owm_data: Weather data from OpenWeatherMap API (fallback source)

    Returns:
        Merged weather dictionary with all available variables, or None if all
        sources are None or empty

    Examples:
        >>> nws = {"timestamp": "2026-01-19T12:00", "temperature_f": 35.0}
        >>> om = {"timestamp": "2026-01-19T12:00", "visibility_m": 10000.0}
        >>> merge_weather_sources(nws, om)
        {'timestamp': '2026-01-19T12:00', 'temperature_f': 35.0, 'visibility_m': 10000.0}

        >>> # NWS preferred for overlapping fields
        >>> nws = {"wind_speed_mph": 10.0}
        >>> om = {"wind_speed_mph": 11.0}
        >>> merge_weather_sources(nws, om)["wind_speed_mph"]
        10.0

    """
    # Normalize empty dicts to None for consistent handling
    sources_data = {
        "nws": nws_data if nws_data else None,
        "om": om_data if om_data else None,
        "owm": owm_data if owm_data else None,
    }

    # Filter to only non-None sources
    available_sources = {k: v for k, v in sources_data.items() if v is not None}

    # Return None if no sources available
    if not available_sources:
        return None

    # If only one source, return it directly (no merge needed)
    if len(available_sources) == 1:
        source_dict = next(iter(available_sources.values()))
        if not isinstance(source_dict, dict):
            msg = "Source data is not a dictionary!"
            raise ValueError(msg)

        # Convert to Python native types
        output = {
            key: _convert_to_python_type(value)
            for key, value in source_dict.items()
            if value is not None
        }
        return output

    # Multiple sources present - merge with precedence: NWS > Open-Meteo > OpenWeatherMap
    result_dict: dict[str, object] = {}

    # Collect all unique keys from all sources
    all_keys: set[str] = set()
    for source_dict in available_sources.values():
        if isinstance(source_dict, dict):
            all_keys.update(source_dict.keys())

    # For each key, use value from highest precedence source that has it
    precedence_order = ["nws", "om", "owm"]
    for key in all_keys:
        value = None
        # Try each source in order of precedence
        for source_name in precedence_order:
            if source_name not in available_sources:
                continue
            source_dict = available_sources.get(source_name)
            if not isinstance(source_dict, dict):
                continue
            if key not in source_dict:
                continue

            candidate = source_dict[key]
            if candidate is None or (
                isinstance(candidate, (float, np.floating)) and (math.isnan(candidate))
            ):
                continue
            value = candidate
            break

        result_dict[key] = _convert_to_python_type(value)

    return result_dict if result_dict else None
