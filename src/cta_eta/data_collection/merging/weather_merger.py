"""Multi-source weather data merger with precedence rules.

Merges weather data from National Weather Service (NWS), Open-Meteo (OM),
and OpenWeatherMap (OWM) into unified weather records.

Precedence for overlapping fields: NWS > Open-Meteo > OpenWeatherMap
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from typing import Any


def _convert_to_python_type(value: Any) -> Any:
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
    nws_data: dict[str, Any] | None,
    om_data: dict[str, Any] | None,
    owm_data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
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
    # Treat empty dicts as None
    if nws_data is not None and not nws_data:
        nws_data = None
    if om_data is not None and not om_data:
        om_data = None
    if owm_data is not None and not owm_data:
        owm_data = None

    # Return None if all sources are missing
    if nws_data is None and om_data is None and owm_data is None:
        return None

    # If only one source present, return it directly
    if nws_data is not None and om_data is None and owm_data is None:
        return nws_data
    if nws_data is None and om_data is not None and owm_data is None:
        return om_data
    if nws_data is None and om_data is None and owm_data is not None:
        return owm_data

    # Multiple sources present - merge with pandas
    sources = []
    source_names = []

    if nws_data is not None:
        sources.append(pd.DataFrame([nws_data]))
        source_names.append("nws")

    if om_data is not None:
        sources.append(pd.DataFrame([om_data]))
        source_names.append("om")

    if owm_data is not None:
        sources.append(pd.DataFrame([owm_data]))
        source_names.append("owm")

    # Concatenate all sources with suffixes to track origin
    merged = pd.concat(sources, axis=1, keys=source_names)

    # Flatten MultiIndex columns
    merged.columns = [
        f"{col}_{source}" if source else col
        for source, col in merged.columns
    ]

    # Find all unique column names (without suffixes)
    all_columns = set()
    for col in merged.columns:
        # Remove _nws, _om, _owm suffix if present
        base_col = col
        for suffix in ["_nws", "_om", "_owm"]:
            if col.endswith(suffix):
                base_col = col[: -len(suffix)]
                break
        all_columns.add(base_col)

    # Coalesce columns with precedence: NWS > Open-Meteo > OpenWeatherMap
    result_dict: dict[str, Any] = {}

    for base_col in all_columns:
        # Try each source in order of precedence
        value = None
        for source in ["nws", "om", "owm"]:
            source_col = f"{base_col}_{source}"
            if source_col in merged.columns:
                candidate = merged[source_col].iloc[0]
                # Use first non-null value
                if pd.notna(candidate):
                    value = candidate
                    break

        # Only include non-null values in final result
        if pd.notna(value):
            result_dict[base_col] = _convert_to_python_type(value)

    return result_dict
