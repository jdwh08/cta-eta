"""Unit tests for daemon_utils: error classification and discovery state."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from cta_eta.data_collection.orchestration.daemon_utils import (
    DiscoveryStateMarker,
    ErrorCategory,
    classify_error,
)


class TestClassifyError:
    """Tests for classify_error()."""

    def test_valueerror_with_config_keywords_returns_configuration(self) -> None:
        """Test that ValueError with config keywords returns ErrorCategory.CONFIGURATION."""
        for msg in [
            "Missing API key",
            "Required field X",
            "invalid config",
            "not set",
            "must be set",
        ]:
            assert classify_error(ValueError(msg)) == ErrorCategory.CONFIGURATION

    def test_httpx_429_returns_rate_limit(self) -> None:
        """Test that httpx.HTTPStatusError with 429 status code returns ErrorCategory.RATE_LIMIT."""
        resp = MagicMock()
        resp.status_code = httpx.codes.TOO_MANY_REQUESTS
        err = httpx.HTTPStatusError("429", request=MagicMock(), response=resp)
        assert classify_error(err) == ErrorCategory.RATE_LIMIT

    def test_httpx_request_error_returns_transient(self) -> None:
        """Test that httpx.RequestError returns ErrorCategory.TRANSIENT."""
        err = httpx.RequestError("network", request=MagicMock())
        assert classify_error(err) == ErrorCategory.TRANSIENT

    def test_httpx_timeout_returns_transient(self) -> None:
        """Test that httpx.TimeoutException returns ErrorCategory.TRANSIENT."""
        err = httpx.TimeoutException("timeout", request=MagicMock())
        assert classify_error(err) == ErrorCategory.TRANSIENT

    def test_timeout_error_returns_transient(self) -> None:
        """Test that TimeoutError returns ErrorCategory.TRANSIENT."""
        assert classify_error(TimeoutError("timed out")) == ErrorCategory.TRANSIENT

    def test_httpx_5xx_in_range_returns_transient(self) -> None:
        """Test that httpx.HTTPStatusError with 5xx status code returns ErrorCategory.TRANSIENT."""
        for code in (500, 501):
            resp = MagicMock()
            resp.status_code = code
            err = httpx.HTTPStatusError(str(code), request=MagicMock(), response=resp)
            assert classify_error(err) == ErrorCategory.TRANSIENT

    def test_httpx_4xx_except_429_returns_configuration(self) -> None:
        """Test that httpx.HTTPStatusError with 4xx status code returns ErrorCategory.CONFIGURATION."""
        for code in (400, 401, 403, 404):
            resp = MagicMock()
            resp.status_code = code
            err = httpx.HTTPStatusError(str(code), request=MagicMock(), response=resp)
            assert classify_error(err) == ErrorCategory.CONFIGURATION

    def test_other_exception_returns_unknown(self) -> None:
        """Test that other exceptions return ErrorCategory.UNKNOWN."""
        assert classify_error(RuntimeError("x")) == ErrorCategory.UNKNOWN
        assert classify_error(ValueError("something else")) == ErrorCategory.UNKNOWN

    def test_cta_tracker_api_error_102_returns_daily_quota(self) -> None:
        """Test that CTATrackerAPIError with err_cd=102 returns ErrorCategory.DAILY_QUOTA."""
        from cta_eta.data_collection.exceptions import CTATrackerAPIError

        err = CTATrackerAPIError(err_cd="102", err_nm="Daily limit exceeded")
        assert classify_error(err) == ErrorCategory.DAILY_QUOTA

    def test_cta_tracker_api_error_configuration_codes_return_configuration(
        self,
    ) -> None:
        """Test that CTATrackerAPIError with config error codes returns ErrorCategory.CONFIGURATION."""
        from cta_eta.data_collection.exceptions import CTATrackerAPIError

        for err_cd in ("100", "101", "106", "107", "500"):
            err = CTATrackerAPIError(err_cd=err_cd, err_nm=f"Error {err_cd}")
            assert classify_error(err) == ErrorCategory.CONFIGURATION

    def test_cta_tracker_api_error_unknown_code_returns_configuration(self) -> None:
        """Test that CTATrackerAPIError with unknown error code returns ErrorCategory.CONFIGURATION."""
        from cta_eta.data_collection.exceptions import CTATrackerAPIError

        err = CTATrackerAPIError(err_cd="999", err_nm="Unknown error")
        assert classify_error(err) == ErrorCategory.CONFIGURATION

    def test_httpx_status_error_with_102_in_body_returns_daily_quota(self) -> None:
        """Test that HTTPStatusError with errCd=102 in response body returns ErrorCategory.DAILY_QUOTA."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "ctatt": {"errCd": "102", "errNm": "Daily limit exceeded"}
        }
        err = httpx.HTTPStatusError("200", request=MagicMock(), response=resp)
        assert classify_error(err) == ErrorCategory.DAILY_QUOTA

    def test_httpx_status_error_with_500_in_body_returns_configuration(self) -> None:
        """Test that HTTPStatusError with errCd=500 in response body returns ErrorCategory.CONFIGURATION."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "ctatt": {"errCd": "500", "errNm": "Internal server error"}
        }
        err = httpx.HTTPStatusError("200", request=MagicMock(), response=resp)
        assert classify_error(err) == ErrorCategory.CONFIGURATION

    def test_httpx_status_error_with_err_cd_zero_uses_http_classification(
        self,
    ) -> None:
        """Test that HTTPStatusError with errCd=0 uses HTTP status code classification."""
        resp = MagicMock()
        resp.status_code = 500
        resp.json.return_value = {"ctatt": {"errCd": "0", "errNm": None}}
        err = httpx.HTTPStatusError("500", request=MagicMock(), response=resp)
        assert classify_error(err) == ErrorCategory.TRANSIENT

    def test_httpx_status_error_with_no_err_cd_uses_http_classification(self) -> None:
        """Test that HTTPStatusError without errCd uses HTTP status code classification."""
        resp = MagicMock()
        resp.status_code = 404
        resp.json.return_value = {"ctatt": {"tmst": "2026-01-25T12:00:00"}}
        err = httpx.HTTPStatusError("404", request=MagicMock(), response=resp)
        assert classify_error(err) == ErrorCategory.CONFIGURATION

    def test_httpx_status_error_with_invalid_json_uses_http_classification(
        self,
    ) -> None:
        """Test that HTTPStatusError with invalid JSON uses HTTP status code classification."""
        resp = MagicMock()
        resp.status_code = 501
        resp.json.side_effect = ValueError("Invalid JSON")
        err = httpx.HTTPStatusError("501", request=MagicMock(), response=resp)
        assert classify_error(err) == ErrorCategory.TRANSIENT


class TestDiscoveryStateMarker:
    """Tests for DiscoveryStateMarker."""

    def test_start_writes_initial_payload(self) -> None:
        """Test that start writes initial payload."""
        writes: list[dict] = []

        def capture(d: dict) -> None:
            writes.append(d)

        m = DiscoveryStateMarker(provider="p", total=5, write=capture, daemon_class="D")
        m.start()
        assert len(writes) == 1
        assert writes[0]["daemon_class"] == "D"
        assert writes[0]["provider"] == "p"
        assert writes[0]["status"] == "in_progress"
        assert writes[0]["total"] == 5
        assert writes[0]["succeeded"] == 0
        assert writes[0]["failed"] == 0

    def test_success_increments_succeeded_and_writes(self) -> None:
        """Test that success increments succeeded and writes."""
        writes: list[dict] = []

        def capture(d: dict) -> None:
            writes.append(d.copy())

        m = DiscoveryStateMarker(provider="p", total=5, write=capture, daemon_class="D")
        m.start()
        m.success()
        m.success()
        assert writes[-1]["succeeded"] == 2
        assert writes[-1]["failed"] == 0

    def test_failure_increments_failed_and_writes(self) -> None:
        """Test that failure increments failed and writes."""
        writes: list[dict] = []

        def capture(d: dict) -> None:
            writes.append(d.copy())

        m = DiscoveryStateMarker(provider="p", total=5, write=capture, daemon_class="D")
        m.start()
        m.failure()
        assert writes[-1]["succeeded"] == 0
        assert writes[-1]["failed"] == 1

    def test_finish_sets_status_and_error_when_provided(self) -> None:
        """Test that finish sets status and error when provided."""
        writes: list[dict] = []

        def capture(d: dict) -> None:
            writes.append(d.copy())

        m = DiscoveryStateMarker(provider="p", total=5, write=capture, daemon_class="D")
        m.start()
        m.finish("failed", error=ValueError("x"))
        assert writes[-1]["status"] == "failed"
        assert writes[-1]["error_type"] == "ValueError"
        assert writes[-1]["error_message"] == "x"

    def test_finish_sets_status_without_error(self) -> None:
        """Test that finish sets status without error."""
        writes: list[dict] = []

        def capture(d: dict) -> None:
            writes.append(d.copy())

        m = DiscoveryStateMarker(provider="p", total=5, write=capture, daemon_class="D")
        m.start()
        m.finish("completed")
        assert writes[-1]["status"] == "completed"
        assert "error_type" not in writes[-1]
