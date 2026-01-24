"""Chicago Open Data CTA track geometry (shape) client.

Track shapes are extremely stable reference data. This module therefore provides:
- A robust API client with retries and structured logging
- Minimal normalization into a stable schema suitable for long-term caching
- A TTL-backed cache builder (via `cta_eta.cache.CachedData`) for monthly refreshes

Data source:
Chicago Open Data (Socrata) dataset for CTA 'L' Track Segments (MultiLineString geometry).

Example raw geometry (MultiLineString):
    {
        "type": "MultiLineString",
        "coordinates": [
            [
                [-87.62820914531717, 41.87690834080102],
                [-87.62756718362677, 41.87693211074777],
                [-87.62603003565505, 41.87670362155761],
            ]
        ],
    }

Example normalized record (truncated):
    {
        "segment_id": "a1b2c3d4e5f6a7b8",
        "lines": ["Brown", "Orange", "Pink", "Purple (Express)"],
        "description": "Tower 12 to Library",
        "endpoint_a": "Tower 12",
        "endpoint_b": "Library",
        "track_type": "Elevated or At Grade",
        "legend": "ML",
        "shape_len": 647.793224715,
        "geometry": [[[...]]],
        "start_lat": 41.87690834080102,
        "start_lon": -87.62820914531717,
        "end_lat": 41.87670362155761,
        "end_lon": -87.62603003565505,
    }
"""

from __future__ import annotations

import hashlib
from typing import Any, Final

import httpx
import stamina

### OWN MODULES
from cta_eta.data_collection.config import get_config_section, load_config
from cta_eta.data_collection.logging import get_logger, log_api_call
from cta_eta.data_collection.storage_cache.cache import CachedData, create_cached_data
from cta_eta.data_collection.utils import validate_lat_lon

logger = get_logger(__name__)

CHICAGO_OPEN_DATA_RESOURCE_BASE: Final[str] = "https://data.cityofchicago.org/resource"
CTA_TRACK_SHAPES_DATASET_ID: Final[str] = "xbyr-jnvx"
MIN_POINT_DIMENSIONS: Final[int] = 2

config = load_config()
retry_config = get_config_section("retry", config=config)
MAX_RETRY_ATTEMPTS: Final[int] = int(retry_config.get("max_retry_attempts", 10))


def _get_chidata_headers(
    cfg: dict[str, dict[str, str | int | float | bool]],
) -> dict[str, str]:
    secrets = cfg.get("secrets", {})
    token = str(secrets.get("chidata_app_token", "")).strip()
    secret = str(secrets.get("chidata_app_secret", "")).strip()

    if not token:
        msg = (
            "CHIDATA_APP_TOK must be set (via .env) to access Chicago Open Data "
            "reliably with adequate rate limits."
        )
        raise ValueError(msg)
    if not secret:
        msg = "CHIDATA_APP_SECRET must be set (via .env) to access Chicago Open Data "
        "reliably with adequate rate limits."
        raise ValueError(msg)

    headers: dict[str, str] = {
        "X-App-Token": token,
        "X-App-Secret": secret,
    }
    return headers


def _extract_multilinestring_coords(
    geom: dict[str, object],
) -> list[list[list[float]]] | None:
    """Extract MultiLineString coordinates as list[lines][points][lon/lat]."""
    if not isinstance(geom, dict):
        return None
    if geom.get("type") != "MultiLineString":
        return None
    coords = geom.get("coordinates")
    if not isinstance(coords, list) or not coords:
        return None

    cleaned: list[list[list[float]]] = []
    for line in coords:
        if not isinstance(line, list) or not line:
            continue
        cleaned_line: list[list[float]] = [
            [float(pt[0]), float(pt[1])]
            for pt in line
            if isinstance(pt, list)
            and len(pt) >= MIN_POINT_DIMENSIONS
            and isinstance(pt[0], (int, float))
            and isinstance(pt[1], (int, float))
        ]
        if cleaned_line:
            cleaned.append(cleaned_line)

    return cleaned or None


def _parse_description_endpoints(description: str) -> tuple[str | None, str | None]:
    """Parse 'A to B' endpoints from the description field."""
    desc = description.strip()
    if not desc:
        return desc, desc
    if " to " not in desc:
        return desc, desc
    left, right = desc.split(" to ", 1)
    left = left.strip()
    right = right.strip()
    if not left or not right:
        return desc, desc
    return left, right


def _extract_segment_endpoints(
    coords: list[list[list[float]]],
) -> tuple[float, float, float, float]:
    """Extract start/end lon/lat from MultiLineString coordinates.

    Uses the first point of the first line and the last point of the last line.
    """
    start_lon, start_lat = coords[0][0]
    end_lon, end_lat = coords[-1][-1]
    return start_lon, start_lat, end_lon, end_lat


def _extract_bbox(coords: list[list[list[float]]]) -> tuple[float, float, float, float]:
    """Compute (min_lon, min_lat, max_lon, max_lat) for a MultiLineString."""
    min_lon = float("inf")
    min_lat = float("inf")
    max_lon = float("-inf")
    max_lat = float("-inf")

    for line in coords:
        for lon, lat in line:
            min_lon = min(min_lon, lon)
            min_lat = min(min_lat, lat)
            max_lon = max(max_lon, lon)
            max_lat = max(max_lat, lat)

    validate_lat_lon(min_lat, min_lon)
    validate_lat_lon(max_lat, max_lon)

    return min_lon, min_lat, max_lon, max_lat


def normalize_track_shapes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize raw track shape rows into a stable schema.

    Output schema (one row per track segment):
    - segment_id: str  (Socrata row id)
    - lines: list[str]
    - description: str | None
    - endpoint_a: str | None  (parsed from description 'A to B')
    - endpoint_b: str | None
    - track_type: str | None  (e.g., 'Elevated or At Grade', 'Subway')
    - legend: str | None
    - shape_len: float | None
    - geometry: dict[str, object]  (GeoJSON MultiLineString)
    - bbox: dict[str, float]  (min/max lon/lat for quick candidate filtering)
    - start_lat, start_lon, end_lat, end_lon: float
    """
    normalized: list[dict[str, Any]] = []

    for row in rows:
        geom = row.get("the_geom")
        if not isinstance(geom, dict):
            continue
        coords = _extract_multilinestring_coords(geom)
        if coords is None:
            continue

        lines_raw = str(row.get("lines", "")).strip()
        lines = [part.strip() for part in lines_raw.split(",") if part.strip()]

        description_raw = row.get("description")
        description = (
            str(description_raw).strip() if description_raw not in (None, "") else None
        )
        endpoint_a, endpoint_b = (
            _parse_description_endpoints(description) if description else (None, None)
        )

        track_type_raw = row.get("type")
        track_type = (
            str(track_type_raw).strip() if track_type_raw not in (None, "") else None
        )
        legend_raw = row.get("legend")
        legend = str(legend_raw).strip() if legend_raw not in (None, "") else None

        shape_len_raw = row.get("shape_len")
        shape_len: float | None
        if shape_len_raw in (None, ""):
            shape_len = None
        else:
            try:
                shape_len = float(shape_len_raw)
            except (TypeError, ValueError):
                shape_len = None

        start_lon, start_lat, end_lon, end_lat = _extract_segment_endpoints(coords)
        min_lon, min_lat, max_lon, max_lat = _extract_bbox(coords)

        # Socrata's stable row-id metadata is not reliably exposed via the resource API.
        # Instead, derive a deterministic, compact identifier from stable fields.
        fingerprint = "|".join(
            [
                ",".join(lines),
                description or "",
                track_type or "",
                legend or "",
                "" if shape_len is None else f"{shape_len:.6f}",
                f"{start_lon:.6f},{start_lat:.6f}",
                f"{end_lon:.6f},{end_lat:.6f}",
            ]
        ).encode("utf-8")
        segment_id = hashlib.blake2s(fingerprint, digest_size=8).hexdigest()

        normalized.append(
            {
                "segment_id": segment_id,
                "lines": lines,
                "description": description,
                "endpoint_a": endpoint_a,
                "endpoint_b": endpoint_b,
                "track_type": track_type,
                "legend": legend,
                "shape_len": shape_len,
                "geometry": {"type": "MultiLineString", "coordinates": coords},
                "bbox": {
                    "min_lon": min_lon,
                    "min_lat": min_lat,
                    "max_lon": max_lon,
                    "max_lat": max_lat,
                },
                "start_lat": start_lat,
                "start_lon": start_lon,
                "end_lat": end_lat,
                "end_lon": end_lon,
            }
        )

    # Deterministic ordering for cache diffs / reproducibility.
    normalized.sort(key=lambda r: r["segment_id"])
    return normalized


@stamina.retry(on=httpx.HTTPStatusError, attempts=MAX_RETRY_ATTEMPTS)
@log_api_call(logger)
def fetch_track_shapes_raw(
    client: httpx.Client,
    cfg: dict[str, dict[str, str | int | float | bool]],
    *,
    page_size: int = 5000,
) -> list[dict[str, Any]]:
    """Fetch raw track shape records from Chicago Open Data with pagination."""
    headers = _get_chidata_headers(cfg)

    url = f"{CHICAGO_OPEN_DATA_RESOURCE_BASE}/{CTA_TRACK_SHAPES_DATASET_ID}.json"
    select = "the_geom,lines,description,type,legend,shape_len"

    rows: list[dict[str, Any]] = []
    offset = 0

    while True:
        response = client.get(
            url,
            headers=headers,
            params={
                "$select": select,
                "$limit": page_size,
                "$offset": offset,
            },
        )
        response.raise_for_status()
        page = response.json()
        if not isinstance(page, list):
            msg = f"Unexpected Chicago Open Data response type: {type(page)}"
            raise TypeError(msg)
        if not page:
            break

        rows.extend(page)
        offset += page_size

    return rows


def get_track_geometry_cache(
    cfg: dict[str, dict[str, str | int | float | bool]] | None = None,
) -> CachedData[list[dict[str, Any]]]:
    """Build a TTL-backed cache for track geometry.

    Cache TTL is configured via `[cache].track_geometry_ttl` (recommended: 30 days).
    """
    if cfg is None:
        cfg = config

    def _fetch() -> list[dict[str, Any]]:
        with httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        ) as client:
            rows = fetch_track_shapes_raw(client, cfg)
        return normalize_track_shapes(rows)

    return create_cached_data("track_geometry", cfg, _fetch)
