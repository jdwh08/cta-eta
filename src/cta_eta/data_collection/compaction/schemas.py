"""PyArrow schema constants for CTA daemon IPC journal files.

Defines canonical schemas matching the exact record structures produced by
TrainPositionDaemon and WeatherDaemon. Used by the compaction pipeline to
validate journal files before merging into daily Parquet output.

Schemas are derived from the daemon source code:
- train_positions: normalize_train_positions() in api_train_position.py
- weather: _merge_station_weather() in weather_daemon.py + API parsers
"""

import pyarrow as pa

# ---------------------------------------------------------------------------
# Train position schema
# ---------------------------------------------------------------------------
# Fields match normalize_train_positions() in api_train_position.py.
# poll_timestamp stored as datetime by the daemon; pyarrow infers
# pa.timestamp('us', tz='UTC') from Python datetime objects.
# Nullable fields use pa.field(..., nullable=True) which is the default.

TRAIN_POSITION_SCHEMA: pa.Schema = pa.schema(
    [
        # Client-side poll timestamp (datetime from daemon)
        pa.field("poll_timestamp", pa.timestamp("us", tz="America/Chicago")),
        # API-provided timestamp string (ISO format from CTA)
        pa.field("api_timestamp", pa.string()),
        # Train line name (red, blue, brn, g, org, p, pink, y)
        pa.field("route", pa.string()),
        # Train run number (string)
        pa.field("train_id", pa.string()),
        # GPS coordinates (nullable — may be None from API)
        pa.field("lat", pa.float64()),
        pa.field("lon", pa.float64()),
        # Direction in degrees (nullable — may be None)
        pa.field("heading", pa.int64()),
        # Next station identifiers
        pa.field("next_station_id", pa.string()),
        pa.field("next_station_name", pa.string()),
        # Destination identifiers
        pa.field("destination_id", pa.string()),
        pa.field("destination_name", pa.string()),
        # Prediction timestamps (ISO format strings from CTA API)
        pa.field("prediction_time", pa.string()),
        pa.field("predicted_arrival_time", pa.string()),
        # Status flags
        pa.field("is_approaching", pa.bool_()),
        pa.field("is_delayed", pa.bool_()),
    ]
)

# ---------------------------------------------------------------------------
# Weather schema
# ---------------------------------------------------------------------------
# Fields match the unified record produced by WeatherDaemon._merge_station_weather()
# which combines:
#   - NWS fields from _parse_hourly_forecast_response()
#   - Open-Meteo fields from _parse_current_weather_response()
#   - Station metadata added in _merge_station_weather()
#
# All weather measurement fields are nullable (not all sources always provide
# every value, and merge_weather_sources() skips None values).

WEATHER_SCHEMA: pa.Schema = pa.schema(
    [
        # Station identification (added by _merge_station_weather)
        pa.field("station_id", pa.string()),
        pa.field("nws_grid_id", pa.string()),
        pa.field("open_meteo_grid_id", pa.string()),
        # Station GPS coordinates (not provider grid coordinates)
        pa.field("latitude", pa.float64()),
        pa.field("longitude", pa.float64()),
        # Unix timestamp of data collection (time.time() from daemon)
        pa.field("collection_timestamp", pa.float64()),
        # NWS fields (from _parse_hourly_forecast_response)
        pa.field("start_time", pa.string()),
        pa.field("end_time", pa.string()),
        pa.field("temperature_f", pa.float64()),
        pa.field("prob_precip_pct", pa.float64()),
        pa.field("dewpoint_f", pa.float64()),
        pa.field("humidity_pct", pa.float64()),
        pa.field("wind_speed_mph", pa.float64()),
        pa.field("wind_direction", pa.string()),
        pa.field("forecast_desc", pa.string()),
        # Open-Meteo supplementary fields (from _parse_current_weather_response)
        # Note: timestamp from Open-Meteo (ISO string)
        pa.field("timestamp", pa.string()),
        pa.field("visibility_mi", pa.float64()),
        pa.field("snow_depth_in", pa.float64()),
        pa.field("surface_pressure_hpa", pa.float64()),
        pa.field("wind_gusts_mph", pa.float64()),
        pa.field("apparent_temp_f", pa.float64()),
        pa.field("rain_in", pa.float64()),
        pa.field("showers_in", pa.float64()),
        pa.field("snowfall_in", pa.float64()),
    ]
)
