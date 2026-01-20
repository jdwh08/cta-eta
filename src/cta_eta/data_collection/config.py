"""Hybrid configuration loader merging TOML operational settings with .env secrets."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv


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
