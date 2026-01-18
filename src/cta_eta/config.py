"""Hybrid configuration loader merging TOML operational settings with .env secrets."""

import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv


def _load_config_from_path(
    config_path: Path,
) -> dict[str, dict[str, str | int | float | bool]]:
    """Load configuration from a specific TOML file path and merge with .env secrets.

    This is an internal function for testability. Use load_config() in production code.

    Args:
        config_path: Path to the config.toml file

    Returns:
        Nested dict with configuration sections:
        - collection: polling intervals
        - retry: retry attempts and backoff settings
        - storage: data path, partitioning, compression
        - logging: log level, format, paths
        - cache: TTL settings for static data
        - secrets: API keys from environment variables

    Raises:
        FileNotFoundError: If config.toml not found
        tomllib.TOMLDecodeError: If config.toml has invalid syntax

    """
    # Load environment variables from .env file
    load_dotenv()

    # Load TOML configuration from specified path
    with config_path.open("rb") as f:
        config = tomllib.load(f)

    # Add secrets section from environment variables
    config["secrets"] = {
        "cta_api_key": os.getenv("CTA_API_KEY", ""),
        "chidata_app_token": os.getenv("CHIDATA_APP_TOK", ""),
        "chidata_app_secret": os.getenv("CHIDATA_APP_SECRET", ""),
    }

    return config


def load_config() -> dict[str, dict[str, str | int | float | bool]]:
    """Load configuration from config.toml and merge with .env secrets.

    Loads configuration from the default location: project root / config.toml
    (relative to this module's location).

    Returns:
        Nested dict with configuration sections:
        - collection: polling intervals
        - retry: retry attempts and backoff settings
        - storage: data path, partitioning, compression
        - logging: log level, format, paths
        - cache: TTL settings for static data
        - secrets: API keys from environment variables

    Raises:
        FileNotFoundError: If config.toml not found
        tomllib.TOMLDecodeError: If config.toml has invalid syntax

    """
    # Load TOML configuration from project root
    config_path = Path(__file__).parent.parent.parent / "config.toml"
    return _load_config_from_path(config_path)
