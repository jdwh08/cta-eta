"""Unit and integration tests for compact.py orchestration.

Tests cover:
- Safety: archive_journals not called on upload failure; sidecar always written.
- _compact_one_daemon: no journals, all corrupt/skipped, success, schema mismatch,
  upload failure, repaired-count, multiple journals.
- _write_sidecar: content, filename, OSError handling.
- send_compaction_alert: no alerting config, alert sent, send_email_alert returns False.
- main: exception path, sidecar in finally, reprocess flag.

All I/O boundaries are mocked (discover_journals, read_ipc_with_repair, upload_parquet,
archive_journals, load_config, etc.). Table data uses real schemas from schemas.py
so schema validation and concat paths are exercised. Tests are atomic (tmp_path)
and fast (pytest-mock).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pyarrow as pa
import pytest

from cta_eta.data_collection.compaction.compact import (
    CompactionMetrics,
    _compact_one_daemon,
    _write_sidecar,
    main,
    send_compaction_alert,
)
from cta_eta.data_collection.compaction.schemas import (
    TRAIN_POSITION_SCHEMA,
    WEATHER_SCHEMA,
)

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


# ---------------------------------------------------------------------------
# Table builders using real schemas (integration with codebase)
# ---------------------------------------------------------------------------


def make_train_positions_table(rows: int = 1) -> pa.Table:
    """Build a train_positions table matching TRAIN_POSITION_SCHEMA."""
    return pa.table(
        {
            "poll_timestamp": pa.array(
                [datetime(2026, 2, 17, 12, 0, 0, tzinfo=UTC)] * rows,
                type=pa.timestamp("us", tz="UTC"),
            ),
            "api_timestamp": ["2026-02-17T12:00:00"] * rows,
            "route": ["red"] * rows,
            "train_id": ["101"] * rows,
            "lat": pa.array([41.9] * rows, type=pa.float64()),
            "lon": pa.array([-87.6] * rows, type=pa.float64()),
            "heading": pa.array([180] * rows, type=pa.int64()),
            "next_station_id": ["12345"] * rows,
            "next_station_name": ["State/Lake"] * rows,
            "destination_id": ["30396"] * rows,
            "destination_name": ["Howard"] * rows,
            "prediction_time": ["2026-02-17T12:00:00"] * rows,
            "predicted_arrival_time": ["2026-02-17T12:05:00"] * rows,
            "is_approaching": pa.array([False] * rows, type=pa.bool_()),
            "is_delayed": pa.array([False] * rows, type=pa.bool_()),
        },
        schema=TRAIN_POSITION_SCHEMA,
    )


def make_weather_table(rows: int = 1) -> pa.Table:
    """Build a weather table matching WEATHER_SCHEMA."""
    return pa.table(
        {
            "station_id": ["KORD"] * rows,
            "nws_grid_id": ["ILL103"] * rows,
            "open_meteo_grid_id": ["41.72_-87.62"] * rows,
            "latitude": pa.array([41.72] * rows, type=pa.float64()),
            "longitude": pa.array([-87.62] * rows, type=pa.float64()),
            "collection_timestamp": pa.array([1739793600.0] * rows, type=pa.float64()),
            "start_time": ["2026-02-17T12:00:00"] * rows,
            "end_time": ["2026-02-17T13:00:00"] * rows,
            "temperature_f": pa.array([32.0] * rows, type=pa.float64()),
            "prob_precip_pct": pa.array([0.0] * rows, type=pa.float64()),
            "dewpoint_f": pa.array([28.0] * rows, type=pa.float64()),
            "humidity_pct": pa.array([80.0] * rows, type=pa.float64()),
            "wind_speed_mph": pa.array([10.0] * rows, type=pa.float64()),
            "wind_direction": ["N"] * rows,
            "forecast_desc": ["Partly Cloudy"] * rows,
            "timestamp": ["2026-02-17T12:00:00"] * rows,
            "visibility_mi": pa.array([10.0] * rows, type=pa.float64()),
            "snow_depth_in": pa.array([0.0] * rows, type=pa.float64()),
            "surface_pressure_hpa": pa.array([1013.0] * rows, type=pa.float64()),
            "wind_gusts_mph": pa.array([15.0] * rows, type=pa.float64()),
            "apparent_temp_f": pa.array([28.0] * rows, type=pa.float64()),
            "rain_in": pa.array([0.0] * rows, type=pa.float64()),
            "showers_in": pa.array([0.0] * rows, type=pa.float64()),
            "snowfall_in": pa.array([0.0] * rows, type=pa.float64()),
        },
        schema=WEATHER_SCHEMA,
    )


def minimal_config(tmp_path: Path) -> dict[str, Any]:
    """Minimal config with tmp_path-based dirs (no shared /tmp)."""
    return {
        "storage": {"data_path": str(tmp_path / "data")},
        "compaction": {
            "cloud_url": "file://" + str(tmp_path / "cloud"),
            "compaction_dir": str(tmp_path / "compaction"),
            "archive_path": str(tmp_path / "archive"),
            "journal_retention_days": 7,
        },
        "alerting": {},
    }


# ---------------------------------------------------------------------------
# _compact_one_daemon: discovery and read paths
# ---------------------------------------------------------------------------


class TestCompactOneDaemonNoJournals:
    """No journal files: returns partial metrics, no upload/archive."""

    def test_no_journals_returns_partial_metrics(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[],
        )
        config = minimal_config(tmp_path)

        metrics = _compact_one_daemon(
            "train_positions", date(2026, 2, 17), config
        )

        assert metrics.status == "partial"
        assert metrics.journals_found == 0
        assert metrics.journals_repaired == 0
        assert metrics.journals_skipped == 0
        assert metrics.rows_written == 0
        assert metrics.upload_bytes == 0
        assert metrics.daemon == "train_positions"
        assert metrics.date == "2026-02-17"


class TestCompactOneDaemonAllCorruptOrSkipped:
    """All journals corrupt or schema-mismatched: partial metrics, no upload."""

    def test_all_journals_return_zero_batches(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        journal = tmp_path / "journal_120000_000001.ipc"
        journal.touch()
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[journal],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.read_ipc_with_repair",
            return_value=([], False),
        )
        config = minimal_config(tmp_path)

        metrics = _compact_one_daemon(
            "train_positions", date(2026, 2, 17), config
        )

        assert metrics.status == "partial"
        assert metrics.journals_found == 1
        assert metrics.journals_skipped == 1
        assert metrics.rows_written == 0
        assert metrics.upload_bytes == 0

    def test_breaking_drift_journal_still_merged(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Breaking drift journal is merged (continue-on-drift policy), not skipped."""
        journal = tmp_path / "journal_120000_000001.ipc"
        journal.touch()
        wrong_schema_table = pa.table(
            {"x": [1], "y": ["a"]},
            schema=pa.schema([("x", pa.int64()), ("y", pa.string())]),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[journal],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.read_ipc_with_repair",
            return_value=(wrong_schema_table.to_batches(), True),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.load_registry",
            return_value=None,
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.upload_parquet",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.archive_journals",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.prune_archive",
            return_value=[],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.bootstrap_registry",
        )
        config = minimal_config(tmp_path)

        metrics = _compact_one_daemon(
            "train_positions", date(2026, 2, 17), config
        )

        # Journal is NOT skipped — continue-on-drift policy merges all journals
        assert metrics.status == "success"
        assert metrics.journals_found == 1
        assert metrics.journals_skipped == 0
        assert metrics.rows_written == 1


class TestCompactOneDaemonSuccess:
    """Valid journals, upload succeeds: archive and prune called."""

    def test_success_calls_archive_and_prune(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        journal = tmp_path / "journal_120000_000001.ipc"
        journal.touch()
        table = make_train_positions_table(1)
        batches = table.to_batches()
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[journal],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.read_ipc_with_repair",
            return_value=(batches, True),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.upload_parquet",
        )
        mock_archive = mocker.patch(
            "cta_eta.data_collection.compaction.compact.archive_journals",
        )
        mock_prune = mocker.patch(
            "cta_eta.data_collection.compaction.compact.prune_archive",
            return_value=[],
        )
        config = minimal_config(tmp_path)

        metrics = _compact_one_daemon(
            "train_positions", date(2026, 2, 17), config
        )

        assert metrics.status == "success"
        assert metrics.rows_written == 1
        assert metrics.journals_repaired == 0
        assert metrics.journals_skipped == 0
        mock_archive.assert_called_once()
        mock_prune.assert_called_once()

    def test_repaired_journal_increments_journals_repaired(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        journal = tmp_path / "journal_120000_000001.ipc"
        journal.touch()
        table = make_train_positions_table(1)
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[journal],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.read_ipc_with_repair",
            return_value=(table.to_batches(), False),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.upload_parquet",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.archive_journals",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.prune_archive",
            return_value=[],
        )
        config = minimal_config(tmp_path)

        metrics = _compact_one_daemon(
            "train_positions", date(2026, 2, 17), config
        )

        assert metrics.status == "success"
        assert metrics.journals_repaired == 1

    def test_multiple_journals_merged(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        j1 = tmp_path / "journal_000000_000001.ipc"
        j2 = tmp_path / "journal_120000_000001.ipc"
        j1.touch()
        j2.touch()
        table = make_train_positions_table(2)
        batches = table.to_batches()
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[j1, j2],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.read_ipc_with_repair",
            side_effect=[
                ([batches[0]], True),
                ([batches[0]], True),
            ],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.upload_parquet",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.archive_journals",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.prune_archive",
            return_value=[],
        )
        config = minimal_config(tmp_path)

        metrics = _compact_one_daemon(
            "train_positions", date(2026, 2, 17), config
        )

        assert metrics.status == "success"
        assert metrics.journals_found == 2
        assert metrics.rows_written == 4

    def test_weather_daemon_uses_weather_schema(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        journal = tmp_path / "journal_120000_000001.ipc"
        journal.touch()
        table = make_weather_table(1)
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[journal],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.read_ipc_with_repair",
            return_value=(table.to_batches(), True),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.upload_parquet",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.archive_journals",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.prune_archive",
            return_value=[],
        )
        config = minimal_config(tmp_path)

        metrics = _compact_one_daemon("weather", date(2026, 2, 17), config)

        assert metrics.status == "success"
        assert metrics.daemon == "weather"
        assert metrics.rows_written == 1


class TestCompactOneDaemonUploadFailure:
    """Upload fails: failed metrics, alert sent, archive NOT called."""

    def test_archive_not_called_when_upload_raises(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        journal = tmp_path / "journal_120000_000001.ipc"
        journal.touch()
        table = make_train_positions_table(1)
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[journal],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.read_ipc_with_repair",
            return_value=(table.to_batches(), True),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.upload_parquet",
            side_effect=RuntimeError("upload failed"),
        )
        mock_archive = mocker.patch(
            "cta_eta.data_collection.compaction.compact.archive_journals",
        )
        config = minimal_config(tmp_path)

        metrics = _compact_one_daemon(
            "train_positions", date(2026, 2, 17), config
        )

        mock_archive.assert_not_called()
        assert metrics.status == "failed"
        assert metrics.error == "upload failed"

    def test_upload_failure_calls_send_compaction_alert(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        journal = tmp_path / "journal_120000_000001.ipc"
        journal.touch()
        table = make_train_positions_table(1)
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[journal],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.read_ipc_with_repair",
            return_value=(table.to_batches(), True),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.upload_parquet",
            side_effect=OSError(13, "Permission denied"),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.archive_journals",
        )
        mock_alert = mocker.patch(
            "cta_eta.data_collection.compaction.compact.send_compaction_alert",
        )
        config = minimal_config(tmp_path)

        _compact_one_daemon("train_positions", date(2026, 2, 17), config)

        mock_alert.assert_called_once()
        call_args = mock_alert.call_args
        assert call_args.args[0].status == "failed"
        assert call_args.args[1] == config


# ---------------------------------------------------------------------------
# _write_sidecar
# ---------------------------------------------------------------------------


class TestWriteSidecar:
    """Sidecar JSON write and error handling."""

    def test_writes_correct_filename_and_content(
        self, tmp_path: Path
    ) -> None:
        metrics = CompactionMetrics(
            date="2026-02-17",
            daemon="train_positions",
            status="success",
            journals_found=1,
            journals_repaired=0,
            journals_skipped=0,
            rows_written=10,
            upload_bytes=1024,
            elapsed_seconds=1.5,
        )
        compaction_dir = tmp_path / "compaction"
        compaction_dir.mkdir(parents=True)

        _write_sidecar(metrics, compaction_dir)

        sidecar = compaction_dir / "compaction-2026-02-17-train_positions.json"
        assert sidecar.exists()
        data = json.loads(sidecar.read_text())
        assert data["date"] == "2026-02-17"
        assert data["daemon"] == "train_positions"
        assert data["status"] == "success"
        assert data["rows_written"] == 10

    def test_sidecar_content_matches_asdict(self, tmp_path: Path) -> None:
        metrics = CompactionMetrics(
            date="2026-02-18",
            daemon="weather",
            status="partial",
            journals_found=0,
            journals_repaired=0,
            journals_skipped=0,
            rows_written=0,
            upload_bytes=0,
            elapsed_seconds=0.1,
            error=None,
        )
        compaction_dir = tmp_path / "out"
        compaction_dir.mkdir()

        _write_sidecar(metrics, compaction_dir)

        sidecar = compaction_dir / "compaction-2026-02-18-weather.json"
        data = json.loads(sidecar.read_text())
        expected = asdict(metrics)
        assert data == expected

    def test_oserror_logs_warning_does_not_raise(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        metrics = CompactionMetrics(
            date="2026-02-17",
            daemon="train_positions",
            status="success",
            journals_found=0,
            journals_repaired=0,
            journals_skipped=0,
            rows_written=0,
            upload_bytes=0,
            elapsed_seconds=0.0,
        )
        nonexistent = tmp_path / "nonexistent_xyz_123" / "compaction"

        _write_sidecar(metrics, nonexistent)

        assert "Failed to write compaction sidecar" in caplog.text or "sidecar" in caplog.text.lower()


# ---------------------------------------------------------------------------
# send_compaction_alert
# ---------------------------------------------------------------------------


class TestSendCompactionAlert:
    """Alert sending: config presence and send_email_alert integration."""

    def test_no_alerting_section_returns_without_sending(
        self, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_send = mocker.patch(
            "cta_eta.data_collection.compaction.compact.send_email_alert",
        )
        config: dict[str, Any] = {}
        metrics = CompactionMetrics(
            date="2026-02-17",
            daemon="train_positions",
            status="failed",
            journals_found=1,
            journals_repaired=0,
            journals_skipped=0,
            rows_written=0,
            upload_bytes=0,
            elapsed_seconds=0.0,
            error="upload failed",
        )

        send_compaction_alert(metrics, config)

        mock_send.assert_not_called()
        assert "alerting" in caplog.text.lower() or "skipped" in caplog.text.lower()

    def test_alerting_not_dict_returns_without_sending(
        self, mocker: MockerFixture
    ) -> None:
        mock_send = mocker.patch(
            "cta_eta.data_collection.compaction.compact.send_email_alert",
        )
        config = {"alerting": "not-a-dict"}
        metrics = CompactionMetrics(
            date="2026-02-17",
            daemon="weather",
            status="failed",
            journals_found=0,
            journals_repaired=0,
            journals_skipped=0,
            rows_written=0,
            upload_bytes=0,
            elapsed_seconds=0.0,
            error="error",
        )

        send_compaction_alert(metrics, config)

        mock_send.assert_not_called()

    def test_alerting_present_calls_build_email_and_send(
        self, mocker: MockerFixture
    ) -> None:
        mock_build = mocker.patch(
            "cta_eta.data_collection.compaction.compact._build_email_config",
            return_value={"provider": "mailjet"},
        )
        mock_send = mocker.patch(
            "cta_eta.data_collection.compaction.compact.send_email_alert",
            return_value=True,
        )
        config = {"alerting": {"smtp_from": "a@b.com", "smtp_to": ["c@d.com"]}}
        metrics = CompactionMetrics(
            date="2026-02-18",
            daemon="train_positions",
            status="failed",
            journals_found=2,
            journals_repaired=1,
            journals_skipped=0,
            rows_written=100,
            upload_bytes=2048,
            elapsed_seconds=2.0,
            error="Connection timeout",
        )

        send_compaction_alert(metrics, config)

        mock_build.assert_called_once_with(config["alerting"])
        mock_send.assert_called_once()
        call_kw = mock_send.call_args
        assert "2026-02-18" in call_kw.args[1]
        assert "train_positions" in call_kw.args[1]
        assert "Connection timeout" in call_kw.args[2]
        assert "Journals found: 2" in call_kw.args[2]

    def test_send_returns_false_logs_warning(
        self, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
    ) -> None:
        mocker.patch(
            "cta_eta.data_collection.compaction.compact._build_email_config",
            return_value={},
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.send_email_alert",
            return_value=False,
        )
        config = {"alerting": {"enabled": True}}
        metrics = CompactionMetrics(
            date="2026-02-17",
            daemon="weather",
            status="failed",
            journals_found=0,
            journals_repaired=0,
            journals_skipped=0,
            rows_written=0,
            upload_bytes=0,
            elapsed_seconds=0.0,
            error="err",
        )

        send_compaction_alert(metrics, config)

        assert "Failed to send" in caplog.text or "alert" in caplog.text.lower()


# ---------------------------------------------------------------------------
# main: CLI and exception path
# ---------------------------------------------------------------------------


class TestMainSidecarAlwaysWritten:
    """main() writes sidecar in finally even on upload failure."""

    def test_sidecar_written_even_on_upload_failure(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        journal = tmp_path / "journal_120000_000001.ipc"
        journal.touch()
        table = make_train_positions_table(1)
        config = minimal_config(tmp_path)
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.load_config",
            return_value=config,
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[journal],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.read_ipc_with_repair",
            return_value=(table.to_batches(), True),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.upload_parquet",
            side_effect=RuntimeError("upload failed"),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.archive_journals",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.send_compaction_alert",
        )
        mock_sidecar = mocker.patch(
            "cta_eta.data_collection.compaction.compact._write_sidecar",
        )

        main(argv=[])

        assert mock_sidecar.call_count >= 1

    def test_exception_in_compact_still_writes_sidecar(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        config = minimal_config(tmp_path)
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.load_config",
            return_value=config,
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            side_effect=ValueError("discover failed"),
        )
        mock_alert = mocker.patch(
            "cta_eta.data_collection.compaction.compact.send_compaction_alert",
        )
        mock_sidecar = mocker.patch(
            "cta_eta.data_collection.compaction.compact._write_sidecar",
        )

        main(argv=[])

        mock_alert.assert_called()
        call_metrics = mock_alert.call_args[0][0]
        assert call_metrics.status == "failed"
        assert "discover failed" in (call_metrics.error or "")
        mock_sidecar.assert_called()


class TestMainReprocessFlag:
    """--reprocess threads reprocess=True to upload_parquet."""

    def test_reprocess_flag_sets_reprocess_true(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        journal = tmp_path / "journal_120000_000001.ipc"
        journal.touch()
        table = make_train_positions_table(1)
        config = minimal_config(tmp_path)
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.load_config",
            return_value=config,
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[journal],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.read_ipc_with_repair",
            return_value=(table.to_batches(), True),
        )
        mock_upload = mocker.patch(
            "cta_eta.data_collection.compaction.compact.upload_parquet",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.archive_journals",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.prune_archive",
            return_value=[],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact._write_sidecar",
        )

        main(argv=["--reprocess", "2026-02-17"])

        assert mock_upload.call_count >= 1
        for call in mock_upload.call_args_list:
            assert call.kwargs.get("reprocess") is True

    def test_no_reprocess_flag_sets_reprocess_false(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        journal = tmp_path / "journal_120000_000001.ipc"
        journal.touch()
        table = make_train_positions_table(1)
        config = minimal_config(tmp_path)
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.load_config",
            return_value=config,
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[journal],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.read_ipc_with_repair",
            return_value=(table.to_batches(), True),
        )
        mock_upload = mocker.patch(
            "cta_eta.data_collection.compaction.compact.upload_parquet",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.archive_journals",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.prune_archive",
            return_value=[],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact._write_sidecar",
        )

        main(argv=[])

        assert mock_upload.call_count >= 1
        for call in mock_upload.call_args_list:
            assert call.kwargs.get("reprocess") is False


class TestMainTargetDate:
    """main() uses reprocess date or yesterday."""

    def test_reprocess_arg_sets_target_date(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        config = minimal_config(tmp_path)
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.load_config",
            return_value=config,
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact._write_sidecar",
        )
        discover = mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[],
        )

        main(argv=["--reprocess", "2026-02-20"])

        discover.assert_called()
        call_args = discover.call_args[0]
        assert call_args[2] == date(2026, 2, 20)

    def test_no_reprocess_uses_yesterday(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        config = minimal_config(tmp_path)
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.load_config",
            return_value=config,
        )
        fixed_now = datetime(2026, 2, 25, 12, 0, 0, tzinfo=UTC)
        mock_dt = mocker.patch(
            "cta_eta.data_collection.compaction.compact.datetime",
        )
        mock_dt.now.return_value = fixed_now
        discover = mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact._write_sidecar",
        )

        main(argv=[])

        discover.assert_called()
        expected_yesterday = (fixed_now - timedelta(days=1)).date()
        call_args = discover.call_args[0]
        actual = call_args[2]
        assert (actual.date() if hasattr(actual, "date") else actual) == expected_yesterday


# ---------------------------------------------------------------------------
# Integration-style: real IPC + real discover/read, mock upload/archive
# ---------------------------------------------------------------------------


class TestCompactIntegrationStyle:
    """Real discover_journals + read_ipc_with_repair; mock upload/archive.

    Ensures schema validation and concat use real code paths.
    """

    def test_real_ipc_discover_read_upload_mocked(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        from pyarrow import ipc

        day_dir = (
            tmp_path / "data" / "train_positions"
            / "year=2026" / "month=02" / "day=17"
        )
        day_dir.mkdir(parents=True)
        journal_path = day_dir / "journal_120000_000001.ipc"
        table = make_train_positions_table(3)
        sink = pa.OSFile(str(journal_path), "wb")
        writer = ipc.new_stream(sink, table.schema)
        for batch in table.to_batches():
            writer.write_batch(batch)
        writer.close()
        sink.close()

        config = minimal_config(tmp_path)
        config["storage"]["data_path"] = str(tmp_path / "data")
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.load_config",
            return_value=config,
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.upload_parquet",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.archive_journals",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.prune_archive",
            return_value=[],
        )

        metrics = _compact_one_daemon(
            "train_positions", date(2026, 2, 17), config
        )

        assert metrics.status == "success"
        assert metrics.journals_found == 1
        assert metrics.rows_written == 3
        assert metrics.journals_skipped == 0


# ---------------------------------------------------------------------------
# Drift detection integration tests
# ---------------------------------------------------------------------------


import pyarrow.ipc as ipc  # noqa: E402 (grouped here for locality)
import pyarrow.parquet as pq  # noqa: E402


def _write_temp_ipc(path: Path, schema: pa.Schema, rows: int = 5) -> None:
    """Write a minimal IPC stream file with null-filled rows for the given schema.

    Uses ipc.new_stream (IPC stream format) to match read_ipc_with_repair's
    ipc.open_stream reader.
    """
    table = pa.table(
        {
            name: pa.array([None] * rows, type=field.type)
            for name, field in zip(schema.names, schema)
        },
        schema=schema,
    )
    sink = pa.OSFile(str(path), "wb")
    writer = ipc.new_stream(sink, schema)
    for batch in table.to_batches():
        writer.write_batch(batch)
    writer.close()
    sink.close()


class TestDriftAlertOnBreakingDrift:
    """Breaking drift triggers send_drift_alert exactly once per day."""

    def test_alert_sent_once_not_twice_for_two_breaking_journals(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        # Build drifted schema: "route" changed from string to int64
        drifted_fields = [
            TRAIN_POSITION_SCHEMA.field(i)
            if TRAIN_POSITION_SCHEMA.field(i).name != "route"
            else pa.field("route", pa.int64())
            for i in range(len(TRAIN_POSITION_SCHEMA))
        ]
        drifted_schema = pa.schema(drifted_fields)

        journal_1 = tmp_path / "journal_000000_000001.ipc"
        journal_2 = tmp_path / "journal_120000_000001.ipc"
        _write_temp_ipc(journal_1, TRAIN_POSITION_SCHEMA)
        _write_temp_ipc(journal_2, drifted_schema)

        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[journal_1, journal_2],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.load_registry",
            return_value=TRAIN_POSITION_SCHEMA,
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.upload_parquet",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.archive_journals",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.prune_archive",
            return_value=[],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.bootstrap_registry",
        )
        mock_alert = mocker.patch(
            "cta_eta.data_collection.compaction.compact.send_drift_alert",
        )
        config = minimal_config(tmp_path)

        metrics = _compact_one_daemon("train_positions", date(2026, 2, 17), config)

        # Alert fires exactly once (not twice — only first breaking journal alerts)
        mock_alert.assert_called_once()
        # Compaction continues despite breaking drift
        assert metrics.status == "success"


class TestDriftAnnotationInParquet:
    """Merged Parquet has schema_drift=true metadata on breaking drift."""

    def test_parquet_metadata_annotated_with_schema_drift(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        import json as _json

        # Build drifted schema: "route" changed from string to int64
        drifted_fields = [
            TRAIN_POSITION_SCHEMA.field(i)
            if TRAIN_POSITION_SCHEMA.field(i).name != "route"
            else pa.field("route", pa.int64())
            for i in range(len(TRAIN_POSITION_SCHEMA))
        ]
        drifted_schema = pa.schema(drifted_fields)

        journal_1 = tmp_path / "journal_000000_000001.ipc"
        journal_2 = tmp_path / "journal_120000_000001.ipc"
        _write_temp_ipc(journal_1, TRAIN_POSITION_SCHEMA)
        _write_temp_ipc(journal_2, drifted_schema)

        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[journal_1, journal_2],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.load_registry",
            return_value=TRAIN_POSITION_SCHEMA,
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.upload_parquet",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.archive_journals",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.prune_archive",
            return_value=[],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.bootstrap_registry",
        )
        # Allow send_drift_alert to run but suppress actual email sending
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.send_email_alert",
            return_value=True,
        )
        config = minimal_config(tmp_path)
        config["alerting"] = {"smtp_from": "a@b.com", "smtp_to": ["c@d.com"]}

        metrics = _compact_one_daemon("train_positions", date(2026, 2, 17), config)

        assert metrics.status == "success"

        # Locate the local staging Parquet and verify drift metadata
        local_parquet = (
            Path(config["compaction"]["compaction_dir"])
            / "train_positions"
            / "date=2026-02-17"
            / "data.parquet"
        )
        assert local_parquet.exists(), f"Parquet not found at {local_parquet}"
        meta = pq.read_metadata(local_parquet)
        assert meta.metadata.get(b"schema_drift") == b"true", (
            "Expected schema_drift=true in Parquet metadata"
        )
        assert b"drift_summary" in meta.metadata, (
            "Expected drift_summary in Parquet metadata"
        )
        drift_data = _json.loads(meta.metadata[b"drift_summary"])
        assert "breaking_fields" in drift_data


class TestBootstrapOnFirstRun:
    """bootstrap_registry called after first successful compaction."""

    def test_bootstrap_called_when_registry_missing(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        journal = tmp_path / "journal_120000_000001.ipc"
        _write_temp_ipc(journal, TRAIN_POSITION_SCHEMA)

        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[journal],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.load_registry",
            return_value=None,  # no registry yet
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.upload_parquet",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.archive_journals",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.prune_archive",
            return_value=[],
        )
        mock_bootstrap = mocker.patch(
            "cta_eta.data_collection.compaction.compact.bootstrap_registry",
        )
        config = minimal_config(tmp_path)

        _compact_one_daemon("train_positions", date(2026, 2, 17), config)

        mock_bootstrap.assert_called_once()
        call_args = mock_bootstrap.call_args
        # First arg is registry_path (a Path), second is schema (pa.Schema)
        assert isinstance(call_args.args[0], Path)
        assert isinstance(call_args.args[1], pa.Schema)


class TestAdditiveDriftNoAlert:
    """Additive drift (new field) does not trigger send_drift_alert."""

    def test_no_alert_for_additive_drift(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        # Add an extra field not in TRAIN_POSITION_SCHEMA
        additive_schema = pa.schema(
            list(TRAIN_POSITION_SCHEMA) + [pa.field("extra_col", pa.string())]
        )
        journal = tmp_path / "journal_120000_000001.ipc"
        _write_temp_ipc(journal, additive_schema)

        mocker.patch(
            "cta_eta.data_collection.compaction.compact.discover_journals",
            return_value=[journal],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.load_registry",
            return_value=TRAIN_POSITION_SCHEMA,  # extra_col not in registry
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.upload_parquet",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.archive_journals",
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.prune_archive",
            return_value=[],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.compact.bootstrap_registry",
        )
        mock_alert = mocker.patch(
            "cta_eta.data_collection.compaction.compact.send_drift_alert",
        )
        config = minimal_config(tmp_path)

        metrics = _compact_one_daemon("train_positions", date(2026, 2, 17), config)

        # Additive drift does NOT trigger alert
        mock_alert.assert_not_called()
        # Compaction succeeds without error
        assert metrics.status == "success"
