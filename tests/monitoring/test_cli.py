"""Tests for CLI monitoring tool."""

# ruff: noqa: ARG002  # Pytest fixtures appear as unused arguments

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import pytest

from cta_eta.monitoring import cli

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def daemon_state_dir(tmp_path: Path) -> Path:
    """Create temporary daemon state directory."""
    state_dir = tmp_path / ".daemon_state"
    state_dir.mkdir(exist_ok=True)

    # Patch the module-level constant
    original_dir = cli._DAEMON_STATE_DIR
    cli._DAEMON_STATE_DIR = state_dir

    yield state_dir

    # Restore original
    cli._DAEMON_STATE_DIR = original_dir


@pytest.fixture
def active_daemon(daemon_state_dir: Path) -> None:
    """Create state file for an active daemon."""
    state_file = daemon_state_dir / "TrainPositionDaemon.json"
    state = {
        "last_poll_timestamp": time.time() - 60.0,  # 1 minute ago
        "total_records_collected": 1038,
        "current_poll_count": 20,
        "train_poll_interval_seconds": 15,
    }
    with state_file.open("w", encoding="utf-8") as f:
        json.dump(state, f)


@pytest.fixture
def stale_daemon(daemon_state_dir: Path) -> None:
    """Create state file for a stale daemon."""
    state_file = daemon_state_dir / "WeatherDaemon.json"
    state = {
        "last_poll_timestamp": time.time() - 9000.0,  # 2.5 hours ago
        "total_records_collected": 145,
    }
    with state_file.open("w", encoding="utf-8") as f:
        json.dump(state, f)


@pytest.fixture
def error_events(daemon_state_dir: Path) -> None:
    """Create diagnostic events with errors."""
    # Create daemon state file so it can be discovered
    state_file = daemon_state_dir / "TrainPositionDaemon.json"
    state = {
        "last_poll_timestamp": time.time() - 60.0,
        "total_records_collected": 100,
    }
    with state_file.open("w", encoding="utf-8") as f:
        json.dump(state, f)

    # Create events file
    events_file = daemon_state_dir / "TrainPositionDaemon.events.jsonl"
    events = [
        {
            "ts": time.time() - 120.0,  # 2 minutes ago
            "kind": "error",
            "daemon_class": "TrainPositionDaemon",
            "name": "cta.fetch_train_positions",
            "error_type": "httpx.TimeoutError",
            "error_message": "Request timeout after 10.0s",
        },
        {
            "ts": time.time() - 900.0,  # 15 minutes ago
            "kind": "error",
            "daemon_class": "TrainPositionDaemon",
            "name": "cta.fetch_train_positions",
            "error_type": "httpx.HTTPStatusError",
            "error_message": "429 Rate limit exceeded",
            "http_status": 429,
        },
    ]
    with events_file.open("w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")


class TestDiscoverDaemons:
    """Tests for _discover_daemons function."""

    def test_no_state_dir(self, tmp_path: Path) -> None:
        """Test when state directory doesn't exist."""
        cli._DAEMON_STATE_DIR = tmp_path / ".daemon_state"
        result = cli._discover_daemons()
        assert result == []

    def test_empty_state_dir(self, daemon_state_dir: Path) -> None:
        """Test when state directory is empty."""
        result = cli._discover_daemons()
        assert result == []

    def test_discovers_daemons(self, daemon_state_dir: Path) -> None:
        """Test discovering daemon state files."""
        (daemon_state_dir / "TrainPositionDaemon.json").touch()
        (daemon_state_dir / "WeatherDaemon.json").touch()
        (daemon_state_dir / "TrainPositionDaemon.diagnostics.json").touch()

        result = cli._discover_daemons()
        assert sorted(result) == ["TrainPositionDaemon", "WeatherDaemon"]


class TestReadDaemonState:
    """Tests for _read_daemon_state function."""

    def test_nonexistent_file(self, daemon_state_dir: Path) -> None:
        """Test reading nonexistent state file."""
        result = cli._read_daemon_state("NonexistentDaemon")
        assert result is None

    def test_valid_state_file(self, daemon_state_dir: Path, active_daemon: None) -> None:
        """Test reading valid state file."""
        result = cli._read_daemon_state("TrainPositionDaemon")
        assert result is not None
        assert "last_poll_timestamp" in result
        assert "total_records_collected" in result
        assert result["total_records_collected"] == 1038

    def test_corrupted_json(self, daemon_state_dir: Path) -> None:
        """Test handling corrupted JSON file."""
        state_file = daemon_state_dir / "CorruptedDaemon.json"
        with state_file.open("w", encoding="utf-8") as f:
            f.write("{invalid json")

        result = cli._read_daemon_state("CorruptedDaemon")
        assert result is None


class TestFormatDuration:
    """Tests for _format_duration function."""

    def test_seconds_only(self) -> None:
        """Test formatting seconds."""
        assert cli._format_duration(30.5) == "30s"
        assert cli._format_duration(0.0) == "0s"

    def test_minutes(self) -> None:
        """Test formatting minutes."""
        assert cli._format_duration(60.0) == "1m"
        assert cli._format_duration(135.0) == "2m 15s"
        assert cli._format_duration(120.0) == "2m"

    def test_hours(self) -> None:
        """Test formatting hours."""
        assert cli._format_duration(3600.0) == "1h"
        assert cli._format_duration(9000.0) == "2h 30m"
        assert cli._format_duration(7200.0) == "2h"

    def test_days(self) -> None:
        """Test formatting days."""
        assert cli._format_duration(86400.0) == "1d"
        assert cli._format_duration(97200.0) == "1d 3h"
        assert cli._format_duration(172800.0) == "2d"


class TestClassifyStatus:
    """Tests for _classify_status function."""

    def test_unknown_staleness(self) -> None:
        """Test classifying unknown staleness."""
        assert cli._classify_status(None) == "unknown"

    def test_active_daemon(self) -> None:
        """Test classifying active daemon."""
        assert cli._classify_status(60.0) == "active"
        assert cli._classify_status(299.0) == "active"

    def test_stale_daemon(self) -> None:
        """Test classifying stale daemon."""
        assert cli._classify_status(301.0) == "stale"
        assert cli._classify_status(9000.0) == "stale"


class TestReadDiagnosticEvents:
    """Tests for _read_diagnostic_events function."""

    def test_no_events_file(self, daemon_state_dir: Path) -> None:
        """Test when events file doesn't exist."""
        result = cli._read_diagnostic_events("TrainPositionDaemon")
        assert result == []

    def test_reads_events(
        self, daemon_state_dir: Path, error_events: None
    ) -> None:
        """Test reading diagnostic events."""
        result = cli._read_diagnostic_events("TrainPositionDaemon")
        assert len(result) == 2
        # Should be sorted by timestamp descending
        assert result[0]["error_type"] == "httpx.TimeoutError"
        assert result[1]["error_type"] == "httpx.HTTPStatusError"

    def test_handles_corrupted_lines(self, daemon_state_dir: Path) -> None:
        """Test handling corrupted JSONL lines."""
        events_file = daemon_state_dir / "TestDaemon.events.jsonl"
        with events_file.open("w", encoding="utf-8") as f:
            f.write('{"ts": 1.0, "kind": "error"}\n')
            f.write("{invalid json}\n")
            f.write('{"ts": 2.0, "kind": "error"}\n')

        result = cli._read_diagnostic_events("TestDaemon")
        # Should skip corrupted line
        assert len(result) == 2


class TestCmdStatus:
    """Tests for cmd_status command."""

    def test_no_daemons(
        self, daemon_state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test status command when no daemons found."""
        args = type("Args", (), {})()
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_status(args)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "No daemons found" in captured.out

    def test_active_daemon_only(
        self,
        daemon_state_dir: Path,
        active_daemon: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test status command with active daemon."""
        args = type("Args", (), {})()
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_status(args)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "Overall: HEALTHY" in captured.out
        assert "TrainPositionDaemon" in captured.out
        assert "active" in captured.out
        assert "1,038" in captured.out or "1038" in captured.out

    def test_stale_daemon(
        self,
        daemon_state_dir: Path,
        stale_daemon: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test status command with stale daemon."""
        args = type("Args", (), {})()
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_status(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Overall: DEGRADED" in captured.out
        assert "WeatherDaemon" in captured.out
        assert "stale" in captured.out
        assert "STALE" in captured.out

    def test_mixed_daemon_states(
        self,
        daemon_state_dir: Path,
        active_daemon: None,
        stale_daemon: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test status command with both active and stale daemons."""
        args = type("Args", (), {})()
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_status(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Overall: DEGRADED" in captured.out
        assert "1 stale daemon" in captured.out


class TestCmdErrors:
    """Tests for cmd_errors command."""

    def test_no_errors(
        self, daemon_state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test errors command with no errors."""
        args = type("Args", (), {"limit": 20, "json": False})()
        cli.cmd_errors(args)

        captured = capsys.readouterr()
        assert "No errors found" in captured.out

    def test_displays_errors(
        self,
        daemon_state_dir: Path,
        error_events: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test errors command displaying errors."""
        args = type("Args", (), {"limit": 20, "json": False})()
        cli.cmd_errors(args)

        captured = capsys.readouterr()
        assert "Recent Errors" in captured.out
        assert "httpx.TimeoutError" in captured.out
        assert "httpx.HTTPStatusError" in captured.out
        assert "TrainPositionDaemon" in captured.out

    def test_limit_parameter(
        self,
        daemon_state_dir: Path,
        error_events: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test errors command with limit parameter."""
        args = type("Args", (), {"limit": 1, "json": False})()
        cli.cmd_errors(args)

        captured = capsys.readouterr()
        assert "Recent Errors (last 1)" in captured.out
        # Should only show most recent error
        assert "httpx.TimeoutError" in captured.out
        # Should not show older error (less likely, but check count)

    def test_json_output(
        self,
        daemon_state_dir: Path,
        error_events: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test errors command with JSON output."""
        args = type("Args", (), {"limit": 20, "json": True})()
        cli.cmd_errors(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert isinstance(output, list)
        assert len(output) == 2
        assert output[0]["error_type"] == "httpx.TimeoutError"
        assert output[0]["daemon_name"] == "TrainPositionDaemon"


class TestMain:
    """Tests for main entry point."""

    def test_no_command(self) -> None:
        """Test main without command."""
        with pytest.raises(SystemExit):
            cli.main([])

    def test_status_command(self, daemon_state_dir: Path) -> None:
        """Test main with status command."""
        with pytest.raises(SystemExit) as exc_info:
            cli.main(["status"])
        assert exc_info.value.code == 2  # No daemons found

    def test_errors_command(self, daemon_state_dir: Path) -> None:
        """Test main with errors command."""
        cli.main(["errors"])  # Should not raise

    def test_help_flag(self) -> None:
        """Test main with help flag."""
        with pytest.raises(SystemExit) as exc_info:
            cli.main(["--help"])
        assert exc_info.value.code == 0
