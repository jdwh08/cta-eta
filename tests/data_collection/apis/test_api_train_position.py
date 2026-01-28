"""Test the CTA Train Position API client."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from cta_eta.data_collection.apis import api_train_position
from cta_eta.data_collection.exceptions import (
    ConfigurationError,
    CTATrackerAPIError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture


@pytest.fixture
def cta_api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required CTA API env var for the duration of the test."""
    monkeypatch.setenv("CTA_API_KEY", "test-api-key")


@pytest.mark.asyncio
async def test_get_train_positions_requires_api_key(
    monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """Test that get_train_positions requires CTA_API_KEY environment variable."""
    # Arrange
    monkeypatch.delenv("CTA_API_KEY", raising=False)
    client = mocker.AsyncMock(spec=httpx.AsyncClient)

    # Act / Assert
    with pytest.raises(
        ConfigurationError, match="CTA_API_KEY environment variable not set"
    ):
        await api_train_position.get_train_positions(client)


@pytest.mark.asyncio
async def test_get_train_positions_calls_expected_endpoint_and_params(
    cta_api_key_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict[str, Any], int, str], httpx.Response],
) -> None:
    """Test that get_train_positions calls the correct URL with proper params."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)

    expected_url = api_train_position.TRAIN_POSITION_URL
    expected_params = {
        "key": "test-api-key",
        "rt": ",".join(api_train_position.CTA_LINES),
        "outputType": "JSON",
    }

    response_payload = {
        "ctatt": {
            "tmst": "2026-01-14T20:34:15",
            "errCd": "0",
            "errNm": None,
            "route": [],
        }
    }
    response = httpx_json_response(response_payload, 200, expected_url)
    # Mock raise_for_status to verify it's called
    response.raise_for_status = mocker.Mock()
    client.get.return_value = response

    # Act
    result = await api_train_position.get_train_positions(client)

    # Assert
    assert result == response_payload
    client.get.assert_awaited_once()
    assert client.get.call_args.args[0] == expected_url
    assert client.get.call_args.kwargs["params"] == expected_params
    response.raise_for_status.assert_called_once()


@pytest.mark.asyncio
async def test_get_train_positions_returns_full_response_structure(
    cta_api_key_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict[str, Any], int, str], httpx.Response],
) -> None:
    """Test that get_train_positions returns the complete API response."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)

    response_payload = {
        "ctatt": {
            "tmst": "2026-01-14T20:34:15",
            "errCd": "0",
            "errNm": None,
            "route": [
                {
                    "@name": "red",
                    "train": [
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
                            "flags": None,
                            "lat": "42.06106",
                            "lon": "-87.68393",
                            "heading": "150",
                        }
                    ],
                }
            ],
        }
    }
    client.get.return_value = httpx_json_response(
        response_payload, 200, api_train_position.TRAIN_POSITION_URL
    )

    # Act
    result = await api_train_position.get_train_positions(client)

    # Assert
    assert result == response_payload
    assert result["ctatt"]["tmst"] == "2026-01-14T20:34:15"
    assert result["ctatt"]["errCd"] == "0"
    assert len(result["ctatt"]["route"]) == 1
    assert result["ctatt"]["route"][0]["@name"] == "red"


@pytest.mark.asyncio
async def test_get_train_positions_propagates_http_errors_without_retry_delay(
    cta_api_key_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict[str, Any], int, str], httpx.Response],
) -> None:
    """Test that get_train_positions propagates HTTP errors without retry delay."""
    # Arrange
    # Avoid stamina retry/backoff by calling through wrapper levels.
    # Decorator order: log_api_call(stamina.retry(original))
    # We need to unwrap both decorators to get to the original function
    fn_no_retry = api_train_position.get_train_positions.__wrapped__.__wrapped__  # type: ignore[attr-defined]

    client = mocker.AsyncMock(spec=httpx.AsyncClient)

    client.get.return_value = httpx_json_response(
        {"error": "Service Unavailable"}, 503, api_train_position.TRAIN_POSITION_URL
    )

    # Act / Assert
    with pytest.raises(httpx.HTTPStatusError):
        await fn_no_retry(client)


def test_normalize_train_positions_handles_single_train_single_route() -> None:
    """Test normalization with one train on one route."""
    # Arrange
    poll_timestamp = datetime(2026, 1, 14, 20, 34, 15, tzinfo=UTC)
    response = {
        "ctatt": {
            "tmst": "2026-01-14T20:34:15",
            "errCd": "0",
            "errNm": None,
            "route": [
                {
                    "@name": "red",
                    "train": [
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
                            "flags": None,
                            "lat": "42.06106",
                            "lon": "-87.68393",
                            "heading": "150",
                        }
                    ],
                }
            ],
        }
    }

    # Act
    records = api_train_position.normalize_train_positions(response, poll_timestamp)

    # Assert
    assert len(records) == 1
    record = records[0]
    assert record["poll_timestamp"] == poll_timestamp
    assert record["api_timestamp"] == "2026-01-14T20:34:15"
    assert record["route"] == "red"
    assert record["train_id"] == "519"
    assert record["lat"] == 42.06106
    assert record["lon"] == -87.68393
    assert record["heading"] == 150
    assert record["next_station_id"] == "40400"
    assert record["next_station_name"] == "Noyes"
    assert record["destination_id"] == "30176"
    assert record["destination_name"] == "Howard"
    assert record["prediction_time"] == "2026-01-14T20:33:52"
    assert record["predicted_arrival_time"] == "2026-01-14T20:34:52"
    assert record["is_approaching"] is True
    assert record["is_delayed"] is False


def test_normalize_train_positions_handles_multiple_trains_multiple_routes() -> None:
    """Test normalization with multiple trains across multiple routes."""
    # Arrange
    poll_timestamp = datetime(2026, 1, 14, 20, 34, 15, tzinfo=UTC)
    response = {
        "ctatt": {
            "tmst": "2026-01-14T20:34:15",
            "errCd": "0",
            "errNm": None,
            "route": [
                {
                    "@name": "red",
                    "train": [
                        {
                            "rn": "519",
                            "destSt": "30176",
                            "destNm": "Howard",
                            "lat": "42.06106",
                            "lon": "-87.68393",
                            "heading": "150",
                            "nextStaId": "40400",
                            "nextStaNm": "Noyes",
                            "prdt": "2026-01-14T20:33:52",
                            "arrT": "2026-01-14T20:34:52",
                            "isApp": "1",
                            "isDly": "0",
                        },
                        {
                            "rn": "520",
                            "destSt": "30203",
                            "destNm": "Linden",
                            "lat": "42.06762",
                            "lon": "-87.68762",
                            "heading": "333",
                            "nextStaId": "41050",
                            "nextStaNm": "Linden",
                            "prdt": "2026-01-14T20:33:52",
                            "arrT": "2026-01-14T20:34:52",
                            "isApp": "1",
                            "isDly": "0",
                        },
                    ],
                },
                {
                    "@name": "blue",
                    "train": [
                        {
                            "rn": "601",
                            "destSt": "40090",
                            "destNm": "O'Hare",
                            "lat": "41.87811",
                            "lon": "-87.62979",
                            "heading": "45",
                            "nextStaId": "40080",
                            "nextStaNm": "Chicago",
                            "prdt": "2026-01-14T20:34:00",
                            "arrT": "2026-01-14T20:35:00",
                            "isApp": "0",
                            "isDly": "1",
                        }
                    ],
                },
            ],
        }
    }

    # Act
    records = api_train_position.normalize_train_positions(response, poll_timestamp)

    # Assert
    assert len(records) == 3

    # Check first train (red line)
    assert records[0]["route"] == "red"
    assert records[0]["train_id"] == "519"
    assert records[0]["lat"] == 42.06106

    # Check second train (red line)
    assert records[1]["route"] == "red"
    assert records[1]["train_id"] == "520"
    assert records[1]["lat"] == 42.06762

    # Check third train (blue line)
    assert records[2]["route"] == "blue"
    assert records[2]["train_id"] == "601"
    assert records[2]["lat"] == 41.87811
    assert records[2]["is_approaching"] is False
    assert records[2]["is_delayed"] is True


def test_normalize_train_positions_defaults_missing_fields() -> None:
    """Test that normalization defaults missing optional fields correctly."""
    # Arrange
    poll_timestamp = datetime(2026, 1, 14, 20, 34, 15, tzinfo=UTC)
    response = {
        "ctatt": {
            "tmst": "2026-01-14T20:34:15",
            "route": [
                {
                    "@name": "red",
                    "train": [
                        {
                            "rn": "519",
                            "lat": 32.7121,
                            "lon": -117.1605,
                            # Missing: heading, nextStaId, nextStaNm,
                            # destSt, destNm, prdt, arrT, isApp, isDly
                            # NOTE(jdwh08): can't be missing lat/lon due to validation
                        }
                    ],
                }
            ],
        }
    }

    # Act
    records = api_train_position.normalize_train_positions(response, poll_timestamp)

    # Assert
    assert len(records) == 1
    record = records[0]
    assert record["poll_timestamp"] == poll_timestamp
    assert record["api_timestamp"] == "2026-01-14T20:34:15"
    assert record["route"] == "red"
    assert record["train_id"] == "519"
    assert record["lat"] == 32.7121
    assert record["lon"] == -117.1605
    assert record["heading"] is None
    assert record["next_station_id"] is None
    assert record["next_station_name"] is None
    assert record["destination_id"] is None
    assert record["destination_name"] is None
    assert record["prediction_time"] is None
    assert record["predicted_arrival_time"] is None
    assert record["is_approaching"] is False  # Default from "0"
    assert record["is_delayed"] is False  # Default from "0"


def test_normalize_train_positions_handles_empty_routes() -> None:
    """Test normalization when there are no routes."""
    # Arrange
    poll_timestamp = datetime(2026, 1, 14, 20, 34, 15, tzinfo=UTC)
    response = {
        "ctatt": {
            "tmst": "2026-01-14T20:34:15",
            "errCd": "0",
            "errNm": None,
            "route": [],
        }
    }

    # Act
    records = api_train_position.normalize_train_positions(response, poll_timestamp)

    # Assert
    assert len(records) == 0
    assert records == []


def test_normalize_train_positions_handles_route_with_no_trains() -> None:
    """Test normalization when a route has no trains."""
    # Arrange
    poll_timestamp = datetime(2026, 1, 14, 20, 34, 15, tzinfo=UTC)
    response = {
        "ctatt": {
            "tmst": "2026-01-14T20:34:15",
            "errCd": "0",
            "errNm": None,
            "route": [
                {
                    "@name": "red",
                    "train": [],
                },
                {
                    "@name": "blue",
                    "train": [
                        {
                            "rn": "601",
                            "lat": "41.87811",
                            "lon": "-87.62979",
                            "heading": "45",
                        }
                    ],
                },
            ],
        }
    }

    # Act
    records = api_train_position.normalize_train_positions(response, poll_timestamp)

    # Assert
    assert len(records) == 1
    assert records[0]["route"] == "blue"
    assert records[0]["train_id"] == "601"


def test_normalize_train_positions_handles_missing_ctatt() -> None:
    """Test normalization when ctatt key is missing (returns empty list)."""
    # Arrange
    poll_timestamp = datetime(2026, 1, 14, 20, 34, 15, tzinfo=UTC)
    response: dict[str, Any] = {}

    # Act
    records = api_train_position.normalize_train_positions(response, poll_timestamp)

    # Assert
    assert len(records) == 0
    assert records == []


def test_normalize_train_positions_handles_missing_route_key() -> None:
    """Test normalization when route key is missing from ctatt."""
    # Arrange
    poll_timestamp = datetime(2026, 1, 14, 20, 34, 15, tzinfo=UTC)
    response = {
        "ctatt": {
            "tmst": "2026-01-14T20:34:15",
            "errCd": "0",
        }
    }

    # Act
    records = api_train_position.normalize_train_positions(response, poll_timestamp)

    # Assert
    assert len(records) == 0
    assert records == []


def test_normalize_train_positions_handles_missing_api_timestamp() -> None:
    """Test normalization when tmst (API timestamp) is missing."""
    # Arrange
    poll_timestamp = datetime(2026, 1, 14, 20, 34, 15, tzinfo=UTC)
    response = {
        "ctatt": {
            "errCd": "0",
            "route": [
                {
                    "@name": "red",
                    "train": [
                        {
                            "rn": "519",
                            "lat": "42.06106",
                            "lon": "-87.68393",
                        }
                    ],
                }
            ],
        }
    }

    # Act
    records = api_train_position.normalize_train_positions(response, poll_timestamp)

    # Assert
    assert len(records) == 1
    assert records[0]["api_timestamp"] is None


def test_normalize_train_positions_handles_missing_route_name() -> None:
    """Test normalization when @name is missing from route."""
    # Arrange
    poll_timestamp = datetime(2026, 1, 14, 20, 34, 15, tzinfo=UTC)
    response = {
        "ctatt": {
            "tmst": "2026-01-14T20:34:15",
            "route": [
                {
                    "train": [
                        {
                            "rn": "519",
                            "lat": "42.06106",
                            "lon": "-87.68393",
                        }
                    ],
                }
            ],
        }
    }

    # Act
    records = api_train_position.normalize_train_positions(response, poll_timestamp)

    # Assert
    assert len(records) == 1
    assert records[0]["route"] is None


def test_normalize_train_positions_converts_bool_flags_correctly() -> None:
    """Test that isApp and isDly are converted to boolean correctly."""
    # Arrange
    poll_timestamp = datetime(2026, 1, 14, 20, 34, 15, tzinfo=UTC)
    response = {
        "ctatt": {
            "tmst": "2026-01-14T20:34:15",
            "route": [
                {
                    "@name": "red",
                    "train": [
                        {
                            "rn": "519",
                            "isApp": "1",
                            "isDly": "0",
                            "lat": "42.06106",
                            "lon": "-87.68393",
                        },
                        {
                            "rn": "520",
                            "isApp": "0",
                            "isDly": "1",
                            "lat": "42.06106",
                            "lon": "-87.68393",
                        },
                        {
                            "rn": "521",
                            "isApp": "1",
                            "isDly": "1",
                            "lat": "42.06106",
                            "lon": "-87.68393",
                        },
                    ],
                }
            ],
        }
    }

    # Act
    records = api_train_position.normalize_train_positions(response, poll_timestamp)

    # Assert
    assert len(records) == 3
    assert records[0]["is_approaching"] is True
    assert records[0]["is_delayed"] is False
    assert records[1]["is_approaching"] is False
    assert records[1]["is_delayed"] is True
    assert records[2]["is_approaching"] is True
    assert records[2]["is_delayed"] is True


def test_normalize_train_positions_converts_numeric_types_correctly() -> None:
    """Test that lat, lon, and heading are converted to float/int correctly."""
    # Arrange
    poll_timestamp = datetime(2026, 1, 14, 20, 34, 15, tzinfo=UTC)
    response = {
        "ctatt": {
            "tmst": "2026-01-14T20:34:15",
            "route": [
                {
                    "@name": "red",
                    "train": [
                        {
                            "rn": "519",
                            "lat": "42.06106",
                            "lon": "-87.68393",
                            "heading": "150",
                        },
                        {
                            "rn": "520",
                            "lat": "0.0",
                            "lon": "0.0",
                            "heading": "0",
                        },
                    ],
                }
            ],
        }
    }

    # Act
    records = api_train_position.normalize_train_positions(response, poll_timestamp)

    # Assert
    assert len(records) == 2
    assert isinstance(records[0]["lat"], float)
    assert isinstance(records[0]["lon"], float)
    assert isinstance(records[0]["heading"], int)
    assert records[0]["lat"] == 42.06106
    assert records[0]["lon"] == -87.68393
    assert records[0]["heading"] == 150
    assert records[1]["lat"] == 0.0
    assert records[1]["lon"] == 0.0
    assert records[1]["heading"] == 0


def test_normalize_train_positions_handles_all_cta_lines() -> None:
    """Test normalization with all CTA lines present."""
    # Arrange
    poll_timestamp = datetime(2026, 1, 14, 20, 34, 15, tzinfo=UTC)
    routes = [
        {
            "@name": line,
            "train": [
                {
                    "rn": f"{line}-001",
                    "lat": "41.88",
                    "lon": "-87.63",
                    "heading": "90",
                }
            ],
        }
        for line in api_train_position.CTA_LINES
    ]

    response = {
        "ctatt": {
            "tmst": "2026-01-14T20:34:15",
            "route": routes,
        }
    }

    # Act
    records = api_train_position.normalize_train_positions(response, poll_timestamp)

    # Assert
    assert len(records) == len(api_train_position.CTA_LINES)
    route_names = {record["route"] for record in records}
    assert route_names == set(api_train_position.CTA_LINES)


def test_normalize_train_positions_handles_none_train_list() -> None:
    """Test normalization when train list is None instead of empty list."""
    # Arrange
    poll_timestamp = datetime(2026, 1, 14, 20, 34, 15, tzinfo=UTC)
    response = {
        "ctatt": {
            "tmst": "2026-01-14T20:34:15",
            "route": [
                {
                    "@name": "red",
                    "train": None,
                }
            ],
        }
    }

    # Act / Assert
    # .get() returns None, which gets wrapped in [None], then AttributeError when calling .get() on None
    with pytest.raises(AttributeError):
        api_train_position.normalize_train_positions(response, poll_timestamp)


def test_normalize_train_positions_handles_invalid_numeric_strings() -> None:
    """Test normalization when lat/lon/heading are invalid numeric strings."""
    # Arrange
    poll_timestamp = datetime(2026, 1, 14, 20, 34, 15, tzinfo=UTC)
    response = {
        "ctatt": {
            "tmst": "2026-01-14T20:34:15",
            "route": [
                {
                    "@name": "red",
                    "train": [
                        {
                            "rn": "519",
                            "lat": "not-a-float",
                            "lon": "-87.68393",
                            "heading": "150",
                        }
                    ],
                }
            ],
        }
    }

    # Act / Assert
    with pytest.raises(TypeError):
        api_train_position.normalize_train_positions(response, poll_timestamp)


def test_normalize_train_positions_handles_invalid_bool_strings() -> None:
    """Test normalization when isApp/isDly are invalid (non-numeric strings)."""
    # Arrange
    poll_timestamp = datetime(2026, 1, 14, 20, 34, 15, tzinfo=UTC)
    response = {
        "ctatt": {
            "tmst": "2026-01-14T20:34:15",
            "route": [
                {
                    "@name": "red",
                    "train": [
                        {
                            "rn": "519",
                            "lat": "42.06106",
                            "lon": "-87.68393",
                            "heading": "150",
                            "isApp": "not-a-number",
                            "isDly": "0",
                        }
                    ],
                }
            ],
        }
    }

    # Act / Assert
    with pytest.raises(ValueError):
        api_train_position.normalize_train_positions(response, poll_timestamp)


class TestCTATrackerAPIError:
    """Tests for CTATrackerAPIError exception class."""

    def test_cta_tracker_api_error_with_code_and_message(self) -> None:
        """CTATrackerAPIError stores err_cd and err_nm and formats message."""
        # Arrange & Act
        error = CTATrackerAPIError(err_cd="102", err_nm="Daily limit exceeded")

        # Assert
        assert error.err_cd == "102"
        assert error.err_nm == "Daily limit exceeded"
        assert "102" in str(error)
        assert "Daily limit exceeded" in str(error)

    def test_cta_tracker_api_error_with_code_only(self) -> None:
        """CTATrackerAPIError with only err_cd formats message without err_nm."""
        # Arrange & Act
        error = CTATrackerAPIError(err_cd="500")

        # Assert
        assert error.err_cd == "500"
        assert error.err_nm is None
        assert "500" in str(error)


@pytest.mark.asyncio
async def test_get_train_positions_raises_cta_error_on_err_cd_102(
    cta_api_key_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict[str, Any], int, str], httpx.Response],
) -> None:
    """get_train_positions raises CTATrackerAPIError when errCd=102."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    response_payload = {
        "ctatt": {
            "tmst": "2026-01-25T12:00:00",
            "errCd": "102",
            "errNm": "Daily limit exceeded",
        }
    }
    client.get.return_value = httpx_json_response(
        response_payload, 200, api_train_position.TRAIN_POSITION_URL
    )

    # Act / Assert
    with pytest.raises(CTATrackerAPIError) as exc_info:
        await api_train_position.get_train_positions(client)

    assert exc_info.value.err_cd == "102"
    assert exc_info.value.err_nm == "Daily limit exceeded"


@pytest.mark.asyncio
async def test_get_train_positions_raises_cta_error_on_err_cd_500(
    cta_api_key_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict[str, Any], int, str], httpx.Response],
) -> None:
    """get_train_positions raises CTATrackerAPIError when errCd=500."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    response_payload = {
        "ctatt": {
            "tmst": "2026-01-25T12:00:00",
            "errCd": "500",
            "errNm": "Internal server error",
        }
    }
    client.get.return_value = httpx_json_response(
        response_payload, 200, api_train_position.TRAIN_POSITION_URL
    )

    # Act / Assert
    with pytest.raises(CTATrackerAPIError) as exc_info:
        await api_train_position.get_train_positions(client)

    assert exc_info.value.err_cd == "500"
    assert exc_info.value.err_nm == "Internal server error"


@pytest.mark.asyncio
async def test_get_train_positions_does_not_raise_on_err_cd_zero(
    cta_api_key_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict[str, Any], int, str], httpx.Response],
) -> None:
    """get_train_positions does not raise when errCd=0 (success)."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    response_payload = {
        "ctatt": {
            "tmst": "2026-01-25T12:00:00",
            "errCd": "0",
            "errNm": None,
            "route": [],
        }
    }
    client.get.return_value = httpx_json_response(
        response_payload, 200, api_train_position.TRAIN_POSITION_URL
    )

    # Act
    result = await api_train_position.get_train_positions(client)

    # Assert
    assert result == response_payload


@pytest.mark.asyncio
async def test_get_train_positions_does_not_raise_when_err_cd_missing(
    cta_api_key_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict[str, Any], int, str], httpx.Response],
) -> None:
    """get_train_positions does not raise when errCd is missing."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    response_payload = {
        "ctatt": {
            "tmst": "2026-01-25T12:00:00",
            "route": [],
        }
    }
    client.get.return_value = httpx_json_response(
        response_payload, 200, api_train_position.TRAIN_POSITION_URL
    )

    # Act
    result = await api_train_position.get_train_positions(client)

    # Assert
    assert result == response_payload


@pytest.mark.asyncio
async def test_get_train_positions_handles_all_cta_error_codes(
    cta_api_key_env: None,  # noqa: ARG001
    mocker: MockerFixture,
    httpx_json_response: Callable[[dict[str, Any], int, str], httpx.Response],
) -> None:
    """get_train_positions raises CTATrackerAPIError for all CTA error codes."""
    # Arrange
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    error_codes = ["100", "101", "102", "106", "107", "500"]

    for err_cd in error_codes:
        response_payload = {
            "ctatt": {
                "tmst": "2026-01-25T12:00:00",
                "errCd": err_cd,
                "errNm": f"Error {err_cd}",
            }
        }
        client.get.return_value = httpx_json_response(
            response_payload, 200, api_train_position.TRAIN_POSITION_URL
        )

        # Act / Assert
        with pytest.raises(api_train_position.CTATrackerAPIError) as exc_info:
            await api_train_position.get_train_positions(client)

        assert exc_info.value.err_cd == err_cd
