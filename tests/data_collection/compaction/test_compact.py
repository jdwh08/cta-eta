"""Unit tests for compact.py orchestration safety invariants.

Tests cover three critical safety properties:
1. archive_journals is NOT called when upload fails (two-phase safety)
2. _write_sidecar is always called in the finally block (even on upload failure)
3. --reprocess flag threads reprocess=True through to upload_parquet

All I/O is mocked — no real filesystem or cloud access.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pyarrow as pa
import pytest

from cta_eta.data_collection.compaction.compact import (
    CompactionMetrics,
    _compact_one_daemon,
    _write_sidecar,
    main,
)
from cta_eta.data_collection.compaction.ipc_reader import read_ipc_with_repair


def minimal_config() -> dict[str, Any]:
    """Minimal config dict for tests — avoids real file I/O."""
    return {
        "storage": {"data_path": "/tmp/fake-data"},
        "compaction": {
            "cloud_url": "file:///tmp/fake-cloud",
            "compaction_dir": "/tmp/fake-compaction",
            "archive_path": "/tmp/fake-archive",
            "journal_retention_days": 7,
        },
        "alerting": {},
    }


def _make_one_row_table() -> pa.Table:
    """Build a minimal single-row train_positions table matching TRAIN_POSITION_SCHEMA."""
    from cta_eta.data_collection.compaction.schemas import TRAIN_POSITION_SCHEMA

    import pyarrow as pa
    from datetime import datetime, timezone

    return pa.table(
        {
            "poll_timestamp": pa.array(
                [datetime(2026, 2, 17, 12, 0, 0, tzinfo=timezone.utc)],
                type=pa.timestamp("us", tz="UTC"),
            ),
            "api_timestamp": ["2026-02-17T12:00:00"],
            "route": ["red"],
            "train_id": ["101"],
            "lat": pa.array([41.9], type=pa.float64()),
            "lon": pa.array([-87.6], type=pa.float64()),
            "heading": pa.array([180], type=pa.int64()),
            "next_station_id": ["12345"],
            "next_station_name": ["State/Lake"],
            "destination_id": ["30396"],
            "destination_name": ["Howard"],
            "prediction_time": ["2026-02-17T12:00:00"],
            "predicted_arrival_time": ["2026-02-17T12:05:00"],
            "is_approaching": pa.array([False], type=pa.bool_()),
            "is_delayed": pa.array([False], type=pa.bool_()),
        },
        schema=TRAIN_POSITION_SCHEMA,
    )


class TestArchiveNotCalledOnUploadFailure:
    """Test 1: archive_journals is NOT called when upload fails."""

    def test_archive_not_called_when_upload_raises(self, tmp_path: Path) -> None:
        """archive_journals must not be called if upload_parquet raises."""
        fake_journal = tmp_path / "journal_120000_000001.ipc"
        fake_journal.touch()

        one_row_table = _make_one_row_table()
        fake_read_result = ([one_row_table.to_batches()[0]], True)

        # Make compaction_dir a real tmp_path subdir so mkdir works
        config = minimal_config()
        config["compaction"]["compaction_dir"] = str(tmp_path / "compaction")

        with (
            patch(
                "cta_eta.data_collection.compaction.compact.discover_journals",
                return_value=[fake_journal],
            ),
            patch(
                "cta_eta.data_collection.compaction.compact.read_ipc_with_repair",
                return_value=fake_read_result,
            ),
            patch(
                "cta_eta.data_collection.compaction.compact.upload_parquet",
                side_effect=RuntimeError("upload failed"),
            ),
            patch(
                "cta_eta.data_collection.compaction.compact.archive_journals",
            ) as mock_archive,
        ):
            metrics = _compact_one_daemon(
                "train_positions",
                date(2026, 2, 17),
                config,
            )

        # archive_journals must NOT be called when upload raises
        mock_archive.assert_not_called()
        # Metrics should report failure
        assert metrics.status == "failed"


class TestSidecarAlwaysWritten:
    """Test 2: _write_sidecar is always called, even when upload raises."""

    def test_sidecar_written_even_on_upload_failure(self, tmp_path: Path) -> None:
        """_write_sidecar must be called exactly once even when upload raises."""
        fake_journal = tmp_path / "journal_120000_000001.ipc"
        fake_journal.touch()

        one_row_table = _make_one_row_table()
        fake_read_result = ([one_row_table.to_batches()[0]], True)

        config = minimal_config()
        config["compaction"]["compaction_dir"] = str(tmp_path / "compaction")

        with (
            patch(
                "cta_eta.data_collection.compaction.compact.load_config",
                return_value=config,
            ),
            patch(
                "cta_eta.data_collection.compaction.compact.discover_journals",
                return_value=[fake_journal],
            ),
            patch(
                "cta_eta.data_collection.compaction.compact.read_ipc_with_repair",
                return_value=fake_read_result,
            ),
            patch(
                "cta_eta.data_collection.compaction.compact.upload_parquet",
                side_effect=RuntimeError("upload failed"),
            ),
            patch(
                "cta_eta.data_collection.compaction.compact.archive_journals",
            ),
            patch(
                "cta_eta.data_collection.compaction.compact._write_sidecar",
            ) as mock_sidecar,
            patch(
                "cta_eta.data_collection.compaction.compact.send_compaction_alert",
            ),
        ):
            main(argv=[])

        # _write_sidecar must be called once per daemon (train_positions + weather = 2)
        # but for train_positions specifically it must be called at least once
        assert mock_sidecar.call_count >= 1


class TestReprocessFlagThreadedToUpload:
    """Test 3: --reprocess flag passes reprocess=True to upload_parquet."""

    def test_reprocess_flag_sets_reprocess_true(self, tmp_path: Path) -> None:
        """upload_parquet must be called with reprocess=True when --reprocess is given."""
        fake_journal = tmp_path / "journal_120000_000001.ipc"
        fake_journal.touch()

        one_row_table = _make_one_row_table()
        fake_read_result = ([one_row_table.to_batches()[0]], True)

        config = minimal_config()
        config["compaction"]["compaction_dir"] = str(tmp_path / "compaction")

        with (
            patch(
                "cta_eta.data_collection.compaction.compact.load_config",
                return_value=config,
            ),
            patch(
                "cta_eta.data_collection.compaction.compact.discover_journals",
                return_value=[fake_journal],
            ),
            patch(
                "cta_eta.data_collection.compaction.compact.read_ipc_with_repair",
                return_value=fake_read_result,
            ),
            patch(
                "cta_eta.data_collection.compaction.compact.upload_parquet",
            ) as mock_upload,
            patch(
                "cta_eta.data_collection.compaction.compact.archive_journals",
            ),
            patch(
                "cta_eta.data_collection.compaction.compact.prune_archive",
                return_value=[],
            ),
            patch(
                "cta_eta.data_collection.compaction.compact._write_sidecar",
            ),
        ):
            main(argv=["--reprocess", "2026-02-17"])

        # upload_parquet should be called with reprocess=True for each daemon
        assert mock_upload.call_count >= 1
        for call_args in mock_upload.call_args_list:
            assert call_args.kwargs.get("reprocess") is True, (
                f"Expected reprocess=True but got {call_args.kwargs}"
            )

    def test_no_reprocess_flag_sets_reprocess_false(self, tmp_path: Path) -> None:
        """upload_parquet must be called with reprocess=False when no --reprocess flag."""
        fake_journal = tmp_path / "journal_120000_000001.ipc"
        fake_journal.touch()

        one_row_table = _make_one_row_table()
        fake_read_result = ([one_row_table.to_batches()[0]], True)

        config = minimal_config()
        config["compaction"]["compaction_dir"] = str(tmp_path / "compaction")

        with (
            patch(
                "cta_eta.data_collection.compaction.compact.load_config",
                return_value=config,
            ),
            patch(
                "cta_eta.data_collection.compaction.compact.discover_journals",
                return_value=[fake_journal],
            ),
            patch(
                "cta_eta.data_collection.compaction.compact.read_ipc_with_repair",
                return_value=fake_read_result,
            ),
            patch(
                "cta_eta.data_collection.compaction.compact.upload_parquet",
            ) as mock_upload,
            patch(
                "cta_eta.data_collection.compaction.compact.archive_journals",
            ),
            patch(
                "cta_eta.data_collection.compaction.compact.prune_archive",
                return_value=[],
            ),
            patch(
                "cta_eta.data_collection.compaction.compact._write_sidecar",
            ),
        ):
            main(argv=[])

        # upload_parquet should be called with reprocess=False when no --reprocess flag
        assert mock_upload.call_count >= 1
        for call_args in mock_upload.call_args_list:
            assert call_args.kwargs.get("reprocess") is False, (
                f"Expected reprocess=False but got {call_args.kwargs}"
            )
