"""Hybrid configuration loader merging TOML operational settings with .env secrets."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

# Keys that should be redacted in logs
_SENSITIVE_KEYS: Final[set[str]] = {
    "cta_api_key",
    "chidata_app_token",
    "chidata_app_secret",
    "openweathermap_api_key",
}


def _sanitize_config_for_logging(
    config: dict[str, dict[str, str | int | float | bool]],
) -> dict[str, dict[str, str | int | float | bool]]:
    """Sanitize configuration dictionary for safe logging.

    Replaces sensitive values (API keys, tokens, secrets) with "<REDACTED>"
    to prevent accidental exposure in logs.

    Args:
        config: Configuration dictionary to sanitize

    Returns:
        Sanitized copy of configuration dictionary

    """
    sanitized: dict[str, dict[str, str | int | float | bool]] = {}
    for section_name, section_data in config.items():
        if not isinstance(section_data, dict):
            sanitized[section_name] = section_data
            continue

        sanitized_section: dict[str, str | int | float | bool] = {}
        for key, value in section_data.items():
            if (
                key.lower() in _SENSITIVE_KEYS
                or "key" in key.lower()
                or "secret" in key.lower()
                or "token" in key.lower()
            ):
                sanitized_section[key] = "<REDACTED>"
            else:
                sanitized_section[key] = value
        sanitized[section_name] = sanitized_section

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

    return config


# TODO(jdwh08): break up config validation into smaller functions, one for each feature
def validate_config(
    config: dict[str, dict[str, str | int | float | bool]],
    required_features: list[str] | None = None,
) -> None:
    """Validate configuration for required credentials based on enabled features.

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
    features = config.get("features", {})

    # Determine which features to validate
    if required_features is None:
        # Validate based on enabled features in config
        required_features = []
        if features.get("train_positions", False):
            required_features.append("train_positions")
        if features.get("weather_collection", False):
            required_features.append("weather_collection")
        if features.get("station_data", False):
            required_features.append("station_data")
        # OpenWeatherMap is optional fallback, don't require it

    missing_credentials: list[str] = []

    if "train_positions" in required_features and not secrets.get("cta_api_key"):
        missing_credentials.append("CTA_API_KEY (required for train_positions)")

    if "weather_collection" in required_features:
        nws_app_name = os.getenv("NWS_APP_NAME")
        nws_email = os.getenv("NWS_EMAIL")
        if not nws_app_name:
            missing_credentials.append("NWS_APP_NAME (required for weather_collection)")
        if not nws_email:
            missing_credentials.append("NWS_EMAIL (required for weather_collection)")

    if "station_data" in required_features:
        if not secrets.get("chidata_app_token"):
            missing_credentials.append("CHIDATA_APP_TOK (required for station_data)")
        if not secrets.get("chidata_app_secret"):
            missing_credentials.append("CHIDATA_APP_SECRET (required for station_data)")

    if missing_credentials:
        msg = (
            "Missing required credentials for enabled features:\n"
            + "\n".join(f"  - {cred}" for cred in missing_credentials)
            + "\n\nSet these environment variables in your .env file."
        )
        raise ValueError(msg)


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


def get_config_section(
    section: str,
    *,
    config: dict[str, dict[str, str | int | float | bool]] | None = None,
    default: dict[str, str | int | float | bool] | None = None,
) -> dict[str, str | int | float | bool]:
    """Safely get a configuration section with fallback defaults.

    Args:
        section: Section name to retrieve
        config: Configuration dictionary. If None, uses load_config() to load the config.
        default: Default value if section is missing (defaults to empty dict)

    Returns:
        Configuration section dictionary

    """
    if config is None:
        config = load_config()

    if default is None:
        default = {}
    return (
        config.get(section, default)
        if isinstance(config.get(section), dict)
        else default
    )
