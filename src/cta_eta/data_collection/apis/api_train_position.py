"""CTA Train Position API client with retry logic and response normalization.

This module provides functions to fetch train positions from the CTA Train Tracker API
and normalize nested JSON responses into flat records for Parquet storage.

API Documentation: https://www.transitchicago.com/developers/ttdocs/
Rate Limit: 50,000 requests/day (see Appendix D of API documentation)

All functions accept an httpx.AsyncClient parameter for dependency injection and proper
connection pooling management by the caller.

Example raw response:
{
    "ctatt": {
        "tmst": "2026-01-14T20:34:15",  # API timestamp
        "errCd": "0",
        "errNm": null,
        "route": [  # Array of train lines
            {
                "@name": "p",
                "train": [  # Array of trains on this line
                    {
                        "rn": "519",
                        "destSt": "30176",
                        "destNm": "Howard",
                        "trDr": "5",
                        "nextStaId": "40400",
                        "nextStpId": "30079",
                        "nextStaNm": "Noyes",
                        "prdt": "2026-01-14T20:33:52",
                        "arrT": "2026-01-14T20:34:52",
                        "isApp": "1",
                        "isDly": "0",
                        "flags": null,
                        "lat": "42.06106",
                        "lon": "-87.68393",
                        "heading": "150"
                    },
                    ...
                ]
            }
        ]
    }
}
"""

import os
from datetime import datetime
from typing import Any, Final

import httpx
import stamina

from cta_eta.data_collection.config import load_config
from cta_eta.data_collection.logging import get_logger, log_api_call
from cta_eta.data_collection.utils import validate_lat_lon

logger = get_logger(__name__)

# CTA API endpoint and configuration
TRAIN_POSITION_URL: Final[str] = (
    "http://lapi.transitchicago.com/api/1.0/ttpositions.aspx"
)
CTA_LINES: Final[list[str]] = ["red", "blue", "brn", "g", "org", "p", "pink", "y"]

# Load retry configuration from config.toml
config = load_config()
retry_config = config.get("retry", {})
MAX_RETRY_ATTEMPTS: Final[int] = int(retry_config.get("max_retry_attempts", 10))


@stamina.retry(on=httpx.HTTPStatusError, attempts=MAX_RETRY_ATTEMPTS)
@log_api_call(logger)
async def get_train_positions(client: httpx.AsyncClient) -> dict[str, Any]:
    """Fetch current train positions for all CTA lines from the Train Tracker API.

    Makes a single API call to retrieve positions for all 8 CTA train lines at once.
    Uses stamina retry decorator for resilience against transient HTTP errors.

    Args:
        client: HTTP client for API requests

    Returns:
        dict[str, Any]: Raw JSON response from the API.

    Raises:
        httpx.HTTPStatusError: After max retry attempts exhausted
        ValueError: If CTA_API_KEY environment variable not set

    """
    api_key = os.getenv("CTA_API_KEY")
    if not api_key:
        msg = "CTA_API_KEY environment variable not set"
        raise ValueError(msg)

    response = await client.get(
        TRAIN_POSITION_URL,
        params={
            "key": api_key,
            "rt": ",".join(CTA_LINES),
            "outputType": "JSON",
        },
    )
    response.raise_for_status()
    return response.json()


def normalize_train_positions(
    response: dict[str, Any], poll_timestamp: datetime
) -> list[dict[str, Any]]:
    """Normalize nested API response into flat records for Parquet storage.

    Extracts one row per train from the nested route/train structure, preserving both
    our polling timestamp and the API's timestamp for temporal analysis.

    Args:
        response: Raw JSON response from get_train_positions()
        poll_timestamp: Client timestamp when API call was initiated

    Returns:
        list[dict[str, Any]]: List of flat train records, one per train, with fields:
            - poll_timestamp: Our client timestamp (datetime)
            - api_timestamp: API's data collection time (str)
            - route: Line name (red, blue, etc.)
            - train_id: Train run number
            - lat: Latitude (float)
            - lon: Longitude (float)
            - heading: Direction in degrees (int)
            - next_station_id: Next station ID (str)
            - next_station_name: Next station name (str)
            - destination_id: Destination station ID (str)
            - destination_name: Destination station name (str)
            - predicted_arrival_time: Predicted arrival timestamp (str)
            - is_approaching: Whether train is approaching next station (bool)
            - is_delayed: Whether train is delayed (bool)

    Example:
        >>> async with httpx.AsyncClient() as client:
        ...     response = await get_train_positions(client)
        >>> poll_time = datetime.now(timezone.utc)
        >>> records = normalize_train_positions(response, poll_time)
        >>> len(records)  # Number of active trains across all lines
        157
        >>> records[0].keys()
        dict_keys(['poll_timestamp', 'api_timestamp', 'route', 'train_id', ...])

    """
    ctatt = response.get("ctatt", {})
    api_timestamp = ctatt.get("tmst")
    routes = ctatt.get("route", [])

    # Normalize: one row per train
    records: list[dict[str, object]] = []

    for route in routes:
        route_name = route.get("@name")
        trains_raw = route.get("train", [])

        # Normalize trains to always be a list (XML-to-JSON conversion returns dict for single train)
        trains = trains_raw if isinstance(trains_raw, list) else [trains_raw]

        for train in trains:
            v_lat = train.get("lat")
            v_lon = train.get("lon")
            validate_lat_lon(v_lat, v_lon)

            v_heading = train.get("heading")
            record = {
                "poll_timestamp": poll_timestamp,
                "api_timestamp": api_timestamp,
                "route": route_name,
                "train_id": train.get("rn"),
                "lat": float(v_lat) if v_lat is not None else None,
                "lon": float(v_lon) if v_lon is not None else None,
                "heading": int(v_heading) if v_heading is not None else None,
                "next_station_id": train.get("nextStaId"),
                "next_station_name": train.get("nextStaNm"),
                "destination_id": train.get("destSt"),
                "destination_name": train.get("destNm"),
                "prediction_time": train.get("prdt"),
                "predicted_arrival_time": train.get("arrT"),
                "is_approaching": bool(int(train.get("isApp", "0"))),
                "is_delayed": bool(int(train.get("isDly", "0"))),
            }
            records.append(record)

    return records
