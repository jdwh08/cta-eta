"""Unit tests for run_alerts module: config loading, metrics fetch, and alert flow."""

# ruff: noqa: ERA001

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cta_eta.monitoring import run_alerts

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """Path to a temporary config file (caller writes TOML)."""
    return tmp_path / "config.toml"


@pytest.fixture
def valid_alerting_config() -> dict[str, object]:
    """Minimal [alerting] section with enabled=true (API-based email only)."""
    return {
        "enabled": True,
        "cooldown_hours": 4,
        "smtp_from": "alerts@example.com",
        "smtp_to": ["ops@example.com"],
        "last_alert_state": ".daemon_state/last_alert.json",
    }


@pytest.fixture
def metrics_with_alert_context() -> dict[str, object]:
    """Full metrics dict as returned by cta-monitor metrics --json with should_alert."""
    return {
        "overall_status": "critical",
        "should_alert": True,
        "alert_context": {
            "should_alert": True,
            "violations": [
                {"metric": "success_rate", "threshold": 0.9, "actual": 0.45},
            ],
        },
        "daemons": [],
    }


# ---------------------------------------------------------------------------
# Tests: _load_alerting_config
# ---------------------------------------------------------------------------


class TestLoadAlertingConfig:
    """Tests for _load_alerting_config()."""

    def test_returns_none_when_file_missing(self, config_path: Path) -> None:
        """Returns None when config file does not exist."""
        run_alerts._DEFAULT_CONFIG_PATH = config_path
        assert not config_path.exists()
        result = run_alerts._load_alerting_config(config_path)
        assert result is None

    def test_returns_none_when_alerting_section_absent(
        self, config_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Returns None when [alerting] section is missing."""
        config_path.write_text("[other]\nkey = true\n", encoding="utf-8")
        result = run_alerts._load_alerting_config(config_path)
        assert result is None
        assert "alerting skipped" in capsys.readouterr().out.lower()

    def test_returns_none_when_enabled_false(
        self, config_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Returns None when alerting.enabled is false."""
        config_path.write_text("[alerting]\nenabled = false\n", encoding="utf-8")
        result = run_alerts._load_alerting_config(config_path)
        assert result is None
        assert "alerting skipped" in capsys.readouterr().out.lower()

    def test_returns_none_when_toml_invalid(
        self, config_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Returns None when TOML is malformed."""
        config_path.write_text("invalid toml [[[\n", encoding="utf-8")
        result = run_alerts._load_alerting_config(config_path)
        assert result is None
        out = capsys.readouterr().out
        assert "Warning" in out or "failed" in out.lower()

    def test_returns_alerting_section_when_enabled(
        self, config_path: Path, valid_alerting_config: dict[str, object]
    ) -> None:
        """Returns [alerting] dict when enabled=true."""
        config_path.write_text(
            "[alerting]\n"
            "enabled = true\n"
            "cooldown_hours = 4\n"
            'smtp_from = "alerts@example.com"\n'
            'smtp_to = ["ops@example.com"]\n'
            'last_alert_state = ".daemon_state/last_alert.json"\n',
            encoding="utf-8",
        )
        result = run_alerts._load_alerting_config(config_path)
        assert result is not None
        assert result.get("enabled") is True
        assert result.get("cooldown_hours") == 4
        assert result.get("smtp_to") == ["ops@example.com"]


# ---------------------------------------------------------------------------
# Tests: _build_email_config
# ---------------------------------------------------------------------------


class TestBuildEmailConfig:
    """Tests for _build_email_config()."""

    def test_builds_mailjet_config_by_default(
        self, valid_alerting_config: dict[str, object], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mailjet config is built when email_provider is default (mailjet)."""
        monkeypatch.setenv("MAILJET_API_KEY", "mj-key")
        monkeypatch.setenv("MAILJET_API_SECRET", "mj-secret")
        result = run_alerts._build_email_config(valid_alerting_config)
        assert result["provider"] == "mailjet"
        assert result["api_key"] == "mj-key"
        assert result["api_secret"] == "mj-secret"
        assert result["from_addr"] == "alerts@example.com"
        assert result["to_addrs"] == ["ops@example.com"]

    def test_builds_mailjet_config_when_provider_mailjet(
        self, valid_alerting_config: dict[str, object], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mailjet config is built when email_provider is explicitly mailjet."""
        valid_alerting_config["email_provider"] = "mailjet"
        monkeypatch.setenv("MAILJET_API_KEY", "mj-key")
        monkeypatch.setenv("MAILJET_API_SECRET", "mj-secret")
        result = run_alerts._build_email_config(valid_alerting_config)
        assert result["provider"] == "mailjet"
        assert result["from_addr"] == "alerts@example.com"
        assert result["to_addrs"] == ["ops@example.com"]

    def test_builds_minimal_config_for_unsupported_provider(
        self, valid_alerting_config: dict[str, object]
    ) -> None:
        """Unknown provider yields minimal config (send_email_alert will return False)."""
        valid_alerting_config["email_provider"] = "sendgrid"
        result = run_alerts._build_email_config(valid_alerting_config)
        assert result["provider"] == "sendgrid"
        assert result["from_addr"] == "alerts@example.com"
        assert result["to_addrs"] == ["ops@example.com"]

    def test_to_addrs_from_config_list(
        self, valid_alerting_config: dict[str, object], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """to_addrs is taken from config smtp_to list."""
        monkeypatch.setenv("MAILJET_API_KEY", "k")
        monkeypatch.setenv("MAILJET_API_SECRET", "s")
        valid_alerting_config["smtp_to"] = ["a@x.com", "b@x.com"]
        result = run_alerts._build_email_config(valid_alerting_config)
        assert result["to_addrs"] == ["a@x.com", "b@x.com"]


# ---------------------------------------------------------------------------
# Tests: _fetch_metrics
# ---------------------------------------------------------------------------


class TestFetchMetrics:
    """Tests for _fetch_metrics()."""

    def test_returns_parsed_json_on_success(self, mocker: pytest.MockerFixture) -> None:
        """Returns dict when subprocess exits 0 and stdout is valid JSON."""
        payload = {"status": "healthy", "daemons": []}
        mock_run = mocker.patch.object(
            subprocess,
            "run",
            return_value=mocker.Mock(
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            ),
        )
        result = run_alerts._fetch_metrics()
        assert result == payload
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "cta-monitor" in call_args
        assert "metrics" in call_args
        assert "--json" in call_args

    def test_returns_none_when_subprocess_nonzero(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """Returns None when cta-monitor exits non-zero."""
        mocker.patch.object(
            subprocess,
            "run",
            return_value=mocker.Mock(
                returncode=1,
                stdout="",
                stderr="command failed",
            ),
        )
        result = run_alerts._fetch_metrics()
        assert result is None

    def test_returns_none_when_stdout_empty(self, mocker: pytest.MockerFixture) -> None:
        """Returns None when subprocess stdout is empty."""
        mocker.patch.object(
            subprocess,
            "run",
            return_value=mocker.Mock(
                returncode=0,
                stdout="   \n",
                stderr="",
            ),
        )
        result = run_alerts._fetch_metrics()
        assert result is None

    def test_returns_none_on_timeout(self, mocker: pytest.MockerFixture) -> None:
        """Returns None when subprocess times out."""
        mocker.patch.object(
            subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("cta-monitor", 30),
        )
        result = run_alerts._fetch_metrics()
        assert result is None

    def test_returns_none_on_file_not_found(self, mocker: pytest.MockerFixture) -> None:
        """Returns None when cta-monitor is not found."""
        mocker.patch.object(
            subprocess,
            "run",
            side_effect=FileNotFoundError(),
        )
        result = run_alerts._fetch_metrics()
        assert result is None

    def test_returns_none_when_stdout_not_valid_json(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """Returns None when stdout is not valid JSON."""
        mocker.patch.object(
            subprocess,
            "run",
            return_value=mocker.Mock(
                returncode=0,
                stdout="not json",
                stderr="",
            ),
        )
        result = run_alerts._fetch_metrics()
        assert result is None


# ---------------------------------------------------------------------------
# Tests: main
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for main() entry point."""

    def test_exit_0_when_config_disabled(self, mocker: pytest.MockerFixture) -> None:
        """Exits 0 when alerting config is None (disabled or missing)."""
        mocker.patch.object(
            run_alerts,
            "_load_alerting_config",
            return_value=None,
        )
        with pytest.raises(SystemExit) as exc_info:
            run_alerts.main()
        assert exc_info.value.code == 0

    def test_exit_0_when_fetch_metrics_fails(
        self,
        mocker: pytest.MockerFixture,
        valid_alerting_config: dict[str, object],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Exits 0 when _fetch_metrics returns None (soft failure)."""
        mocker.patch.object(
            run_alerts,
            "_load_alerting_config",
            return_value=valid_alerting_config,
        )
        mocker.patch.object(run_alerts, "_fetch_metrics", return_value=None)
        with pytest.raises(SystemExit) as exc_info:
            run_alerts.main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "could not fetch" in out.lower() or "Warning" in out

    def test_exit_0_when_no_alert_needed(
        self,
        mocker: pytest.MockerFixture,
        valid_alerting_config: dict[str, object],
        metrics_with_alert_context: dict[str, object],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Exits 0 when should_send_alert returns False."""
        mocker.patch.object(
            run_alerts,
            "_load_alerting_config",
            return_value=valid_alerting_config,
        )
        mocker.patch.object(
            run_alerts,
            "_fetch_metrics",
            return_value=metrics_with_alert_context,
        )
        mocker.patch(
            "cta_eta.monitoring.run_alerts.should_send_alert",
            return_value=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            run_alerts.main()
        assert exc_info.value.code == 0
        assert "No alert needed" in capsys.readouterr().out

    def test_exit_0_and_saves_timestamp_when_alert_sent(
        self,
        mocker: pytest.MockerFixture,
        valid_alerting_config: dict[str, object],
        metrics_with_alert_context: dict[str, object],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When alert is sent, main returns normally and save_alert_timestamp is called."""
        last_alert_path = tmp_path / "last_alert.json"
        cfg: dict[str, Any] = {
            **valid_alerting_config,
            "last_alert_state": str(last_alert_path),
        }
        mocker.patch.object(
            run_alerts,
            "_load_alerting_config",
            return_value=cfg,
        )
        mocker.patch.object(
            run_alerts,
            "_fetch_metrics",
            return_value=metrics_with_alert_context,
        )
        mocker.patch(
            "cta_eta.monitoring.run_alerts.should_send_alert",
            return_value=True,
        )
        send_email_alert = mocker.patch(
            "cta_eta.monitoring.run_alerts.send_email_alert",
            return_value=True,
        )
        save_alert_timestamp = mocker.patch(
            "cta_eta.monitoring.run_alerts.save_alert_timestamp",
        )
        run_alerts.main()
        send_email_alert.assert_called_once()
        save_alert_timestamp.assert_called_once_with(last_alert_path)
        assert "Alert sent" in capsys.readouterr().out

    def test_exit_1_when_email_send_fails(
        self,
        mocker: pytest.MockerFixture,
        valid_alerting_config: dict[str, object],
        metrics_with_alert_context: dict[str, object],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Exits 1 when send_email_alert returns False."""
        mocker.patch.object(
            run_alerts,
            "_load_alerting_config",
            return_value=valid_alerting_config,
        )
        mocker.patch.object(
            run_alerts,
            "_fetch_metrics",
            return_value=metrics_with_alert_context,
        )
        mocker.patch(
            "cta_eta.monitoring.run_alerts.should_send_alert",
            return_value=True,
        )
        mocker.patch(
            "cta_eta.monitoring.run_alerts.send_email_alert",
            return_value=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            run_alerts.main()
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "failed" in out.lower() or "Warning" in out

    def test_alert_context_non_dict_treated_as_empty(
        self,
        mocker: pytest.MockerFixture,
        valid_alerting_config: dict[str, object],
        tmp_path: Path,
    ) -> None:
        """When alert_context is not a dict, it is treated as empty and no crash."""
        metrics: dict[str, object] = {
            "overall_status": "unknown",
            "alert_context": "not-a-dict",
        }
        mocker.patch.object(
            run_alerts,
            "_load_alerting_config",
            return_value=valid_alerting_config,
        )
        mocker.patch.object(run_alerts, "_fetch_metrics", return_value=metrics)
        mocker.patch(
            "cta_eta.monitoring.run_alerts.should_send_alert",
            return_value=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            run_alerts.main()
        assert exc_info.value.code == 0

    def test_violations_non_list_treated_as_empty(
        self,
        mocker: pytest.MockerFixture,
        valid_alerting_config: dict[str, object],
        tmp_path: Path,
    ) -> None:
        """When violations is not a list, it is treated as empty list."""
        metrics: dict[str, object] = {
            "alert_context": {"should_alert": True, "violations": "not-a-list"},
        }
        mocker.patch.object(
            run_alerts,
            "_load_alerting_config",
            return_value=valid_alerting_config,
        )
        mocker.patch.object(run_alerts, "_fetch_metrics", return_value=metrics)
        mocker.patch(
            "cta_eta.monitoring.run_alerts.should_send_alert",
            return_value=True,
        )
        format_alert_message = mocker.patch(
            "cta_eta.monitoring.run_alerts.format_alert_message",
            return_value="formatted",
        )
        mocker.patch(
            "cta_eta.monitoring.run_alerts.send_email_alert",
            return_value=True,
        )
        mocker.patch("cta_eta.monitoring.run_alerts.save_alert_timestamp")
        run_alerts.main()
        format_alert_message.assert_called_once_with([])

    def test_subject_pluralization_single_violation(
        self,
        mocker: pytest.MockerFixture,
        valid_alerting_config: dict[str, object],
        metrics_with_alert_context: dict[str, object],
    ) -> None:
        """Subject uses singular 'violation' when count is 1."""
        mocker.patch.object(
            run_alerts,
            "_load_alerting_config",
            return_value=valid_alerting_config,
        )
        mocker.patch.object(
            run_alerts,
            "_fetch_metrics",
            return_value=metrics_with_alert_context,
        )
        mocker.patch(
            "cta_eta.monitoring.run_alerts.should_send_alert",
            return_value=True,
        )
        send_email_alert = mocker.patch(
            "cta_eta.monitoring.run_alerts.send_email_alert",
            return_value=True,
        )
        mocker.patch("cta_eta.monitoring.run_alerts.save_alert_timestamp")
        run_alerts.main()
        # send_email_alert(email_config, subject, body) — subject is second positional
        subject = send_email_alert.call_args[0][1]
        assert "1 violation" in subject

    def test_subject_pluralization_multiple_violations(
        self,
        mocker: pytest.MockerFixture,
        valid_alerting_config: dict[str, object],
    ) -> None:
        """Subject uses 'violations' when count is not 1."""
        metrics: dict[str, object] = {
            "alert_context": {
                "should_alert": True,
                "violations": [
                    {"metric": "a", "threshold": 1, "actual": 2},
                    {"metric": "b", "threshold": 1, "actual": 2},
                ],
            },
        }
        mocker.patch.object(
            run_alerts,
            "_load_alerting_config",
            return_value=valid_alerting_config,
        )
        mocker.patch.object(run_alerts, "_fetch_metrics", return_value=metrics)
        mocker.patch(
            "cta_eta.monitoring.run_alerts.should_send_alert",
            return_value=True,
        )
        send_email_alert = mocker.patch(
            "cta_eta.monitoring.run_alerts.send_email_alert",
            return_value=True,
        )
        mocker.patch("cta_eta.monitoring.run_alerts.save_alert_timestamp")
        run_alerts.main()
        subject = send_email_alert.call_args[0][1]
        assert "2 violations" in subject
