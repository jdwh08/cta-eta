"""Chicago Open Data CTA stations client.

Provides fast, cached access to ~300 CTA station locations and metadata.
Stations rarely change (weeks/months), making them ideal for weekly TTL refresh.

Data source:
Chicago Data Portal (Socrata) dataset for CTA stations with GeoJSON coordinates.

Example raw station record:
    {
        "station_id": "900",
        "longname": "Howard",
        "address": "1649 W. Howard Street",
        "lines": "Red, Yellow, Purple, Evanston Express",
        "the_geom": {
            "type": "Point",
            "coordinates": [-87.672892, 42.019063]
        }
    }

Example normalized station:
    {
        "id": "900",
        "name": "Howard",
        "address": "1649 W. Howard Street",
        "lines": "Red, Yellow, Purple, Evanston Express",
        "latitude": 42.019063,
        "longitude": -87.672892
    }
"""

from __future__ import annotations

from typing import Any, Final

import httpx
import stamina

### OWN MODULES
from cta_eta.data_collection.config import load_config
from cta_eta.data_collection.logging import get_logger, log_api_call
from cta_eta.data_collection.storage_cache.cache import CachedData, create_cached_data

logger = get_logger(__name__)

CHICAGO_DATA_VIEWS_BASE: Final[str] = "https://data.cityofchicago.org/api/v3/views"
CTA_STATIONS_DATASET_ID: Final[str] = "3tzw-cg4m"
MIN_COORDINATE_DIMENSIONS: Final[int] = 2

config = load_config()
retry_config = config.get("retry", {})
MAX_RETRY_ATTEMPTS: Final[int] = int(retry_config.get("max_retry_attempts", 10))


def _get_chidata_headers(
    cfg: dict[str, dict[str, str | int | float | bool]],
) -> dict[str, str]:
    """Extract Chicago Data Portal credentials from config.

    Args:
        cfg: Configuration dict with secrets section

    Returns:
        Dict with X-App-Token and X-App-Secret headers

    Raises:
        ValueError: If credentials are missing from config

    """
    secrets = cfg.get("secrets", {})
    token = str(secrets.get("chidata_app_token", "")).strip()
    secret = str(secrets.get("chidata_app_secret", "")).strip()

    if not token:
        msg = (
            "CHIDATA_APP_TOK must be set (via .env) to access Chicago Data Portal "
            "reliably with adequate rate limits."
        )
        raise ValueError(msg)
    if not secret:
        msg = (
            "CHIDATA_APP_SECRET must be set (via .env) to access Chicago Data Portal "
            "reliably with adequate rate limits."
        )
        raise ValueError(msg)

    headers: dict[str, str] = {
        "X-App-Token": token,
        "X-App-Secret": secret,
    }
    return headers


@stamina.retry(on=httpx.HTTPStatusError, attempts=MAX_RETRY_ATTEMPTS)
@log_api_call(logger)
def get_cta_stations(
    client: httpx.Client,
    cfg: dict[str, dict[str, str | int | float | bool]],
) -> list[dict[str, Any]]:
    """Fetch CTA stations from Chicago Data Portal API.

    Args:
        client: httpx.Client instance for connection pooling
        cfg: Configuration dict with secrets

    Returns:
        List of raw station dicts from API

    Raises:
        httpx.HTTPStatusError: If API request fails (retried by stamina)
        TypeError: If API response is not a list

    """
    headers = _get_chidata_headers(cfg)
    url = f"{CHICAGO_DATA_VIEWS_BASE}/{CTA_STATIONS_DATASET_ID}/query.json"

    response = client.get(url, headers=headers)
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, list):
        msg = f"Unexpected Chicago Data Portal response type: {type(data)}"
        raise TypeError(msg)

    return data


def normalize_cta_stations(raw_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize CTA stations API response to flat schema.

    Extracts fields and flattens GeoJSON Point geometry to latitude/longitude.

    Args:
        raw_data: List of raw station dicts from API

    Returns:
        List of normalized station dicts with flat schema

    """
    normalized: list[dict[str, Any]] = []

    for station in raw_data:
        # Skip stations without geometry
        geom = station.get("the_geom")
        if not isinstance(geom, dict):
            continue

        # Extract GeoJSON Point coordinates [longitude, latitude]
        coordinates = geom.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) < MIN_COORDINATE_DIMENSIONS:
            continue

        longitude = float(coordinates[0])
        latitude = float(coordinates[1])

        # Extract station fields
        station_id = str(station.get("station_id", ""))
        name = str(station.get("longname", ""))
        address = str(station.get("address", ""))
        lines = str(station.get("lines", ""))

        normalized.append(
            {
                "id": station_id,
                "name": name,
                "address": address,
                "lines": lines,
                "latitude": latitude,
                "longitude": longitude,
            }
        )

    # Deterministic ordering for cache diffs / reproducibility
    normalized.sort(key=lambda r: r["id"])
    return normalized


def get_stations_cache(
    cfg: dict[str, dict[str, str | int | float | bool]] | None = None,
) -> CachedData[list[dict[str, Any]]]:
    """Build a TTL-backed cache for CTA stations.

    Cache TTL is configured via `[cache].stations_ttl` (recommended: 7 days).

    Args:
        cfg: Optional config dict (uses load_config() if not provided)

    Returns:
        CachedData instance configured for stations

    """
    if cfg is None:
        cfg = config

    def _fetch() -> list[dict[str, Any]]:
        with httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        ) as client:
            raw_data = get_cta_stations(client, cfg)
        return normalize_cta_stations(raw_data)

    return create_cached_data("stations", cfg, _fetch)
