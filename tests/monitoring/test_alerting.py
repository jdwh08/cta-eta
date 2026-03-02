"""Tests for alerting module: threshold checking, cooldown, and message formatting."""

# ruff: noqa: ERA001  # Section separator comments are intentional

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import pytest
from httpx import Request, Response

from cta_eta.monitoring import alerting

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def last_alert_path(tmp_path: Path) -> Path:
    """Provide a temp path for the last alert timestamp file."""
    return tmp_path / "last_alert.json"


@pytest.fixture
def metrics_no_alert() -> dict:
    """Metrics data where should_alert is False."""
    return {
        "overall_status": "healthy",
        "should_alert": False,
        "violations": [],
    }


@pytest.fixture
def metrics_with_alert() -> dict:
    """Metrics data where should_alert is True with violations."""
    return {
        "overall_status": "critical",
        "should_alert": True,
        "violations": [
            {
                "metric": "success_rate",
                "threshold": 0.9,
                "actual": 0.45,
            }
        ],
    }


# ---------------------------------------------------------------------------
# Tests: load_last_alert_time
# ---------------------------------------------------------------------------


class TestLoadLastAlertTime:
    """Tests for load_last_alert_time()."""

    def test_returns_none_when_file_missing(self, last_alert_path: Path) -> None:
        """Returns None when the alert state file does not exist."""
        result = alerting.load_last_alert_time(last_alert_path)
        assert result is None

    def test_returns_timestamp_from_valid_file(self, last_alert_path: Path) -> None:
        """Returns the float timestamp from a valid JSON file."""
        ts = 1700000000.0
        last_alert_path.write_text(json.dumps({"last_alert": ts}), encoding="utf-8")
        result = alerting.load_last_alert_time(last_alert_path)
        assert result == ts

    def test_returns_none_for_invalid_json(self, last_alert_path: Path) -> None:
        """Returns None when the file contains invalid JSON."""
        last_alert_path.write_text("not valid json", encoding="utf-8")
        result = alerting.load_last_alert_time(last_alert_path)
        assert result is None

    def test_returns_none_for_missing_key(self, last_alert_path: Path) -> None:
        """Returns None when the JSON has no 'last_alert' key."""
        last_alert_path.write_text(json.dumps({"other_key": 123}), encoding="utf-8")
        result = alerting.load_last_alert_time(last_alert_path)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: save_alert_timestamp
# ---------------------------------------------------------------------------


class TestSaveAlertTimestamp:
    """Tests for save_alert_timestamp()."""

    def test_creates_file_with_timestamp(self, last_alert_path: Path) -> None:
        """Creates JSON file with 'last_alert' key containing unix timestamp."""
        before = time.time()
        alerting.save_alert_timestamp(last_alert_path)
        after = time.time()

        assert last_alert_path.exists()
        data = json.loads(last_alert_path.read_text(encoding="utf-8"))
        assert "last_alert" in data
        assert before <= data["last_alert"] <= after

    def test_overwrites_existing_file(self, last_alert_path: Path) -> None:
        """Overwrites existing alert timestamp file."""
        old_ts = 1000.0
        last_alert_path.write_text(json.dumps({"last_alert": old_ts}), encoding="utf-8")

        alerting.save_alert_timestamp(last_alert_path)

        data = json.loads(last_alert_path.read_text(encoding="utf-8"))
        assert data["last_alert"] > old_ts

    def test_suppresses_os_error(self, tmp_path: Path) -> None:
        """Does not raise when the path is unwritable (best-effort I/O)."""
        # Use a path inside a non-existent directory that cannot be created
        # by making tmp_path itself a read-only file (not a directory)
        bad_dir = tmp_path / "not_a_dir"
        bad_dir.write_text("blocking file", encoding="utf-8")
        bad_path = bad_dir / "last_alert.json"
        # Should not raise — best-effort
        alerting.save_alert_timestamp(bad_path)


# ---------------------------------------------------------------------------
# Tests: should_send_alert
# ---------------------------------------------------------------------------


class TestShouldSendAlert:
    """Tests for should_send_alert()."""

    def test_returns_true_when_should_alert_and_no_previous(
        self, metrics_with_alert: dict, last_alert_path: Path
    ) -> None:
        """Returns True when should_alert=True and no previous alert file."""
        result = alerting.should_send_alert(
            metrics_with_alert, last_alert_path, cooldown_hours=1
        )
        assert result is True

    def test_returns_false_when_should_alert_false(
        self, metrics_no_alert: dict, last_alert_path: Path
    ) -> None:
        """Returns False when should_alert is False, regardless of cooldown."""
        result = alerting.should_send_alert(
            metrics_no_alert, last_alert_path, cooldown_hours=1
        )
        assert result is False

    def test_returns_false_when_in_cooldown(
        self, metrics_with_alert: dict, last_alert_path: Path
    ) -> None:
        """Returns False when last alert was within cooldown window."""
        # Last alert was 30 minutes ago, cooldown is 1 hour
        recent_ts = time.time() - 1800.0
        last_alert_path.write_text(
            json.dumps({"last_alert": recent_ts}), encoding="utf-8"
        )

        result = alerting.should_send_alert(
            metrics_with_alert, last_alert_path, cooldown_hours=1
        )
        assert result is False

    def test_returns_true_when_cooldown_expired(
        self, metrics_with_alert: dict, last_alert_path: Path
    ) -> None:
        """Returns True when last alert was beyond the cooldown window."""
        # Last alert was 2 hours ago, cooldown is 1 hour
        old_ts = time.time() - 7200.0
        last_alert_path.write_text(json.dumps({"last_alert": old_ts}), encoding="utf-8")

        result = alerting.should_send_alert(
            metrics_with_alert, last_alert_path, cooldown_hours=1
        )
        assert result is True

    def test_returns_false_when_missing_should_alert_key(
        self, last_alert_path: Path
    ) -> None:
        """Returns False when metrics_data is missing 'should_alert' key (defensive)."""
        metrics: dict = {"overall_status": "healthy"}
        result = alerting.should_send_alert(metrics, last_alert_path, cooldown_hours=1)
        assert result is False

    def test_returns_true_when_invalid_alert_file(
        self, metrics_with_alert: dict, last_alert_path: Path
    ) -> None:
        """Returns True when last_alert_path has invalid JSON (treat as no previous alert)."""
        last_alert_path.write_text("invalid json content", encoding="utf-8")
        result = alerting.should_send_alert(
            metrics_with_alert, last_alert_path, cooldown_hours=1
        )
        assert result is True

    def test_returns_false_when_should_alert_false_even_with_expired_cooldown(
        self, metrics_no_alert: dict, last_alert_path: Path
    ) -> None:
        """Returns False when should_alert is False even if cooldown is expired."""
        old_ts = time.time() - 7200.0
        last_alert_path.write_text(json.dumps({"last_alert": old_ts}), encoding="utf-8")

        result = alerting.should_send_alert(
            metrics_no_alert, last_alert_path, cooldown_hours=1
        )
        assert result is False


# ---------------------------------------------------------------------------
# Tests: format_alert_message
# ---------------------------------------------------------------------------


class TestFormatAlertMessage:
    """Tests for format_alert_message()."""

    def test_empty_violations_returns_default_message(self) -> None:
        """Returns 'No specific violations reported' for empty list."""
        result = alerting.format_alert_message([])
        assert result == "No specific violations reported"

    def test_single_violation_with_all_keys(self) -> None:
        """Single violation with metric, threshold, actual keys is formatted correctly."""
        violations = [{"metric": "success_rate", "threshold": 0.9, "actual": 0.45}]
        result = alerting.format_alert_message(violations)
        assert "success_rate" in result
        assert "0.45" in result
        assert "0.9" in result

    def test_single_violation_format_structure(self) -> None:
        """Single violation follows '- metric: actual=X exceeds threshold=Y' format."""
        violations = [{"metric": "error_rate", "threshold": 0.1, "actual": 0.55}]
        result = alerting.format_alert_message(violations)
        assert "error_rate" in result
        assert "actual=" in result
        assert "threshold=" in result

    def test_multiple_violations_are_multiline(self) -> None:
        """Multiple violations produce a multi-line string."""
        violations = [
            {"metric": "success_rate", "threshold": 0.9, "actual": 0.45},
            {"metric": "error_rate", "threshold": 0.1, "actual": 0.55},
        ]
        result = alerting.format_alert_message(violations)
        lines = [line for line in result.splitlines() if line.strip()]
        assert len(lines) >= 2
        assert "success_rate" in result
        assert "error_rate" in result

    def test_violation_missing_keys_handled_gracefully(self) -> None:
        """Violations missing some keys do not raise KeyError."""
        violations = [{"metric": "unknown_metric"}]
        result = alerting.format_alert_message(violations)
        assert "unknown_metric" in result

    def test_completely_empty_violation_dict_handled(self) -> None:
        """A violation dict with no keys at all is handled gracefully."""
        violations: list[dict] = [{}]
        result = alerting.format_alert_message(violations)
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Tests: send_email_alert (Mailjet path)
# ---------------------------------------------------------------------------


class TestSendEmailAlertMailjet:
    """Tests for send_email_alert() when provider is mailjet."""

    def test_returns_true_when_mailjet_returns_200(self, mocker: MockerFixture) -> None:
        """send_email_alert with provider mailjet returns True when API returns 200."""
        mock_post = mocker.patch(
            "cta_eta.monitoring.alerting.httpx.Client",
        )
        mock_resp = mocker.Mock()
        mock_resp.status_code = 200
        mock_resp.text = "{}"
        mock_client_instance = mocker.Mock()
        mock_client_instance.__enter__ = mocker.Mock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = mocker.Mock(return_value=None)
        mock_client_instance.post = mocker.Mock(return_value=mock_resp)
        mock_post.return_value = mock_client_instance

        config = {
            "provider": "mailjet",
            "api_key": "key",
            "api_secret": "secret",
            "from_addr": "alerts@example.com",
            "to_addrs": ["ops@example.com"],
        }
        result = alerting.send_email_alert(config, "Test subject", "Body text")

        assert result is True
        call_kw = mock_client_instance.post.call_args[1]
        assert (
            call_kw["json"]["Messages"][0]["Subject"] == "[CTA ETA Alert] Test subject"
        )
        assert call_kw["json"]["Messages"][0]["TextPart"] == "Body text"
        assert call_kw["json"]["Messages"][0]["From"]["Email"] == "alerts@example.com"
        assert [t["Email"] for t in call_kw["json"]["Messages"][0]["To"]] == [
            "ops@example.com"
        ]

    def test_returns_false_when_mailjet_returns_non_200(
        self, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
    ) -> None:
        """send_email_alert with provider mailjet returns False when API returns error status."""
        mock_post = mocker.patch(
            "cta_eta.monitoring.alerting.httpx.Client",
        )
        mock_resp = Response(
            request=Request("POST", "https://api.mailjet.com/v3.1/send"),
            status_code=401,
            content=b"Unauthorized",
        )
        mock_client_instance = mocker.Mock()
        mock_client_instance.__enter__ = mocker.Mock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = mocker.Mock(return_value=None)
        mock_client_instance.post = mocker.Mock(return_value=mock_resp)
        mock_post.return_value = mock_client_instance

        config = {
            "provider": "mailjet",
            "api_key": "key",
            "api_secret": "secret",
            "from_addr": "a@example.com",
            "to_addrs": ["b@example.com"],
        }
        result = alerting.send_email_alert(config, "Sub", "Body")
        assert result is False
        assert "Unauthorized" in caplog.text

    def test_returns_false_for_unsupported_provider(
        self, mocker: MockerFixture
    ) -> None:
        """send_email_alert returns False and does not call HTTP when provider is unknown."""
        mock_client = mocker.patch(
            "cta_eta.monitoring.alerting.httpx.Client",
        )
        config = {
            "provider": "sendgrid",
            "from_addr": "a@example.com",
            "to_addrs": ["b@example.com"],
        }
        result = alerting.send_email_alert(config, "Sub", "Body")
        assert result is False
        mock_client.assert_not_called()
