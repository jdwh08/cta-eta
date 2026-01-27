"""Test the Chicago Open Data CTA track shape client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest

from cta_eta.data_collection.apis import api_track_shape
from cta_eta.data_collection.exceptions import (
    APIResponseError,
    ConfigurationError,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def test_get_chidata_headers_requires_token() -> None:
    """Test that _get_chidata_headers requires CHIDATA_APP_TOK."""
    # Arrange
    cfg: dict[str, dict[str, str | int | float | bool]] = {
        "secrets": {"chidata_app_token": "", "chidata_app_secret": "secret"}
    }

    # Act / Assert
    with pytest.raises(ConfigurationError, match="CHIDATA_APP_TOK must be set"):
        api_track_shape._get_chidata_headers(cfg)


def test_get_chidata_headers_requires_secret() -> None:
    """Test that _get_chidata_headers requires CHIDATA_APP_SECRET."""
    # Arrange
    cfg: dict[str, dict[str, str | int | float | bool]] = {
        "secrets": {"chidata_app_token": "token", "chidata_app_secret": ""}
    }

    # Act / Assert
    with pytest.raises(ConfigurationError, match="CHIDATA_APP_SECRET must be set"):
        api_track_shape._get_chidata_headers(cfg)


def test_get_chidata_headers_strips_whitespace() -> None:
    """Test that _get_chidata_headers strips whitespace from credentials."""
    # Arrange
    cfg: dict[str, dict[str, str | int | float | bool]] = {
        "secrets": {"chidata_app_token": "  tok  ", "chidata_app_secret": "  sec  "}
    }

    # Act
    headers = api_track_shape._get_chidata_headers(cfg)

    # Assert
    assert headers == {"X-App-Token": "tok", "X-App-Secret": "sec"}


def test_extract_multilinestring_coords_filters_invalid_points_and_lines() -> None:
    """Test that MultiLineString coordinate extraction filters invalid content."""
    # Arrange
    geom: dict[str, object] = {
        "type": "MultiLineString",
        "coordinates": [
            # empty line -> dropped
            [],
            # not a list -> dropped
            "bad",
            # mixed points -> only valid numeric [lon, lat] should remain
            [
                [-87.63, 41.88],
                [-87.62, 41.89, 123],  # extra dimensions tolerated
                ["-87.61", 41.90],  # lon not numeric -> dropped
                [-87.60],  # too short -> dropped
                (-87.59, 41.91),  # tuple, not list -> dropped
            ],
        ],
    }

    # Act
    coords = api_track_shape._extract_multilinestring_coords(geom)

    # Assert
    assert coords == [[[-87.63, 41.88], [-87.62, 41.89]]]


@pytest.mark.parametrize(
    ("description", "expected_a", "expected_b"),
    [
        ("Tower 12 to Library", "Tower 12", "Library"),
        ("A to B", "A", "B"),
        ("NoDelimiterHere", "NoDelimiterHere", "NoDelimiterHere"),
        ("  ", "", ""),
        ("A to ", "A to", "A to"),
        (" to B", "to B", "to B"),
    ],
)
def test_parse_description_endpoints_edge_cases(
    description: str, expected_a: str | None, expected_b: str | None
) -> None:
    """Test parsing of description endpoints for edge-case formats."""
    # Arrange / Act
    endpoint_a, endpoint_b = api_track_shape._parse_description_endpoints(description)

    # Assert
    assert endpoint_a == expected_a
    assert endpoint_b == expected_b


def test_normalize_track_shapes_skips_invalid_geometry_and_is_deterministic() -> None:
    """Test that normalization skips bad rows and returns stable ordering."""
    # Arrange
    raw_common: list[dict[str, Any]] = [
        # invalid geometry -> skipped
        {"the_geom": None},
        {"the_geom": {"type": "Point", "coordinates": [-87.0, 41.0]}},
        {"the_geom": {"type": "MultiLineString", "coordinates": []}},
    ]
    row_a: dict[str, Any] = {
        "the_geom": {
            "type": "MultiLineString",
            "coordinates": [[[-87.628, 41.876], [-87.626, 41.877]]],
        },
        "lines": "Brown, Orange",
        "description": "Tower 12 to Library",
        "type": "Elevated or At Grade",
        "legend": "ML",
        "shape_len": "647.793224715",
    }
    row_b: dict[str, Any] = {
        "the_geom": {
            "type": "MultiLineString",
            "coordinates": [[[-87.700, 41.900], [-87.710, 41.910]]],
        },
        "lines": "Red",
        "description": "Howard",
        "type": "Subway",
        "legend": "",
        "shape_len": "not-a-float",
    }

    # Act
    normalized_1 = api_track_shape.normalize_track_shapes(raw_common + [row_a, row_b])
    normalized_2 = api_track_shape.normalize_track_shapes(raw_common + [row_b, row_a])

    # Assert
    assert normalized_1 == normalized_2
    assert len(normalized_1) == 2

    rec = next(r for r in normalized_1 if r["description"] == "Tower 12 to Library")
    assert rec["lines"] == ["Brown", "Orange"]
    assert rec["endpoint_a"] == "Tower 12"
    assert rec["endpoint_b"] == "Library"
    assert rec["shape_len"] == 647.793224715
    assert rec["start_lat"] == 41.876
    assert rec["start_lon"] == -87.628
    assert rec["end_lat"] == 41.877
    assert rec["end_lon"] == -87.626
    assert rec["bbox"] == {
        "min_lon": -87.628,
        "min_lat": 41.876,
        "max_lon": -87.626,
        "max_lat": 41.877,
    }
    assert rec["geometry"]["type"] == "MultiLineString"
    assert isinstance(rec["segment_id"], str)
    assert rec["segment_id"]

    rec_bad_len = next(r for r in normalized_1 if r["description"] == "Howard")
    assert rec_bad_len["shape_len"] is None


def test_fetch_track_shapes_raw_paginates_and_uses_expected_request_params(
    mocker: pytest.MockFixture,
    httpx_json_response: Callable[[Any, int, str], httpx.Response],
) -> None:
    """Test that fetch_track_shapes_raw paginates and uses expected headers/params."""
    # Arrange
    client = mocker.Mock(spec=httpx.Client)
    cfg: dict[str, dict[str, str | int | float | bool]] = {
        "secrets": {"chidata_app_token": "tok", "chidata_app_secret": "sec"}
    }
    url = (
        f"{api_track_shape.CHICAGO_OPEN_DATA_RESOURCE_BASE}/"
        f"{api_track_shape.CTA_TRACK_SHAPES_DATASET_ID}.json"
    )
    page_0 = [
        {"the_geom": {"type": "MultiLineString", "coordinates": [[[0.0, 0.0]]]}},
    ]
    page_1: list[dict[str, Any]] = []
    client.get.side_effect = [
        httpx_json_response(page_0, 200, url),
        httpx_json_response(page_1, 200, url),
    ]

    # Act
    rows = api_track_shape.fetch_track_shapes_raw(client, cfg, page_size=1)

    # Assert
    assert rows == page_0
    assert client.get.call_count == 2

    expected_headers = {"X-App-Token": "tok", "X-App-Secret": "sec"}
    select = "the_geom,lines,description,type,legend,shape_len"

    call_0 = client.get.call_args_list[0]
    assert call_0.args[0] == url
    assert call_0.kwargs["headers"] == expected_headers
    assert call_0.kwargs["params"] == {"$select": select, "$limit": 1, "$offset": 0}

    call_1 = client.get.call_args_list[1]
    assert call_1.args[0] == url
    assert call_1.kwargs["headers"] == expected_headers
    assert call_1.kwargs["params"] == {"$select": select, "$limit": 1, "$offset": 1}


def test_fetch_track_shapes_raw_rejects_non_list_json(
    mocker: pytest.MockFixture,
    httpx_json_response: Callable[[Any, int, str], httpx.Response],
) -> None:
    """Test that fetch_track_shapes_raw rejects non-list JSON payloads."""
    # Arrange
    client = mocker.Mock(spec=httpx.Client)
    cfg: dict[str, dict[str, str | int | float | bool]] = {
        "secrets": {"chidata_app_token": "tok", "chidata_app_secret": "sec"}
    }
    url = (
        f"{api_track_shape.CHICAGO_OPEN_DATA_RESOURCE_BASE}/"
        f"{api_track_shape.CTA_TRACK_SHAPES_DATASET_ID}.json"
    )
    client.get.return_value = httpx_json_response({"not": "a list"}, 200, url)

    # Act / Assert
    with pytest.raises(APIResponseError, match="Unexpected Chicago Open Data response type"):
        api_track_shape.fetch_track_shapes_raw(client, cfg)


def test_fetch_track_shapes_raw_propagates_http_errors_without_retry_delay(
    mocker: pytest.MockFixture,
    httpx_json_response: Callable[[Any, int, str], httpx.Response],
) -> None:
    """Test that fetch_track_shapes_raw propagates HTTP errors without retry delay."""
    # Arrange
    # Avoid stamina retry/backoff by calling through one wrapper level.
    # (Decorator order: stamina.retry(log_api_call(original)))
    fn_no_retry = api_track_shape.fetch_track_shapes_raw.__wrapped__  # type: ignore[attr-defined]

    client = mocker.Mock(spec=httpx.Client)
    cfg: dict[str, dict[str, str | int | float | bool]] = {
        "secrets": {"chidata_app_token": "tok", "chidata_app_secret": "sec"}
    }
    url = (
        f"{api_track_shape.CHICAGO_OPEN_DATA_RESOURCE_BASE}/"
        f"{api_track_shape.CTA_TRACK_SHAPES_DATASET_ID}.json"
    )
    client.get.return_value = httpx_json_response({"error": "nope"}, 503, url)

    # Act / Assert
    with pytest.raises(httpx.HTTPStatusError):
        fn_no_retry(client, cfg)


def test_get_track_geometry_cache_wires_fetch_fn_without_network(
    mocker: pytest.MockFixture,
) -> None:
    """Test that get_track_geometry_cache builds a cache wired to fetch and normalize."""
    # Arrange
    cfg: dict[str, dict[str, str | int | float | bool]] = {
        "cache": {"directory": "/tmp/does-not-matter", "track_geometry_ttl": 3600},  # noqa: S108
        "secrets": {"chidata_app_token": "tok", "chidata_app_secret": "sec"},
    }

    create_cached_data = mocker.patch.object(api_track_shape, "create_cached_data")
    create_cached_data.return_value = mocker.sentinel.cached

    raw = [
        {"the_geom": {"type": "MultiLineString", "coordinates": [[[0.0, 0.0]]]}},
    ]
    normalized = [{"segment_id": "abc", "geometry": {"type": "MultiLineString"}}]
    fetch_track_shapes_raw = mocker.patch.object(
        api_track_shape, "fetch_track_shapes_raw", return_value=raw
    )
    normalize_track_shapes = mocker.patch.object(
        api_track_shape, "normalize_track_shapes", return_value=normalized
    )

    # Patch httpx.Client used inside the closure so no real network happens.
    client_cm = mocker.Mock()
    client_cm.__enter__ = mocker.Mock(return_value=mocker.Mock(spec=httpx.Client))
    client_cm.__exit__ = mocker.Mock(return_value=None)
    httpx_client_ctor = mocker.patch.object(
        api_track_shape.httpx, "Client", return_value=client_cm
    )

    # Act
    cache = api_track_shape.get_track_geometry_cache(cfg)

    # Assert
    assert cache is mocker.sentinel.cached
    create_cached_data.assert_called_once()
    assert create_cached_data.call_args.args[0] == "track_geometry"
    assert create_cached_data.call_args.args[1] == cfg

    fetch_fn = create_cached_data.call_args.args[2]
    assert callable(fetch_fn)

    # Act (exercise the closure deterministically)
    result = fetch_fn()

    # Assert
    assert result == normalized
    httpx_client_ctor.assert_called_once()
    fetch_track_shapes_raw.assert_called_once()
    normalize_track_shapes.assert_called_once_with(raw)


def test_get_track_geometry_cache_defaults_to_module_config(
    mocker: pytest.MockFixture,
) -> None:
    """Test that get_track_geometry_cache defaults cfg to the module config when omitted."""
    # Arrange
    create_cached_data = mocker.patch.object(api_track_shape, "create_cached_data")
    create_cached_data.return_value = mocker.sentinel.cached

    # Act
    cache = api_track_shape.get_track_geometry_cache()

    # Assert
    assert cache is mocker.sentinel.cached
    create_cached_data.assert_called_once()
    assert create_cached_data.call_args.args[0] == "track_geometry"
    assert create_cached_data.call_args.args[1] == api_track_shape.config
