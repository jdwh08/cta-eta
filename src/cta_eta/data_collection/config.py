"""Hybrid configuration loader merging TOML operational settings with .env secrets."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Final, cast

from dotenv import load_dotenv

# Keys that should be redacted in logs
_SENSITIVE_KEYS: Final[set[str]] = {
    "cta_api_key",
    "chidata_app_token",
    "chidata_app_secret",
    "openweathermap_api_key",
    "s3_bucket",
    "gcs_bucket",
    "azure_bucket",
}


def _sanitize_config_for_logging(
    config: dict[str, str | int | float | bool | dict[str, str | int | float | bool]],
) -> dict[str, str | int | float | bool | dict[str, str | int | float | bool]]:
    """Sanitize configuration dictionary for safe logging.

    Replaces sensitive values (API keys, tokens, secrets) with "<REDACTED>"
    to prevent accidental exposure in logs.

    Args:
        config: Configuration dictionary to sanitize

    Returns:
        Sanitized copy of configuration dictionary

    """
    sanitized: dict[
        str, str | int | float | bool | dict[str, str | int | float | bool]
    ] = {}
    for section_name, section_data in config.items():
        if not isinstance(section_data, dict):
            if (
                section_name.lower() in _SENSITIVE_KEYS
                or "key" in section_name.lower()
                or "secret" in section_name.lower()
                or "token" in section_name.lower()
            ):
                sanitized[section_name] = "<REDACTED>"
            else:
                sanitized[section_name] = section_data
            continue

        sanitized_section: dict[
            str, str | int | float | bool | dict[str, str | int | float | bool]
        ] = {}
        for key, value in section_data.items():
            if (
                key.lower() in _SENSITIVE_KEYS
                or "key" in key.lower()
                or "secret" in key.lower()
                or "token" in key.lower()
            ):
                sanitized_section[key] = "<REDACTED>"
            elif isinstance(value, dict):
                sanitized_section[key] = _sanitize_config_for_logging(
                    cast(
                        "dict[str, str | int | float | bool | dict[str, str | int | float | bool]]",
                        value,
                    )
                )  # ty:ignore[invalid-assignment]
            else:
                sanitized_section[key] = value
        sanitized[section_name] = sanitized_section  # ty:ignore[invalid-assignment]

    return sanitized


def _load_config_from_path(
    config_path: Path,
) -> dict[str, dict[str, str | int | float | bool]]:
    """Load configuration from a specific TOML file path and merge with .env secrets.

    This is an internal function for testability. Use `load_config()` in production code.

    Args:
        config_path: Path to the `config.toml` file

    Returns:
        Nested dict with configuration sections.

    Raises:
        FileNotFoundError: If `config.toml` not found
        tomllib.TOMLDecodeError: If `config.toml` has invalid syntax

    """
    # Load environment variables from .env file.
    load_dotenv()

    with config_path.open("rb") as f:
        config = tomllib.load(f)

    # Add secrets section from environment variables.
    # These default to empty strings so the codebase can run partial pipelines
    # (e.g., weather-only) without requiring unrelated credentials.
    cta_api_key = (os.getenv("CTA_API_KEY") or "").strip()
    chidata_app_token = (os.getenv("CHIDATA_APP_TOK") or "").strip()
    chidata_app_secret = (os.getenv("CHIDATA_APP_SECRET") or "").strip()
    openweathermap_api_key = (os.getenv("OPENWEATHERMAP_API_KEY") or "").strip()
    config["secrets"] = {
        "cta_api_key": cta_api_key,
        "chidata_app_token": chidata_app_token,
        "chidata_app_secret": chidata_app_secret,
        "openweathermap_api_key": openweathermap_api_key,
    }

    # Inject storage compaction bucket names and optional S3 endpoint from env
    storage = config.setdefault("storage", {})
    if not isinstance(storage, dict):
        storage = {}
        config["storage"] = storage
    compaction = storage.setdefault("compaction", {})
    if not isinstance(compaction, dict):
        compaction = {}
        storage["compaction"] = compaction
    compaction["s3_bucket"] = (os.getenv("S3_BUCKET") or "").strip()
    compaction["s3_endpoint_url"] = (os.getenv("S3_ENDPOINT_URL") or "").strip()
    compaction["gcs_bucket"] = (os.getenv("GCS_BUCKET") or "").strip()
    compaction["azure_bucket"] = (os.getenv("AZURE_BUCKET") or "").strip()

    return config


def get_required_credentials(
    config: dict[str, dict[str, str | int | float | bool]],
    required_features: list[str] | None = None,
) -> list[str]:
    """Get the required credentials for the enabled features.

    Args:
        config: Configuration dictionary from load_config()
        required_features: List of feature names to get required credentials for. If None,
            gets required credentials for all enabled features.

    Returns:
        List of required credentials

    """
    if required_features is None:
        required_features = [
            feature
            for feature in config.get("features", {})
            if config.get("features", {}).get(feature, False)
        ]
    required_credentials: list[str] = []
    if "station_data" in required_features or "weather_collection" in required_features:
        required_credentials.append("chidata_app_token")
        required_credentials.append("chidata_app_secret")
    if "train_positions" in required_features:
        required_credentials.append("cta_api_key")
    if "weather_collection" in required_features:
        required_credentials.append("nws_app_name")
        required_credentials.append("nws_email")
    if "weather_collection_fallback" in required_features:
        required_credentials.append("openweathermap_api_key")
    return required_credentials


def validate_config_secrets(
    config: dict[str, dict[str, str | int | float | bool]],
    required_features: list[str] | None = None,
) -> None:
    """Validate configuration for required credential secrets based on enabled features.

    Checks that all required credentials are present for enabled features.
    Raises ValueError with clear error messages if validation fails.

    Args:
        config: Configuration dictionary from load_config()
        required_features: List of feature names to validate. If None, validates all
            features that are enabled in config["features"]. Features:
            - "train_positions": Requires CTA_API_KEY
            - "weather_collection": Requires NWS_APP_NAME and NWS_EMAIL
            - "station_data": Requires CHIDATA_APP_TOK and CHIDATA_APP_SECRET
            - "openweathermap_fallback": Requires OPENWEATHERMAP_API_KEY (optional)

    Raises:
        ValueError: If required credentials are missing for enabled features

    """
    secrets = config.get("secrets", {})

    # Determine which features to validate
    required_credentials = set(get_required_credentials(config, required_features))
    missing_credentials: set[str] = set()

    if "cta_api_key" in required_credentials and not secrets.get("cta_api_key"):
        missing_credentials.add("CTA_API_KEY (required for train_positions)")

    if "nws_app_name" in required_credentials or "nws_email" in required_credentials:
        nws_app_name = os.getenv("NWS_APP_NAME")
        nws_email = os.getenv("NWS_EMAIL")
        if not nws_app_name:
            missing_credentials.add("NWS_APP_NAME (required for weather_collection)")
        if not nws_email:
            missing_credentials.add("NWS_EMAIL (required for weather_collection)")

    if (
        "chidata_app_token" in required_credentials
        or "chidata_app_secret" in required_credentials
    ):
        if not secrets.get("chidata_app_token"):
            missing_credentials.add(
                "CHIDATA_APP_TOK (required for station_data in weather_collection)"
            )
        if not secrets.get("chidata_app_secret"):
            missing_credentials.add(
                "CHIDATA_APP_SECRET (required for station_data in weather_collection)"
            )

    if missing_credentials:
        msg = (
            "Missing required credentials for enabled features:\n"
            + "\n".join(f"  - {cred}" for cred in missing_credentials)
            + "\n\nSet these environment variables in your .env file."
        )
        raise ValueError(msg)


def _validate_config_file_settings_station_data(
    config: dict[str, dict[str, str | int | float | bool]],
) -> None:
    """Validate configuration for required file settings for station data.

    Checks that all required file settings are present for station data.
    Raises ValueError with clear error messages if validation fails.
    """
    cache_config = config.get("cache", {})
    if not isinstance(cache_config, dict):
        msg = f"Configuration path 'cache': section 'cache' must be a dictionary, got {type(cache_config).__name__}."
        raise TypeError(msg)
    if "stations_ttl" not in cache_config or not isinstance(
        cache_config["stations_ttl"], int
    ):
        msg = "Configuration path 'cache': section 'cache' must contain 'stations_ttl' key with an integer value."
        raise ValueError(msg)
    if "track_geometry_ttl" not in cache_config or not isinstance(
        cache_config["track_geometry_ttl"], int
    ):
        msg = "Configuration path 'cache': section 'cache' must contain 'track_geometry_ttl' key with an integer value."
        raise ValueError(msg)
    if "weather_mapping_ttl" not in cache_config or not isinstance(
        cache_config["weather_mapping_ttl"], int
    ):
        msg = "Configuration path 'cache': section 'cache' must contain 'weather_mapping_ttl' key with an integer value."
        raise ValueError(msg)


def _validate_config_file_settings_train_positions(
    config: dict[str, dict[str, str | int | float | bool]],
) -> None:
    """Validate configuration for required file settings for train positions."""
    collection_config = config.get("collection", {})
    if not isinstance(collection_config, dict):
        msg = f"Configuration path 'collection': section 'collection' must be a dictionary, got {type(collection_config).__name__}."
        raise TypeError(msg)
    if "train_interval_seconds" not in collection_config or not isinstance(
        collection_config["train_interval_seconds"], int
    ):
        msg = "Configuration path 'collection': section 'collection' must contain 'train_interval_seconds' key with an integer value."
        raise ValueError(msg)
    if "rate_limits" not in collection_config or not isinstance(
        collection_config["rate_limits"], dict
    ):
        msg = "Configuration path 'collection': section 'collection' must contain 'rate_limits' key with a dictionary value."
        raise ValueError(msg)
    if "cta" not in collection_config["rate_limits"] or not isinstance(
        collection_config["rate_limits"]["cta"], dict
    ):
        msg = "Configuration path 'collection': section 'collection' must contain 'rate_limits.cta' key with a dictionary value."
        raise ValueError(msg)
    if "max_per_second" not in collection_config["rate_limits"][
        "cta"
    ] or not isinstance(
        collection_config["rate_limits"]["cta"]["max_per_second"], float
    ):
        msg = "Configuration path 'collection': section 'collection' must contain 'rate_limits.cta.max_per_second' key with a float value."
        raise ValueError(msg)
    if "max_at_once" not in collection_config["rate_limits"]["cta"] or not isinstance(
        collection_config["rate_limits"]["cta"]["max_at_once"], int
    ):
        msg = "Configuration path 'collection': section 'collection' must contain 'rate_limits.cta.max_at_once' key with an integer value."
        raise ValueError(msg)


def _validate_config_file_settings_weather_collection(
    config: dict[str, dict[str, str | int | float | bool]],
) -> None:
    """Validate configuration for required file settings for weather collection."""
    rate_limits_config = config.get("rate_limits", {})
    if not isinstance(rate_limits_config, dict):
        msg = f"Configuration path 'rate_limits': section 'rate_limits' must be a dictionary, got {type(rate_limits_config).__name__}."
        raise TypeError(msg)
    if "nws" not in rate_limits_config or not isinstance(
        rate_limits_config["nws"], dict
    ):
        msg = "Configuration path 'rate_limits': section 'rate_limits' must contain 'nws' key with a dictionary value."
        raise ValueError(msg)
    if "max_per_second" not in rate_limits_config["nws"] or not isinstance(
        rate_limits_config["nws"]["max_per_second"], float | int
    ):
        msg = "Configuration path 'rate_limits': section 'rate_limits' must contain 'nws.max_per_second' key with a float value."
        raise ValueError(msg)
    if "max_at_once" not in rate_limits_config["nws"] or not isinstance(
        rate_limits_config["nws"]["max_at_once"], int
    ):
        msg = "Configuration path 'rate_limits': section 'rate_limits' must contain 'nws.max_at_once' key with an integer value."
        raise ValueError(msg)


def _validate_config_file_settings_weather_collection_fallback(
    config: dict[str, dict[str, str | int | float | bool]],
) -> None:
    """Validate configuration for required file settings for weather collection fallback."""
    rate_limits_config = config.get("rate_limits", {})
    if not isinstance(rate_limits_config, dict):
        msg = f"Configuration path 'rate_limits': section 'rate_limits' must be a dictionary, got {type(rate_limits_config).__name__}."
        raise TypeError(msg)
    if "openweathermap" not in rate_limits_config or not isinstance(
        rate_limits_config["openweathermap"], dict
    ):
        msg = "Configuration path 'rate_limits': section 'rate_limits' must contain 'openweathermap' key with a dictionary value."
        raise ValueError(msg)
    if "max_per_second" not in rate_limits_config["openweathermap"] or not isinstance(
        rate_limits_config["openweathermap"]["max_per_second"], float | int
    ):
        msg = "Configuration path 'rate_limits': section 'rate_limits' must contain 'openweathermap.max_per_second' key with a float value."
        raise ValueError(msg)
    if "max_at_once" not in rate_limits_config["openweathermap"] or not isinstance(
        rate_limits_config["openweathermap"]["max_at_once"], int
    ):
        msg = "Configuration path 'rate_limits': section 'rate_limits' must contain 'openweathermap.max_at_once' key with an integer value."
        raise ValueError(msg)


def _validate_config_file_settings_storage(  # noqa: C901
    config: dict[str, dict[str, str | int | float | bool]],
) -> None:
    """Validate configuration for storage.immediate and storage.compaction."""
    storage = config.get("storage", {})
    if not isinstance(storage, dict):
        msg = f"Configuration path 'storage': must be a dictionary, got {type(storage).__name__}."
        raise TypeError(msg)
    immediate = storage.get("immediate", {})
    if not isinstance(immediate, dict):
        msg = "Configuration path 'storage.immediate': must be a dictionary."
        raise TypeError(msg)
    if "data_path" not in immediate:
        msg = "Configuration path 'storage.immediate': must contain 'data_path'."
        raise ValueError(msg)
    if "journal_rotation_minutes" not in immediate or not isinstance(
        immediate["journal_rotation_minutes"], int
    ):
        msg = "Configuration path 'storage.immediate': must contain 'journal_rotation_minutes' (integer)."
        raise ValueError(msg)
    if "partition_hour" not in immediate or not isinstance(
        immediate["partition_hour"], int
    ):
        msg = "Configuration path 'storage.immediate': must contain 'partition_hour' (integer)."
        raise ValueError(msg)
    compaction = storage.get("compaction", {})
    if not isinstance(compaction, dict):
        msg = "Configuration path 'storage.compaction': must be a dictionary."
        raise TypeError(msg)
    if "backend" not in compaction:
        msg = "Configuration path 'storage.compaction': must contain 'backend'."
        raise ValueError(msg)
    if "staging_path" not in compaction:
        msg = "Configuration path 'storage.compaction': must contain 'staging_path'."
        raise ValueError(msg)
    if "upload_prefix" not in compaction:
        msg = "Configuration path 'storage.compaction': must contain 'upload_prefix'."
        raise ValueError(msg)
    if "archive_path" not in compaction:
        msg = "Configuration path 'storage.compaction': must contain 'archive_path'."
        raise ValueError(msg)
    if "journal_retention_days" not in compaction or not isinstance(
        compaction["journal_retention_days"], int
    ):
        msg = "Configuration path 'storage.compaction': must contain 'journal_retention_days' (integer)."
        raise ValueError(msg)


def validate_config_file_settings(
    config: dict[str, dict[str, str | int | float | bool]],
    required_features: list[str] | None = None,
) -> None:
    """Validate configuration for required file settings based on enabled features.

    Checks that all required file settings are present for enabled features.
    Raises ValueError with clear error messages if validation fails.

    Args:
        config: Configuration dictionary from load_config()
        required_features: List of feature names to validate. If None, validates all
            features that are enabled in config["features"].

    """
    if required_features is None:
        required_features = [
            feature
            for feature in config.get("features", {})
            if config.get("features", {}).get(feature, False)
        ]
    if "station_data" in required_features:
        _validate_config_file_settings_station_data(config)
    if "train_positions" in required_features:
        _validate_config_file_settings_train_positions(config)
        _validate_config_file_settings_storage(config)
    if "weather_collection" in required_features:
        _validate_config_file_settings_weather_collection(config)
        _validate_config_file_settings_storage(config)
    if "weather_collection_fallback" in required_features:
        _validate_config_file_settings_weather_collection_fallback(config)


def load_config() -> dict[str, dict[str, str | int | float | bool]]:
    """Load configuration from `config.toml` and merge with `.env` secrets.

    Loads configuration from the default location: project root / `config.toml`.

    Returns:
        Nested dict with configuration sections.

    Raises:
        FileNotFoundError: If `config.toml` not found
        tomllib.TOMLDecodeError: If `config.toml` has invalid syntax

    """
    # `.../src/cta_eta/data_collection/config.py` → project root is 4 levels up.
    config_path = Path(__file__).resolve().parents[3] / "config.toml"
    return _load_config_from_path(config_path)


def validate_config(
    config: dict[str, dict[str, str | int | float | bool]],
    required_features: list[str] | None = None,
) -> None:
    """Validate configuration for required credentials and file settings based on enabled features."""
    validate_config_secrets(config, required_features)
    validate_config_file_settings(config, required_features)


def get_config_section(
    section: str,
    *,
    config: dict[str, dict[str, str | int | float | bool]] | None = None,
    _full_path: str | None = None,
) -> dict[str, str | int | float | bool]:
    """Get a configuration section. Fails fast if the path is missing or invalid.

    Args:
        section: Section name to retrieve. Use a period for sub-sections
            (e.g. "rate_limits.nws" for the "nws" section inside "rate_limits").
        config: Configuration dictionary. If None, uses load_config().
        _full_path: Internal: used for error messages when recursing. Do not pass.

    Returns:
        Configuration section dictionary (always a dict of str keys to primitives).

    Raises:
        ValueError: If a segment in the path is missing. Includes the full path
            and the failing segment.
        TypeError: If a segment in the path is not a dict (cannot be traversed).
            Includes the full path, the segment, and the actual type.

    Examples:
        >>> get_config_section("rate_limits")
        {'cta': {...}, 'nws': {...}, ...}
        >>> get_config_section("rate_limits.nws")
        {'max_per_second': 5, 'max_at_once': 5}

    """
    if config is None:
        config = load_config()

    full_path = section if _full_path is None else _full_path

    subsection_split = section.find(".")
    if subsection_split > 0:
        cur_subsection = section[:subsection_split]
        next_subsection = section[subsection_split + 1 :]
        if cur_subsection not in config:
            msg = (
                f"Configuration path '{full_path}': section '{cur_subsection}' "
                "is missing."
            )
            raise ValueError(msg)
        val = config[cur_subsection]
        if not isinstance(val, dict):
            msg = (
                f"Configuration path '{full_path}': section '{cur_subsection}' "
                f"must be a dict, got {type(val).__name__}."
            )
            raise TypeError(msg)
        return get_config_section(
            next_subsection,
            config=cast("dict[str, dict[str, str | int | float | bool]]", val),
            _full_path=full_path,
        )

    if section not in config:
        msg = f"Configuration path '{full_path}': section '{section}' is missing."
        raise ValueError(msg)
    val = config[section]
    if not isinstance(val, dict):
        msg = (
            f"Configuration path '{full_path}': section '{section}' "
            f"must be a dict, got {type(val).__name__}."
        )
        raise TypeError(msg)
    return val
