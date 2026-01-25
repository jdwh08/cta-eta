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
