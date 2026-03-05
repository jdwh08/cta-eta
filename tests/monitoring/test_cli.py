"""Unit tests for CLI monitoring tool (cta_eta.monitoring.cli).

Tests are atomic and side-effect free: daemon state, data dir, and compaction dir
use tmp_path and monkeypatch for module constants. Mocked data is written to real
temp files (JSON/JSONL) so the CLI reads from disk as in production. Use
pytest-mock only where necessary (e.g. pyarrow availability, time) for speed and
determinism.
"""

# ruff: noqa: ARG002  # Pytest fixtures appear as unused arguments

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cta_eta.data_collection.compaction.compact import cmd_schema_update
from cta_eta.monitoring import cli

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    pa = None  # type: ignore[assignment]
    pq = None  # type: ignore[assignment]


@pytest.fixture
def daemon_state_dir(tmp_path: Path, mocker: MockerFixture) -> Path:
    """Create temporary daemon state directory and patch module constant."""
    mocker.patch("cta_eta.monitoring.cli._daemon_state_dir", return_value=tmp_path)
    return tmp_path


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


@pytest.fixture
def data_dir(tmp_path: Path, mocker: MockerFixture) -> Path:
    """Temporary data directory; patches cli._DEFAULT_DATA_DIR."""
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    mocker.patch("cta_eta.monitoring.cli._data_dir", return_value=data)
    return data


@pytest.fixture
def compaction_dir(tmp_path: Path, mocker: MockerFixture) -> Path:
    """Temporary compaction directory; patches cli._DEFAULT_COMPACTION_DIR."""
    comp = tmp_path / "data" / "compaction"
    comp.mkdir(parents=True, exist_ok=True)
    mocker.patch("cta_eta.monitoring.cli._compaction_dir", return_value=comp)
    return comp


class TestDiscoverDaemons:
    """Tests for _discover_daemons function."""

    def test_no_state_dir(self, mocker: MockerFixture) -> None:
        """When state directory does not exist, returns empty list."""
        mocker.patch(
            "cta_eta.monitoring.cli._daemon_state_dir", return_value=Path.cwd()
        )
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

    def test_valid_state_file(
        self, daemon_state_dir: Path, active_daemon: None
    ) -> None:
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

    def test_reads_events(self, daemon_state_dir: Path, error_events: None) -> None:
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
        """Status with both active and stale daemons reports DEGRADED and exit 1."""
        args = type("Args", (), {})()
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_status(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Overall: DEGRADED" in captured.out
        assert "stale daemon" in captured.out

    def test_unknown_state_when_state_file_missing(
        self, daemon_state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Daemon discovered via state file but state unreadable shows unknown and exit 2."""
        (daemon_state_dir / "TrainPositionDaemon.json").touch()
        with (daemon_state_dir / "TrainPositionDaemon.json").open("wb") as f:
            f.write(b"not valid json")
        args = type("Args", (), {})()
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_status(args)
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "unknown" in captured.out
        assert "Overall: UNKNOWN" in captured.out

    def test_alternate_last_collection_timestamp(
        self, daemon_state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """State using last_collection_timestamp (no last_poll_timestamp) is handled."""
        state_file = daemon_state_dir / "TrainPositionDaemon.json"
        state = {
            "last_collection_timestamp": time.time() - 60.0,
            "total_records_collected": 500,
        }
        with state_file.open("w", encoding="utf-8") as f:
            json.dump(state, f)
        args = type("Args", (), {})()
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_status(args)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "HEALTHY" in captured.out
        assert "500" in captured.out


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
        """Errors command with --json outputs machine-readable list."""
        args = type("Args", (), {"limit": 20, "json": True})()
        cli.cmd_errors(args)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert isinstance(output, list)
        assert len(output) == 2
        assert output[0]["error_type"] == "httpx.TimeoutError"
        assert output[0]["daemon_name"] == "TrainPositionDaemon"

    def test_error_detected_by_span_name_suffix(
        self, daemon_state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Events with name ending in .error are treated as errors even without kind=error."""
        (daemon_state_dir / "WeatherDaemon.json").write_text(
            json.dumps({"last_poll_timestamp": time.time()}), encoding="utf-8"
        )
        events_file = daemon_state_dir / "WeatherDaemon.events.jsonl"
        events_file.write_text(
            json.dumps(
                {
                    "ts": time.time() - 10.0,
                    "name": "weather.fetch.error",
                    "daemon_class": "WeatherDaemon",
                    "error_type": "ValueError",
                    "error_message": "Bad response",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        args = type("Args", (), {"limit": 20, "json": True})()
        cli.cmd_errors(args)
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert len(out) == 1
        assert out[0]["span_name"] == "weather.fetch.error"
        assert out[0]["error_type"] == "ValueError"


class TestCmdGaps:
    """Tests for cmd_gaps command."""

    def test_no_dataset_dir(
        self, data_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When dataset subdir does not exist, prints message and no gaps."""
        args = type(
            "Args", (), {"dataset": "train_positions", "days": 7, "json": False}
        )()
        cli.cmd_gaps(args)
        captured = capsys.readouterr()
        assert "Dataset directory not found" in captured.out
        assert "No gaps found" in captured.out

    def test_no_gaps_found(
        self, data_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Dataset dir exists but no Parquet files; outputs no gaps."""
        dataset_dir = data_dir / "train_positions"
        dataset_dir.mkdir(parents=True, exist_ok=True)
        args = type(
            "Args", (), {"dataset": "train_positions", "days": 7, "json": False}
        )()
        cli.cmd_gaps(args)
        captured = capsys.readouterr()
        assert "No gaps found" in captured.out

    def test_pyarrow_not_installed(
        self, data_dir: Path, mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When pyarrow is not available, prints error and returns without reading files."""
        (data_dir / "train_positions").mkdir(parents=True, exist_ok=True)
        mocker.patch.object(cli, "pq", None)
        args = type(
            "Args", (), {"dataset": "train_positions", "days": 7, "json": False}
        )()
        cli.cmd_gaps(args)
        captured = capsys.readouterr()
        assert "pyarrow not installed" in captured.out

    def test_gaps_from_parquet_metadata(
        self, data_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Gap metadata is read from real Parquet schema metadata (integration-style)."""
        if pq is None or pa is None:
            pytest.skip("pyarrow required for Parquet gap metadata test")
        dataset_dir = data_dir / "train_positions"
        dataset_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(tz=UTC)
        gap_end_ts = (now - timedelta(days=1)).timestamp()
        gap_metadata = json.dumps(
            {
                "is_gap": True,
                "gap_end_timestamp": gap_end_ts,
                "gap_duration_seconds": 300.0,
                "gap_reason": "daemon_restart",
                "missed_poll_cycles": 20,
            }
        ).encode()
        table = pa.table({"dummy": [1]}).replace_schema_metadata(
            {"gap_metadata": gap_metadata}
        )
        parquet_path = dataset_dir / "gap_2026.parquet"
        pq.write_table(table, parquet_path)
        args = type(
            "Args", (), {"dataset": "train_positions", "days": 7, "json": True}
        )()
        cli.cmd_gaps(args)
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert len(out) == 1
        assert out[0]["is_gap"] is True
        assert out[0]["gap_reason"] == "daemon_restart"
        assert out[0]["missed_poll_cycles"] == 20

    def test_gap_outside_window_omitted(
        self, data_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Gaps with gap_end_timestamp before cutoff are omitted."""
        if pq is None or pa is None:
            pytest.skip("pyarrow required")
        dataset_dir = data_dir / "train_positions"
        dataset_dir.mkdir(parents=True, exist_ok=True)
        old_ts = (datetime.now(tz=UTC) - timedelta(days=30)).timestamp()
        gap_metadata = json.dumps(
            {
                "is_gap": True,
                "gap_end_timestamp": old_ts,
                "gap_duration_seconds": 100.0,
                "gap_reason": "old",
                "missed_poll_cycles": 5,
            }
        ).encode()
        table = pa.table({"dummy": [1]}).replace_schema_metadata(
            {"gap_metadata": gap_metadata}
        )
        pq.write_table(table, dataset_dir / "old_gap.parquet")
        args = type(
            "Args", (), {"dataset": "train_positions", "days": 7, "json": True}
        )()
        cli.cmd_gaps(args)
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out == []

    def test_parquet_without_gap_metadata_skipped(
        self, data_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Parquet files without gap_metadata in schema are skipped."""
        if pq is None or pa is None:
            pytest.skip("pyarrow required")
        dataset_dir = data_dir / "train_positions"
        dataset_dir.mkdir(parents=True, exist_ok=True)
        table = pa.table({"col": [1, 2, 3]})
        pq.write_table(table, dataset_dir / "normal.parquet")
        args = type(
            "Args", (), {"dataset": "train_positions", "days": 7, "json": True}
        )()
        cli.cmd_gaps(args)
        captured = capsys.readouterr()
        assert json.loads(captured.out) == []

    def test_json_output(
        self, data_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Output is valid JSON list when --json."""
        (data_dir / "train_positions").mkdir(parents=True, exist_ok=True)
        args = type(
            "Args", (), {"dataset": "train_positions", "days": 7, "json": True}
        )()
        cli.cmd_gaps(args)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert isinstance(output, list)


class TestCmdMetrics:
    """Tests for cmd_metrics command."""

    def test_no_metrics_files(
        self, daemon_state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test metrics command with no metrics files."""
        args = type("Args", (), {"window": 1, "json": False})()
        cli.cmd_metrics(args)

        captured = capsys.readouterr()
        assert "No metrics data available" in captured.out

    def test_with_metrics_data(
        self, daemon_state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test metrics command with mock metrics data."""
        # Create metrics file
        metrics_file = daemon_state_dir / "TrainPositionDaemon.metrics.jsonl"
        metrics_snapshot = {
            "ts": time.time(),
            "daemon_class": "TrainPositionDaemon",
            "diag_run_id": "test123",
            "metrics": {
                "time_window_metrics": {
                    "last_hour": {
                        "overall_success_rate": 0.95,
                        "total_calls": 240,
                        "per_span_metrics": {
                            "test_span": {
                                "success_rate": 0.95,
                                "error_rate": 0.05,
                                "total_calls": 240,
                                "p50_ms": 500.0,
                                "p95_ms": 850.0,
                                "p99_ms": 1200.0,
                            }
                        },
                    },
                    "last_24h": {
                        "overall_success_rate": 0.92,
                        "total_calls": 5760,
                        "per_span_metrics": {},
                    },
                }
            },
        }

        with metrics_file.open("w", encoding="utf-8") as f:
            f.write(json.dumps(metrics_snapshot) + "\n")

        # Create daemon state file so it gets discovered
        state_file = daemon_state_dir / "TrainPositionDaemon.json"
        with state_file.open("w", encoding="utf-8") as f:
            json.dump({"last_poll_timestamp": time.time()}, f)

        args = type("Args", (), {"window": 1, "json": False})()
        cli.cmd_metrics(args)

        captured = capsys.readouterr()
        assert "Metrics Summary" in captured.out
        assert "TrainPositionDaemon" in captured.out
        assert "95.0%" in captured.out or "95%" in captured.out

    def test_json_output(
        self, daemon_state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Metrics with --json outputs overall_status, daemons, alert_context."""
        args = type("Args", (), {"window": 1, "json": True})()
        cli.cmd_metrics(args)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "overall_status" in output
        assert "daemons" in output
        assert "alert_context" in output

    def test_window_24h_uses_last_24h_metrics(
        self, daemon_state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Window 24 uses last_24h time_window_metrics from metrics file."""
        (daemon_state_dir / "TrainPositionDaemon.json").write_text(
            json.dumps({"last_poll_timestamp": time.time()}), encoding="utf-8"
        )
        metrics_file = daemon_state_dir / "TrainPositionDaemon.metrics.jsonl"
        metrics_file.write_text(
            json.dumps(
                {
                    "ts": time.time(),
                    "metrics": {
                        "time_window_metrics": {
                            "last_hour": {
                                "overall_success_rate": 1.0,
                                "total_calls": 10,
                                "per_span_metrics": {},
                            },
                            "last_24h": {
                                "overall_success_rate": 0.85,
                                "total_calls": 2000,
                                "per_span_metrics": {"span1": {"p95_ms": 100.0}},
                            },
                        }
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        args = type("Args", (), {"window": 24, "json": False})()
        cli.cmd_metrics(args)
        captured = capsys.readouterr()
        assert "85.0%" in captured.out or "85%" in captured.out
        assert "2,000" in captured.out or "2000" in captured.out

    def test_critical_status_when_success_below_half(
        self, daemon_state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Overall status is critical when success rate < 0.5."""
        (daemon_state_dir / "TrainPositionDaemon.json").write_text(
            json.dumps({"last_poll_timestamp": time.time()}), encoding="utf-8"
        )
        (daemon_state_dir / "TrainPositionDaemon.metrics.jsonl").write_text(
            json.dumps(
                {
                    "ts": time.time(),
                    "metrics": {
                        "time_window_metrics": {
                            "last_hour": {
                                "overall_success_rate": 0.3,
                                "total_calls": 100,
                                "per_span_metrics": {},
                            },
                        }
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        args = type("Args", (), {"window": 1, "json": True})()
        cli.cmd_metrics(args)
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out["overall_status"] == "critical"
        assert any(
            v["severity"] == "critical" for v in out["alert_context"]["violations"]
        )

    def test_degraded_status_when_success_below_90(
        self, daemon_state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Overall status is degraded when success rate in [0.5, 0.9)."""
        (daemon_state_dir / "TrainPositionDaemon.json").write_text(
            json.dumps({"last_poll_timestamp": time.time()}), encoding="utf-8"
        )
        (daemon_state_dir / "TrainPositionDaemon.metrics.jsonl").write_text(
            json.dumps(
                {
                    "ts": time.time(),
                    "metrics": {
                        "time_window_metrics": {
                            "last_hour": {
                                "overall_success_rate": 0.8,
                                "total_calls": 100,
                                "per_span_metrics": {},
                            },
                        }
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        args = type("Args", (), {"window": 1, "json": True})()
        cli.cmd_metrics(args)
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out["overall_status"] == "degraded"

    def test_corrupt_metrics_line_skipped(
        self, daemon_state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Invalid JSON line in metrics file is skipped; last valid line is used."""
        (daemon_state_dir / "TrainPositionDaemon.json").write_text(
            json.dumps({"last_poll_timestamp": time.time()}), encoding="utf-8"
        )
        metrics_file = daemon_state_dir / "TrainPositionDaemon.metrics.jsonl"
        with metrics_file.open("w", encoding="utf-8") as f:
            f.write("{invalid\n")
            f.write(
                json.dumps(
                    {
                        "ts": time.time(),
                        "metrics": {
                            "time_window_metrics": {
                                "last_hour": {
                                    "overall_success_rate": 0.99,
                                    "total_calls": 50,
                                    "per_span_metrics": {},
                                },
                            }
                        },
                    }
                )
                + "\n"
            )
        args = type("Args", (), {"window": 1, "json": False})()
        cli.cmd_metrics(args)
        captured = capsys.readouterr()
        assert "99.0%" in captured.out or "99%" in captured.out


class TestCmdCompaction:
    """Tests for cmd_compaction: compaction job status and metrics."""

    def test_no_compaction_records(
        self, compaction_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When no compaction-*.json sidecars exist, prints message and returns."""
        args = type("Args", (), {"days": 7, "json": False})()
        cli.cmd_compaction(args)
        captured = capsys.readouterr()
        assert "No compaction records" in captured.out
        assert "Compaction Status" in captured.out

    def test_compaction_success_record_human(
        self,
        compaction_dir: Path,
        capsys: pytest.CaptureFixture[str],
        mocker: MockerFixture,
    ) -> None:
        """Single success record produces human-readable table and no exit."""
        sidecar = compaction_dir / "compaction-2026-02-20.json"
        sidecar.write_text(
            json.dumps(
                {
                    "date": "2026-02-20",
                    "daemon": "TrainPositionDaemon",
                    "status": "success",
                    "journals_found": 96,
                    "journals_repaired": 0,
                    "journals_skipped": 0,
                    "rows_written": 125000,
                    "upload_bytes": 5_000_000,
                    "elapsed_seconds": 12.5,
                }
            ),
            encoding="utf-8",
        )
        args = type("Args", (), {"days": 7, "json": False})()

        mock_dt = mocker.patch("cta_eta.monitoring.cli.datetime")
        mock_dt.now.return_value = datetime(2026, 2, 24, tzinfo=UTC)
        mock_dt.fromisoformat = datetime.fromisoformat

        cli.cmd_compaction(args)
        captured = capsys.readouterr()
        assert "2026-02-20" in captured.out  # Erroring out
        assert "TrainPositionDaemon" in captured.out
        assert "125,000" in captured.out
        assert "4.8 MB" in captured.out or "5.0 MB" in captured.out
        assert "failures" in captured.out

    def test_compaction_partial_and_failed(
        self,
        compaction_dir: Path,
        capsys: pytest.CaptureFixture[str],
        mocker: MockerFixture,
    ) -> None:
        """Partial and failed status render as PARTIAL and FAILED; failure triggers exit 1."""
        # Patch datetime.now to return a fixed date
        mocker_dt = mocker.patch(
            "cta_eta.monitoring.cli.datetime",
        )
        mocker_dt.now.return_value = datetime(2026, 2, 23, tzinfo=UTC)
        mocker_dt.fromisoformat = datetime.fromisoformat

        (compaction_dir / "compaction-2026-02-21.json").write_text(
            json.dumps(
                {
                    "date": "2026-02-21",
                    "daemon": "WeatherDaemon",
                    "status": "partial",
                    "journals_found": 10,
                    "journals_repaired": 1,
                    "journals_skipped": 0,
                    "rows_written": 1000,
                    "upload_bytes": 0,
                    "elapsed_seconds": 2.0,
                }
            ),
            encoding="utf-8",
        )
        (compaction_dir / "compaction-2026-02-22.json").write_text(
            json.dumps(
                {
                    "date": "2026-02-22",
                    "daemon": "WeatherDaemon",
                    "status": "failed",
                    "journals_found": 10,
                    "journals_repaired": 0,
                    "journals_skipped": 0,
                    "rows_written": 0,
                    "upload_bytes": 0,
                    "elapsed_seconds": 2.0,
                }
            ),
            encoding="utf-8",
        )
        args = type("Args", (), {"days": 7, "json": False})()
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_compaction(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "PARTIAL" in captured.out
        assert "FAILED" in captured.out
        assert "1 repaired" in captured.out

    def test_compaction_journals_skipped_display(
        self,
        compaction_dir: Path,
        capsys: pytest.CaptureFixture[str],
        mocker: MockerFixture,
    ) -> None:
        """Journals column shows (N skipped) when journals_skipped > 0."""
        # Patch datetime.now to return a fixed date
        mocker_dt = mocker.patch(
            "cta_eta.monitoring.cli.datetime",
        )
        mocker_dt.now.return_value = datetime(2026, 2, 21, tzinfo=UTC)
        mocker_dt.fromisoformat = datetime.fromisoformat

        (compaction_dir / "compaction-2026-02-23.json").write_text(
            json.dumps(
                {
                    "date": "2026-02-23",
                    "daemon": "TrainPositionDaemon",
                    "status": "success",
                    "journals_found": 50,
                    "journals_repaired": 0,
                    "journals_skipped": 3,
                    "rows_written": 10000,
                    "upload_bytes": 1000000,
                    "elapsed_seconds": 5.0,
                }
            ),
            encoding="utf-8",
        )
        args = type("Args", (), {"days": 7, "json": False})()
        cli.cmd_compaction(args)
        captured = capsys.readouterr()
        assert "50 (3 skipped)" in captured.out

    def test_compaction_json_output_and_exit_on_failure(
        self,
        compaction_dir: Path,
        capsys: pytest.CaptureFixture[str],
        mocker: MockerFixture,
    ) -> None:
        """With --json, outputs JSON array; exit 1 if any run has status failed."""
        # Patch datetime.now to return a fixed date
        mocker_dt = mocker.patch(
            "cta_eta.monitoring.cli.datetime",
        )
        mocker_dt.now.return_value = datetime(2026, 2, 24, tzinfo=UTC)
        mocker_dt.fromisoformat = datetime.fromisoformat

        (compaction_dir / "compaction-2026-02-24.json").write_text(
            json.dumps(
                {
                    "date": "2026-02-24",
                    "daemon": "TrainPositionDaemon",
                    "status": "failed",
                    "journals_found": 0,
                    "rows_written": 0,
                    "upload_bytes": 0,
                    "elapsed_seconds": 0.0,
                }
            ),
            encoding="utf-8",
        )
        args = type("Args", (), {"days": 7, "json": True})()
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_compaction(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert len(out) == 1
        assert out[0]["status"] == "failed"

    def test_compaction_days_filter(
        self, compaction_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Only sidecars with date within --days window are included."""
        base = datetime.now(tz=UTC).date()
        old_date = (base - timedelta(days=10)).isoformat()
        (compaction_dir / "compaction-old.json").write_text(
            json.dumps(
                {
                    "date": old_date,
                    "daemon": "TrainPositionDaemon",
                    "status": "success",
                    "journals_found": 1,
                    "rows_written": 1,
                    "upload_bytes": 0,
                    "elapsed_seconds": 0.0,
                }
            ),
            encoding="utf-8",
        )
        args = type("Args", (), {"days": 7, "json": True})()
        cli.cmd_compaction(args)
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out == []

    def test_compaction_invalid_sidecar_skipped(
        self,
        compaction_dir: Path,
        capsys: pytest.CaptureFixture[str],
        mocker: MockerFixture,
    ) -> None:
        # Patch datetime.now to return a fixed date
        mocker_dt = mocker.patch(
            "cta_eta.monitoring.cli.datetime",
        )
        mocker_dt.now.return_value = datetime(2026, 2, 25, tzinfo=UTC)
        mocker_dt.fromisoformat = datetime.fromisoformat

        """Sidecar that fails to parse or has invalid date is skipped."""
        (compaction_dir / "compaction-2026-02-25.json").write_text(
            json.dumps(
                {
                    "date": "2026-02-25",
                    "daemon": "TrainPositionDaemon",
                    "status": "success",
                    "journals_found": 1,
                    "rows_written": 1,
                    "upload_bytes": 0,
                    "elapsed_seconds": 0.0,
                }
            ),
            encoding="utf-8",
        )
        (compaction_dir / "compaction-bad.json").write_text(
            "{invalid", encoding="utf-8"
        )
        args = type("Args", (), {"days": 7, "json": True})()
        cli.cmd_compaction(args)
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert len(out) == 1
        assert out[0]["date"] == "2026-02-25"


class TestMain:
    """Tests for main entry point."""

    def test_no_command(self) -> None:
        """Without subcommand, argparse requires command and exits with 2."""
        with pytest.raises(SystemExit) as exc_info:
            cli.main([])
        assert exc_info.value.code == 2

    def test_status_command(self, daemon_state_dir: Path) -> None:
        """Main status dispatches to cmd_status; exit 2 when no daemons."""
        with pytest.raises(SystemExit) as exc_info:
            cli.main(["status"])
        assert exc_info.value.code == 2

    def test_errors_command(self, daemon_state_dir: Path) -> None:
        """Main errors dispatches to cmd_errors and does not exit."""
        cli.main(["errors"])

    def test_gaps_command(self, data_dir: Path) -> None:
        """Main gaps dispatches to cmd_gaps."""
        (data_dir / "train_positions").mkdir(parents=True, exist_ok=True)
        cli.main(["gaps"])

    def test_metrics_command(self, daemon_state_dir: Path) -> None:
        """Main metrics dispatches to cmd_metrics."""
        cli.main(["metrics"])

    def test_compaction_command(self, compaction_dir: Path) -> None:
        """Main compaction dispatches to cmd_compaction."""
        cli.main(["compaction"])

    def test_compaction_json_exit_on_failure(
        self, compaction_dir: Path, mocker: MockerFixture
    ) -> None:
        """Main compaction --json exits 1 when any run has status failed."""
        # Patch datetime.now to return a fixed date
        mocker_dt = mocker.patch(
            "cta_eta.monitoring.cli.datetime",
        )
        mocker_dt.now.return_value = datetime(2026, 2, 25, tzinfo=UTC)
        mocker_dt.fromisoformat = datetime.fromisoformat

        (compaction_dir / "compaction-2026-02-25.json").write_text(
            json.dumps(
                {
                    "date": "2026-02-25",
                    "daemon": "TrainPositionDaemon",
                    "status": "failed",
                    "journals_found": 0,
                    "rows_written": 0,
                    "upload_bytes": 0,
                    "elapsed_seconds": 0.0,
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as exc_info:
            cli.main(["compaction", "--json"])
        assert exc_info.value.code == 1

    def test_help_flag(self) -> None:
        """--help prints help and exits 0."""
        with pytest.raises(SystemExit) as exc_info:
            cli.main(["--help"])
        assert exc_info.value.code == 0


@pytest.mark.skipif(pa is None or pq is None, reason="pyarrow required")
class TestCompactionSchemaColumn:
    """Tests for the Schema column in cmd_compaction (reads schema_drift Parquet metadata)."""

    def _write_sidecar(self, compaction_dir: Path, daemon: str, date_str: str) -> None:
        """Write a minimal compaction sidecar JSON so cmd_compaction has a record to show."""
        sidecar = compaction_dir / f"compaction-{date_str}-{daemon}.json"
        sidecar.write_text(
            json.dumps(
                {
                    "date": date_str,
                    "daemon": daemon,
                    "status": "success",
                    "journals_found": 1,
                    "journals_repaired": 0,
                    "journals_skipped": 0,
                    "rows_written": 100,
                    "upload_bytes": 1000,
                    "elapsed_seconds": 1.0,
                }
            ),
            encoding="utf-8",
        )

    def _make_parquet(
        self,
        compaction_dir: Path,
        daemon: str,
        date_str: str,
        *,
        drift: bool = False,
    ) -> Path:
        """Create a staging Parquet file under the expected compaction layout."""
        parquet_dir = compaction_dir / daemon / f"date={date_str}"
        parquet_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = parquet_dir / "data.parquet"
        table = pa.table({"x": pa.array([1, 2, 3])})
        if drift:
            table = table.replace_schema_metadata({b"schema_drift": b"true"})
        pq.write_table(table, parquet_path, compression="snappy")
        return parquet_path

    def test_schema_column_ok(
        self,
        compaction_dir: Path,
        capsys: pytest.CaptureFixture[str],
        mocker: MockerFixture,
    ) -> None:
        """Schema column shows OK for a Parquet file with no schema_drift metadata."""
        # Patch datetime.now to return a fixed date
        mocker_dt = mocker.patch(
            "cta_eta.monitoring.cli.datetime",
        )
        mocker_dt.now.return_value = datetime(2026, 2, 25, tzinfo=UTC)
        mocker_dt.fromisoformat = datetime.fromisoformat

        daemon = "train_positions"
        date_str = "2026-02-25"
        self._write_sidecar(compaction_dir, daemon, date_str)
        self._make_parquet(compaction_dir, daemon, date_str, drift=False)

        args = type("Args", (), {"days": 7, "json": False})()
        cli.cmd_compaction(args)

        captured = capsys.readouterr()
        assert "OK" in captured.out

    def test_schema_column_drift(
        self,
        compaction_dir: Path,
        capsys: pytest.CaptureFixture[str],
        mocker: MockerFixture,
    ) -> None:
        """Schema column shows DRIFT for a Parquet file annotated with schema_drift=true."""
        # Patch datetime.now to return a fixed date
        mocker_dt = mocker.patch(
            "cta_eta.monitoring.cli.datetime",
        )
        mocker_dt.now.return_value = datetime(2026, 2, 25, tzinfo=UTC)
        mocker_dt.fromisoformat = datetime.fromisoformat

        daemon = "train_positions"
        date_str = "2026-02-25"
        self._write_sidecar(compaction_dir, daemon, date_str)
        self._make_parquet(compaction_dir, daemon, date_str, drift=True)

        args = type("Args", (), {"days": 7, "json": False})()
        cli.cmd_compaction(args)

        captured = capsys.readouterr()
        assert "DRIFT" in captured.out

    def test_schema_column_missing_file(
        self,
        compaction_dir: Path,
        capsys: pytest.CaptureFixture[str],
        mocker: MockerFixture,
    ) -> None:
        """Schema column shows ? when no local Parquet exists for the record."""
        # Patch datetime.now to return a fixed date
        mocker_dt = mocker.patch(
            "cta_eta.monitoring.cli.datetime",
        )
        mocker_dt.now.return_value = datetime(2026, 2, 25, tzinfo=UTC)
        mocker_dt.fromisoformat = datetime.fromisoformat

        daemon = "train_positions"
        date_str = "2026-02-25"
        self._write_sidecar(compaction_dir, daemon, date_str)
        # Do NOT create a Parquet file — only the sidecar exists

        args = type("Args", (), {"days": 7, "json": False})()
        cli.cmd_compaction(args)

        captured = capsys.readouterr()
        assert "?" in captured.out


@pytest.mark.skipif(pa is None or pq is None, reason="pyarrow required")
class TestSchemaUpdateCommand:
    """Tests for cmd_schema_update (cta-compact schema update)."""

    def _make_parquet(self, base_dir: Path, daemon: str, date_str: str) -> Path:
        """Create a staging Parquet under {base_dir}/{daemon}/date={date_str}/data.parquet."""
        parquet_dir = base_dir / daemon / f"date={date_str}"
        parquet_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = parquet_dir / "data.parquet"
        table = pa.table({"value": pa.array([1, 2, 3], type=pa.int64())})
        pq.write_table(table, parquet_path, compression="snappy")
        return parquet_path

    def test_schema_update_writes_registry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cmd_schema_update reads latest Parquet and calls save_registry with its schema."""
        daemon = "train_positions"
        date_str = "2026-02-25"
        self._make_parquet(tmp_path, daemon, date_str)

        mocker.patch(
            "cta_eta.data_collection.compaction.compact.load_config",
            return_value={"storage": {"compaction": {"staging_path": str(tmp_path)}}},
        )
        mock_save = mocker.patch(
            "cta_eta.data_collection.compaction.compact.save_registry"
        )
        mocker.patch("subprocess.run")

        args = type("Args", (), {"daemon": daemon})()
        cmd_schema_update(args)

        assert mock_save.called
        call_args = mock_save.call_args
        # First positional arg is the registry path, second is schema, third is daemon_name
        saved_schema = call_args[0][1]
        assert isinstance(saved_schema, pa.Schema)
        assert saved_schema.field("value").type == pa.int64()

    def test_schema_update_no_parquet_found(
        self,
        tmp_path: Path,
        mocker: MockerFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cmd_schema_update prints message and does not crash when no Parquet exists."""
        daemon = "train_positions"
        # Create the daemon dir but no date= subdirs or data.parquet
        (tmp_path / daemon).mkdir(parents=True, exist_ok=True)

        mocker.patch(
            "cta_eta.data_collection.compaction.compact.load_config",
            return_value={"storage": {"compaction": {"staging_path": str(tmp_path)}}},
        )

        args = type("Args", (), {"daemon": daemon})()
        cmd_schema_update(args)

        captured = capsys.readouterr()
        assert "No Parquet file found" in captured.out
