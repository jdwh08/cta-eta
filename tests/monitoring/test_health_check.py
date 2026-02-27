"""Unit tests for health_check module: daemon heartbeat liveness and CLI."""

# ruff: noqa: ERA001

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cta_eta.monitoring import health_check

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def daemon_state_dir(tmp_path: Path) -> Path:
    """Temporary directory used as .daemon_state; patch module constant."""
    state_dir = tmp_path / ".daemon_state"
    state_dir.mkdir(exist_ok=True)
    original = health_check._DAEMON_STATE_DIR
    health_check._DAEMON_STATE_DIR = state_dir
    yield state_dir
    health_check._DAEMON_STATE_DIR = original


@pytest.fixture
def heartbeat_file(daemon_state_dir: Path) -> Path:
    """Path to a single heartbeat file (caller writes content)."""
    return daemon_state_dir / "TrainPositionDaemon.heartbeat.json"


def write_heartbeat(
    path: Path,
    *,
    timestamp: float,
    daemon: str = "TrainPositionDaemon",
    pid: int | None = 12345,
) -> None:
    """Write a valid heartbeat JSON file."""
    path.write_text(
        json.dumps({"timestamp": timestamp, "daemon": daemon, "pid": pid}),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Tests: _read_heartbeat
# ---------------------------------------------------------------------------


class TestReadHeartbeat:
    """Tests for _read_heartbeat()."""

    def test_returns_dict_for_valid_json(self, heartbeat_file: Path) -> None:
        """Returns parsed dict when file contains valid JSON."""
        write_heartbeat(heartbeat_file, timestamp=1000.0)
        result = health_check._read_heartbeat(heartbeat_file)
        assert result is not None
        assert result["timestamp"] == 1000.0
        assert result["daemon"] == "TrainPositionDaemon"
        assert result["pid"] == 12345

    def test_returns_none_for_invalid_json(self, heartbeat_file: Path) -> None:
        """Returns None when file content is not valid JSON."""
        heartbeat_file.write_text("not valid json", encoding="utf-8")
        result = health_check._read_heartbeat(heartbeat_file)
        assert result is None

    def test_returns_none_for_missing_file(self, daemon_state_dir: Path) -> None:
        """Returns None when file does not exist (OSError)."""
        missing = daemon_state_dir / "Missing.heartbeat.json"
        result = health_check._read_heartbeat(missing)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: _check_daemons
# ---------------------------------------------------------------------------


class TestCheckDaemons:
    """Tests for _check_daemons()."""

    def test_empty_dir_returns_healthy(
        self, daemon_state_dir: Path, mocker: pytest.MockerFixture
    ) -> None:
        """No heartbeat files yields empty results and 'healthy' status."""
        mocker.patch("time.time", return_value=2000.0)
        results, status = health_check._check_daemons(threshold=600)
        assert results == []
        assert status == "healthy"

    def test_single_healthy_daemon(
        self, daemon_state_dir: Path, mocker: pytest.MockerFixture
    ) -> None:
        """One recent heartbeat yields healthy status."""
        mocker.patch("time.time", return_value=2000.0)
        path = daemon_state_dir / "TrainPositionDaemon.heartbeat.json"
        write_heartbeat(path, timestamp=2000.0 - 60.0)
        results, status = health_check._check_daemons(threshold=600)
        assert len(results) == 1
        assert results[0]["status"] == "healthy"
        assert results[0]["age_seconds"] == 60
        assert status == "healthy"

    def test_single_stale_daemon(
        self, daemon_state_dir: Path, mocker: pytest.MockerFixture
    ) -> None:
        """One old heartbeat yields degraded status."""
        mocker.patch("time.time", return_value=2000.0)
        path = daemon_state_dir / "WeatherDaemon.heartbeat.json"
        write_heartbeat(path, timestamp=2000.0 - 700.0, daemon="WeatherDaemon")
        results, status = health_check._check_daemons(threshold=600)
        assert len(results) == 1
        assert results[0]["status"] == "stale"
        assert results[0]["age_seconds"] == 700
        assert status == "degraded"

    def test_boundary_at_threshold(
        self, daemon_state_dir: Path, mocker: pytest.MockerFixture
    ) -> None:
        """Heartbeat exactly at threshold age is still healthy."""
        mocker.patch("time.time", return_value=2000.0)
        path = daemon_state_dir / "Daemon.heartbeat.json"
        write_heartbeat(path, timestamp=2000.0 - 600.0, daemon="Daemon")
        results, status = health_check._check_daemons(threshold=600)
        assert len(results) == 1
        assert results[0]["status"] == "healthy"
        assert status == "healthy"

    def test_boundary_over_threshold(
        self, daemon_state_dir: Path, mocker: pytest.MockerFixture
    ) -> None:
        """Heartbeat one second over threshold is stale."""
        mocker.patch("time.time", return_value=2000.0)
        path = daemon_state_dir / "Daemon.heartbeat.json"
        write_heartbeat(path, timestamp=2000.0 - 601.0, daemon="Daemon")
        results, status = health_check._check_daemons(threshold=600)
        assert len(results) == 1
        assert results[0]["status"] == "stale"
        assert status == "degraded"

    def test_corrupt_file_skipped_with_warning(
        self,
        daemon_state_dir: Path,
        mocker: pytest.MockerFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Corrupt heartbeat file is skipped and warning printed to stderr."""
        mocker.patch("time.time", return_value=2000.0)
        path = daemon_state_dir / "Bad.heartbeat.json"
        path.write_text("{ invalid", encoding="utf-8")
        results, status = health_check._check_daemons(threshold=600)
        assert results == []
        assert status == "healthy"
        err = capsys.readouterr().err
        assert "Could not read heartbeat" in err
        assert "Bad.heartbeat.json" in err

    def test_invalid_timestamp_skipped_with_warning(
        self,
        daemon_state_dir: Path,
        mocker: pytest.MockerFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Heartbeat with non-numeric timestamp is skipped and warning printed."""
        mocker.patch("time.time", return_value=2000.0)
        path = daemon_state_dir / "NoTs.heartbeat.json"
        path.write_text(
            json.dumps({"daemon": "NoTs", "timestamp": "not-a-number"}),
            encoding="utf-8",
        )
        results, status = health_check._check_daemons(threshold=600)
        assert results == []
        assert status == "healthy"
        err = capsys.readouterr().err
        assert "Invalid timestamp" in err

    def test_mixed_healthy_and_stale(
        self, daemon_state_dir: Path, mocker: pytest.MockerFixture
    ) -> None:
        """One healthy and one stale yields degraded and both in results."""
        mocker.patch("time.time", return_value=2000.0)
        write_heartbeat(
            daemon_state_dir / "A.heartbeat.json", timestamp=2000.0 - 60.0, daemon="A"
        )
        write_heartbeat(
            daemon_state_dir / "B.heartbeat.json", timestamp=2000.0 - 700.0, daemon="B"
        )
        results, status = health_check._check_daemons(threshold=600)
        assert len(results) == 2
        by_name = {r["name"]: r for r in results}
        assert by_name["A"]["status"] == "healthy"
        assert by_name["B"]["status"] == "stale"
        assert status == "degraded"

    def test_daemon_name_from_file_stem_when_missing_in_json(
        self, daemon_state_dir: Path, mocker: pytest.MockerFixture
    ) -> None:
        """When 'daemon' key is missing, name comes from heartbeat file stem."""
        mocker.patch("time.time", return_value=2000.0)
        path = daemon_state_dir / "CustomName.heartbeat.json"
        path.write_text(json.dumps({"timestamp": 1999.0, "pid": 999}), encoding="utf-8")
        results, _ = health_check._check_daemons(threshold=600)
        assert len(results) == 1
        assert results[0]["name"] == "CustomName.heartbeat"

    def test_float_timestamp_accepted(
        self, daemon_state_dir: Path, mocker: pytest.MockerFixture
    ) -> None:
        """Float timestamp in heartbeat is accepted."""
        mocker.patch("time.time", return_value=2000.0)
        path = daemon_state_dir / "F.heartbeat.json"
        path.write_text(
            json.dumps({"timestamp": 1999.0, "daemon": "F"}), encoding="utf-8"
        )
        results, status = health_check._check_daemons(threshold=600)
        assert len(results) == 1
        assert results[0]["age_seconds"] == 1
        assert status == "healthy"


# ---------------------------------------------------------------------------
# Tests: main (CLI)
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for main() CLI entry point."""

    def test_exit_2_when_state_dir_missing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Exits 2 when .daemon_state directory does not exist."""
        original = health_check._DAEMON_STATE_DIR
        health_check._DAEMON_STATE_DIR = tmp_path / ".daemon_state"
        assert not health_check._DAEMON_STATE_DIR.exists()
        try:
            with pytest.raises(SystemExit) as exc_info:
                health_check.main([])
            assert exc_info.value.code == 2
        finally:
            health_check._DAEMON_STATE_DIR = original
        out = capsys.readouterr().out
        assert ".daemon_state" in out or "not found" in out.lower()

    def test_exit_2_json_when_state_dir_missing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Exits 2 with JSON output when .daemon_state missing."""
        original = health_check._DAEMON_STATE_DIR
        health_check._DAEMON_STATE_DIR = tmp_path / ".daemon_state"
        try:
            with pytest.raises(SystemExit) as exc_info:
                health_check.main(["--json"])
            assert exc_info.value.code == 2
        finally:
            health_check._DAEMON_STATE_DIR = original
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["status"] == "unknown"
        assert "error" in data
        assert ".daemon_state" in data["error"]

    def test_exit_0_when_no_heartbeat_files(
        self, daemon_state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Exits 0 when state dir exists but no heartbeat files."""
        with pytest.raises(SystemExit) as exc_info:
            health_check.main(["--threshold", "600"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "No heartbeat files" in out or "Daemon Health" in out

    def test_exit_0_when_all_healthy(
        self,
        daemon_state_dir: Path,
        mocker: pytest.MockerFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Exits 0 when all daemons have fresh heartbeats."""
        mocker.patch("time.time", return_value=2000.0)
        write_heartbeat(
            daemon_state_dir / "A.heartbeat.json", timestamp=1999.0, daemon="A"
        )
        with pytest.raises(SystemExit) as exc_info:
            health_check.main(["--threshold", "600"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "HEALTHY" in out

    def test_exit_1_when_any_stale(
        self,
        daemon_state_dir: Path,
        mocker: pytest.MockerFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Exits 1 when at least one daemon has stale heartbeat."""
        mocker.patch("time.time", return_value=2000.0)
        write_heartbeat(
            daemon_state_dir / "Stale.heartbeat.json",
            timestamp=2000.0 - 700.0,
            daemon="Stale",
        )
        with pytest.raises(SystemExit) as exc_info:
            health_check.main(["--threshold", "600"])
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "DEGRADED" in out
        assert "stale" in out.lower()

    def test_json_output_structure_when_healthy(
        self,
        daemon_state_dir: Path,
        mocker: pytest.MockerFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--json produces valid JSON with status, threshold_seconds, daemons."""
        mocker.patch("time.time", return_value=2000.0)
        write_heartbeat(
            daemon_state_dir / "A.heartbeat.json", timestamp=1999.0, daemon="A"
        )
        with pytest.raises(SystemExit) as exc_info:
            health_check.main(["--json", "--threshold", "300"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["status"] == "healthy"
        assert data["threshold_seconds"] == 300
        assert isinstance(data["daemons"], list)
        assert len(data["daemons"]) == 1
        assert data["daemons"][0]["name"] == "A"
        assert data["daemons"][0]["status"] == "healthy"

    def test_json_output_exit_1_when_degraded(
        self,
        daemon_state_dir: Path,
        mocker: pytest.MockerFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--json with degraded status exits 1 and JSON has status degraded."""
        mocker.patch("time.time", return_value=2000.0)
        write_heartbeat(
            daemon_state_dir / "S.heartbeat.json", timestamp=2000.0 - 700.0, daemon="S"
        )
        with pytest.raises(SystemExit) as exc_info:
            health_check.main(["--json", "--threshold", "600"])
        assert exc_info.value.code == 1
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "degraded"

    def test_custom_threshold_passed_to_check(
        self,
        daemon_state_dir: Path,
        mocker: pytest.MockerFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Custom --threshold is used for staleness check."""
        mocker.patch("time.time", return_value=2000.0)
        write_heartbeat(
            daemon_state_dir / "D.heartbeat.json", timestamp=2000.0 - 400.0, daemon="D"
        )
        with pytest.raises(SystemExit) as exc_info:
            health_check.main(["--threshold", "300"])
        assert exc_info.value.code == 1
        with pytest.raises(SystemExit) as exc_info2:
            health_check.main(["--threshold", "600"])
        assert exc_info2.value.code == 0

    def test_human_table_contains_daemon_status_age(
        self,
        daemon_state_dir: Path,
        mocker: pytest.MockerFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Human-readable output includes table with Daemon, Status, Age, PID."""
        mocker.patch("time.time", return_value=2000.0)
        write_heartbeat(
            daemon_state_dir / "T.heartbeat.json", timestamp=1990.0, daemon="T", pid=42
        )
        with pytest.raises(SystemExit):
            health_check.main(["--threshold", "600"])
        out = capsys.readouterr().out
        assert "Daemon" in out
        assert "Status" in out
        assert "Age" in out
        assert "PID" in out
        assert "T" in out or "pid=42" in out
