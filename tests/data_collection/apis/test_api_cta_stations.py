"""Test the Chicago Open Data CTA stations client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest

from cta_eta.data_collection.apis import api_cta_stations

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture


def test_get_chidata_headers_requires_token() -> None:
    """Test that _get_chidata_headers requires CHIDATA_APP_TOK."""
    # Arrange
    cfg: dict[str, dict[str, str | int | float | bool]] = {
        "secrets": {"chidata_app_token": "", "chidata_app_secret": "secret"}
    }

    # Act / Assert
    with pytest.raises(ValueError, match="CHIDATA_APP_TOK must be set"):
        api_cta_stations._get_chidata_headers(cfg)


def test_get_chidata_headers_requires_secret() -> None:
    """Test that _get_chidata_headers requires CHIDATA_APP_SECRET."""
    # Arrange
    cfg: dict[str, dict[str, str | int | float | bool]] = {
        "secrets": {"chidata_app_token": "token", "chidata_app_secret": ""}
    }

    # Act / Assert
    with pytest.raises(ValueError, match="CHIDATA_APP_SECRET must be set"):
        api_cta_stations._get_chidata_headers(cfg)


def test_get_chidata_headers_strips_whitespace() -> None:
    """Test that _get_chidata_headers strips whitespace from credentials."""
    # Arrange
    cfg: dict[str, dict[str, str | int | float | bool]] = {
        "secrets": {"chidata_app_token": "  tok  ", "chidata_app_secret": "  sec  "}
    }

    # Act
    headers = api_cta_stations._get_chidata_headers(cfg)

    # Assert
    assert headers == {"X-App-Token": "tok", "X-App-Secret": "sec"}


def test_get_cta_stations_calls_expected_endpoint_and_headers(
    mocker: MockerFixture,
    httpx_json_response: Callable[[Any, int, str], httpx.Response],
) -> None:
    """Test that get_cta_stations calls the correct URL with auth headers."""
    # Arrange
    client = mocker.Mock(spec=httpx.Client)
    cfg: dict[str, dict[str, str | int | float | bool]] = {
        "secrets": {"chidata_app_token": "tok", "chidata_app_secret": "sec"}
    }
    url = (
        f"{api_cta_stations.CHICAGO_DATA_VIEWS_BASE}/"
        f"{api_cta_stations.CTA_STATIONS_DATASET_ID}/query.json"
    )
    payload = [
        {
            "station_id": "900",
            "longname": "Howard",
            "address": "1649 W. Howard Street",
            "lines": "Red, Yellow, Purple, Evanston Express",
            "the_geom": {"type": "Point", "coordinates": [-87.672892, 42.019063]},
        }
    ]
    client.get.return_value = httpx_json_response(payload, 200, url)

    # Act
    stations = api_cta_stations.get_cta_stations(client, cfg)

    # Assert
    assert stations == payload
    client.get.assert_called_once()
    assert client.get.call_args.args[0] == url
    assert client.get.call_args.kwargs["headers"] == {
        "X-App-Token": "tok",
        "X-App-Secret": "sec",
    }


def test_get_cta_stations_rejects_non_list_json(
    mocker: MockerFixture,
    httpx_json_response: Callable[[Any, int, str], httpx.Response],
) -> None:
    """Test that get_cta_stations rejects non-list JSON payloads."""
    # Arrange
    client = mocker.Mock(spec=httpx.Client)
    cfg: dict[str, dict[str, str | int | float | bool]] = {
        "secrets": {"chidata_app_token": "tok", "chidata_app_secret": "sec"}
    }
    url = (
        f"{api_cta_stations.CHICAGO_DATA_VIEWS_BASE}/"
        f"{api_cta_stations.CTA_STATIONS_DATASET_ID}/query.json"
    )
    client.get.return_value = httpx_json_response({"not": "a list"}, 200, url)

    # Act / Assert
    with pytest.raises(TypeError, match="Unexpected Chicago Data Portal response type"):
        api_cta_stations.get_cta_stations(client, cfg)


def test_get_cta_stations_propagates_http_errors_without_retry_delay(
    mocker: MockerFixture,
    httpx_json_response: Callable[[Any, int, str], httpx.Response],
) -> None:
    """Test that get_cta_stations propagates HTTP errors without retry delay."""
    # Arrange
    # Avoid stamina retry/backoff by calling through one wrapper level.
    # (Decorator order: stamina.retry(log_api_call(original)))
    fn_no_retry = api_cta_stations.get_cta_stations.__wrapped__  # type: ignore[attr-defined]

    client = mocker.Mock(spec=httpx.Client)
    cfg: dict[str, dict[str, str | int | float | bool]] = {
        "secrets": {"chidata_app_token": "tok", "chidata_app_secret": "sec"}
    }
    url = (
        f"{api_cta_stations.CHICAGO_DATA_VIEWS_BASE}/"
        f"{api_cta_stations.CTA_STATIONS_DATASET_ID}/query.json"
    )
    client.get.return_value = httpx_json_response({"error": "nope"}, 503, url)

    # Act / Assert
    with pytest.raises(httpx.HTTPStatusError):
        fn_no_retry(client, cfg)


def test_normalize_cta_stations_filters_invalid_geometry_and_sorts() -> None:
    """Test that normalize_cta_stations skips bad records and returns stable ordering."""
    # Arrange
    raw = [
        # invalid geometry types / shapes -> skipped
        {"station_id": "002", "the_geom": None},
        {"station_id": "003", "the_geom": {"coordinates": "not-a-list"}},
        {"station_id": "004", "the_geom": {"coordinates": [-87.0]}},  # too short
        # valid stations (out of order by id) -> included + sorted by id
        {
            "station_id": "900",
            "longname": "Howard",
            "address": "1649 W. Howard Street",
            "lines": "Red",
            "the_geom": {"type": "Point", "coordinates": [-87.672892, 42.019063]},
        },
        {
            "station_id": "100",
            "longname": "Somewhere",
            "address": "123 Main St",
            "lines": "Blue",
            "the_geom": {"type": "Point", "coordinates": [-87.63, 41.88]},
        },
    ]

    # Act
    normalized = api_cta_stations.normalize_cta_stations(raw)

    # Assert
    assert [s["id"] for s in normalized] == ["100", "900"]
    assert normalized[0]["latitude"] == 41.88
    assert normalized[0]["longitude"] == -87.63
    assert normalized[0]["name"] == "Somewhere"


def test_normalize_cta_stations_raises_on_non_numeric_coordinates() -> None:
    """Test that normalize_cta_stations raises when coordinates cannot be converted to float."""
    # Arrange
    raw = [{"station_id": "900", "the_geom": {"coordinates": ["not-a-float", "41.0"]}}]

    # Act / Assert
    with pytest.raises(ValueError):
        api_cta_stations.normalize_cta_stations(raw)


def test_get_stations_cache_wires_fetch_fn_without_network(
    mocker: MockerFixture,
) -> None:
    """Test that get_stations_cache builds a cache wired to fetch and normalize stations."""
    # Arrange
    cfg: dict[str, dict[str, str | int | float | bool]] = {
        "cache": {"directory": "/tmp/does-not-matter", "stations_ttl": 3600},
        "secrets": {"chidata_app_token": "tok", "chidata_app_secret": "sec"},
    }

    create_cached_data = mocker.patch.object(api_cta_stations, "create_cached_data")
    create_cached_data.return_value = mocker.sentinel.cached

    raw = [{"station_id": "1", "the_geom": {"coordinates": [-87.0, 41.0]}}]
    normalized = [
        {
            "id": "1",
            "name": "",
            "address": "",
            "lines": "",
            "latitude": 41.0,
            "longitude": -87.0,
        }
    ]
    get_cta_stations = mocker.patch.object(
        api_cta_stations, "get_cta_stations", return_value=raw
    )
    normalize_cta_stations = mocker.patch.object(
        api_cta_stations, "normalize_cta_stations", return_value=normalized
    )

    # Patch httpx.Client used inside the closure so no real network happens.
    client_cm = mocker.Mock()
    client_cm.__enter__ = mocker.Mock(return_value=mocker.Mock(spec=httpx.Client))
    client_cm.__exit__ = mocker.Mock(return_value=None)
    httpx_client_ctor = mocker.patch.object(
        api_cta_stations.httpx, "Client", return_value=client_cm
    )

    # Act
    cache = api_cta_stations.get_stations_cache(cfg)

    # Assert
    assert cache is mocker.sentinel.cached
    create_cached_data.assert_called_once()
    assert create_cached_data.call_args.args[0] == "stations"
    assert create_cached_data.call_args.args[1] == cfg

    fetch_fn = create_cached_data.call_args.args[2]
    assert callable(fetch_fn)

    # Act (exercise the closure deterministically)
    result = fetch_fn()

    # Assert
    assert result == normalized
    httpx_client_ctor.assert_called_once()
    get_cta_stations.assert_called_once()
    normalize_cta_stations.assert_called_once_with(raw)


def test_get_stations_cache_defaults_to_module_config(
    mocker: MockerFixture,
) -> None:
    """Test that get_stations_cache defaults cfg to the module config when omitted."""
    # Arrange
    create_cached_data = mocker.patch.object(api_cta_stations, "create_cached_data")
    create_cached_data.return_value = mocker.sentinel.cached

    # Act
    cache = api_cta_stations.get_stations_cache()

    # Assert
    assert cache is mocker.sentinel.cached
    create_cached_data.assert_called_once()
    assert create_cached_data.call_args.args[0] == "stations"
    assert create_cached_data.call_args.args[1] == api_cta_stations.config
