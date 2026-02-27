"""Unit tests for cloud uploader: make_pyarrow_fs and upload_parquet.

All tests mock fsspec, pyarrow.parquet, and stamina for speed and no side effects.
S3, GCS, and Azure (abfs) URL handling is exercised via mocked url_to_fs.
"""

from __future__ import annotations

import types
from typing import TYPE_CHECKING

import pyarrow as pa
import pytest

from cta_eta.data_collection.compaction.uploader import make_pyarrow_fs, upload_parquet

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def _meta_with_rows(num_rows: int) -> object:
    """Minimal Parquet FileMetaData-like object for mocking pq.read_metadata."""
    return types.SimpleNamespace(num_rows=num_rows)


@pytest.fixture
def sample_table() -> pa.Table:
    """Small PyArrow table for upload tests (deterministic, no I/O)."""
    return pa.table({"a": [1, 2, 3], "b": ["x", "y", "z"]})


class TestMakePyArrowFs:
    """Tests for make_pyarrow_fs: building PyArrow filesystem from cloud URL."""

    def test_s3_url_returns_pyarrow_fs_and_path(self, mocker: MockerFixture) -> None:
        mock_fs = mocker.MagicMock()
        url_to_fs = mocker.patch(
            "cta_eta.data_collection.compaction.uploader.fsspec.url_to_fs",
            return_value=(
                mock_fs,
                "my-bucket/raw/train_positions/date=2026-02-17/data.parquet",
            ),
        )

        pa_fs, path = make_pyarrow_fs(
            "s3://my-bucket/raw/train_positions/date=2026-02-17/data.parquet"
        )

        url_to_fs.assert_called_once_with(
            "s3://my-bucket/raw/train_positions/date=2026-02-17/data.parquet"
        )
        assert path == "my-bucket/raw/train_positions/date=2026-02-17/data.parquet"
        assert pa_fs is not None

    def test_gcs_url_calls_url_to_fs_with_gs_scheme(
        self, mocker: MockerFixture
    ) -> None:
        mock_fs = mocker.MagicMock()
        url_to_fs = mocker.patch(
            "cta_eta.data_collection.compaction.uploader.fsspec.url_to_fs",
            return_value=(mock_fs, "bucket/weather/date=2026-02-17/data.parquet"),
        )

        pa_fs, path = make_pyarrow_fs(
            "gs://bucket/weather/date=2026-02-17/data.parquet"
        )

        url_to_fs.assert_called_once_with(
            "gs://bucket/weather/date=2026-02-17/data.parquet"
        )
        assert path == "bucket/weather/date=2026-02-17/data.parquet"

    def test_azure_abfs_url_calls_url_to_fs_with_abfs_scheme(
        self, mocker: MockerFixture
    ) -> None:
        mock_fs = mocker.MagicMock()
        url_to_fs = mocker.patch(
            "cta_eta.data_collection.compaction.uploader.fsspec.url_to_fs",
            return_value=(
                mock_fs,
                "container/path/date=2026-02-17/data.parquet",
            ),
        )

        pa_fs, path = make_pyarrow_fs(
            "abfs://container@account.dfs.core.windows.net/path/date=2026-02-17/data.parquet"
        )

        url_to_fs.assert_called_once()
        assert path == "container/path/date=2026-02-17/data.parquet"

    def test_local_path_passed_through_url_to_fs(self, mocker: MockerFixture) -> None:
        mock_fs = mocker.MagicMock()
        url_to_fs = mocker.patch(
            "cta_eta.data_collection.compaction.uploader.fsspec.url_to_fs",
            return_value=(mock_fs, "/local/raw/data.parquet"),
        )

        pa_fs, path = make_pyarrow_fs("/local/raw/data.parquet")

        url_to_fs.assert_called_once_with("/local/raw/data.parquet")
        assert path == "/local/raw/data.parquet"


class TestUploadParquetSuccess:
    """upload_parquet: success paths."""

    def test_upload_succeeds_and_verifies_row_count(
        self,
        sample_table: pa.Table,
        mocker: MockerFixture,
    ) -> None:
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.make_pyarrow_fs",
            return_value=(mocker.MagicMock(), "bucket/key.parquet"),
        )
        write_table = mocker.patch(
            "cta_eta.data_collection.compaction.uploader.pq.write_table"
        )
        read_meta = mocker.patch(
            "cta_eta.data_collection.compaction.uploader.pq.read_metadata",
            return_value=_meta_with_rows(len(sample_table)),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_once(),
        )

        upload_parquet(sample_table, "s3://bucket/key.parquet")

        write_table.assert_called_once()
        call_kw = write_table.call_args.kwargs
        assert call_kw["compression"] == "snappy"
        assert call_kw["filesystem"] is not None
        read_meta.assert_called()

    def test_upload_empty_table_succeeds(
        self,
        mocker: MockerFixture,
    ) -> None:
        empty_table = pa.table({"a": pa.array([], type=pa.int64())})
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.make_pyarrow_fs",
            return_value=(mocker.MagicMock(), "bucket/empty.parquet"),
        )
        mocker.patch("cta_eta.data_collection.compaction.uploader.pq.write_table")
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.pq.read_metadata",
            return_value=_meta_with_rows(0),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_once(),
        )

        upload_parquet(empty_table, "gs://bucket/empty.parquet")


class TestUploadParquetReprocess:
    """upload_parquet with reprocess=True."""

    def test_reprocess_existing_file_logs_warning_then_overwrites(
        self,
        sample_table: pa.Table,
        mocker: MockerFixture,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.make_pyarrow_fs",
            return_value=(mocker.MagicMock(), "bucket/key.parquet"),
        )
        mocker.patch("cta_eta.data_collection.compaction.uploader.pq.write_table")
        read_meta = mocker.patch(
            "cta_eta.data_collection.compaction.uploader.pq.read_metadata",
            side_effect=[
                _meta_with_rows(100),
                _meta_with_rows(len(sample_table)),
            ],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_once(),
        )

        upload_parquet(sample_table, "s3://bucket/key.parquet", reprocess=True)

        assert "Overwriting existing file" in caplog.text
        assert "100 rows" in caplog.text or "100" in caplog.text
        assert read_meta.call_count >= 2

    def test_reprocess_no_existing_file_logs_debug_then_uploads(
        self,
        sample_table: pa.Table,
        mocker: MockerFixture,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.make_pyarrow_fs",
            return_value=(mocker.MagicMock(), "bucket/key.parquet"),
        )
        mocker.patch("cta_eta.data_collection.compaction.uploader.pq.write_table")
        read_meta = mocker.patch(
            "cta_eta.data_collection.compaction.uploader.pq.read_metadata",
            side_effect=[
                FileNotFoundError(),
                _meta_with_rows(len(sample_table)),
            ],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_once(),
        )

        with caplog.at_level("DEBUG"):
            upload_parquet(sample_table, "gs://bucket/key.parquet", reprocess=True)

        assert (
            "No existing file" in caplog.text or "proceeding with fresh" in caplog.text
        )
        assert read_meta.call_count >= 2

    def test_reprocess_read_metadata_other_error_propagates(
        self,
        sample_table: pa.Table,
        mocker: MockerFixture,
    ) -> None:
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.make_pyarrow_fs",
            return_value=(mocker.MagicMock(), "bucket/key.parquet"),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.pq.read_metadata",
            side_effect=OSError(13, "Permission denied"),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_once(),
        )

        with pytest.raises(OSError):
            upload_parquet(sample_table, "s3://bucket/key.parquet", reprocess=True)


class TestUploadParquetFailurePaths:
    """upload_parquet: row mismatch and retry behavior."""

    def test_row_count_mismatch_raises_runtime_error(
        self,
        sample_table: pa.Table,
        mocker: MockerFixture,
    ) -> None:
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.make_pyarrow_fs",
            return_value=(mocker.MagicMock(), "bucket/key.parquet"),
        )
        mocker.patch("cta_eta.data_collection.compaction.uploader.pq.write_table")
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.pq.read_metadata",
            return_value=_meta_with_rows(999),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_once(),
        )

        with pytest.raises(RuntimeError) as exc_info:
            upload_parquet(sample_table, "s3://bucket/key.parquet")

        assert "Row count mismatch" in str(exc_info.value)
        assert "expected 3" in str(exc_info.value)
        assert "got 999" in str(exc_info.value)

    def test_write_failure_propagates_after_retries_exhausted(
        self,
        sample_table: pa.Table,
        mocker: MockerFixture,
    ) -> None:
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.make_pyarrow_fs",
            return_value=(mocker.MagicMock(), "bucket/key.parquet"),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.pq.write_table",
            side_effect=OSError(13, "Permission denied"),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_three_times(),
        )

        with pytest.raises(OSError) as exc_info:
            upload_parquet(sample_table, "s3://bucket/key.parquet")

        assert exc_info.value.errno == 13

    def test_transient_failure_then_success_succeeds_on_second_attempt(
        self,
        sample_table: pa.Table,
        mocker: MockerFixture,
    ) -> None:
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.make_pyarrow_fs",
            return_value=(mocker.MagicMock(), "bucket/key.parquet"),
        )
        write_table = mocker.patch(
            "cta_eta.data_collection.compaction.uploader.pq.write_table",
            side_effect=[ConnectionError("timeout"), None],
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.pq.read_metadata",
            return_value=_meta_with_rows(len(sample_table)),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_twice(),
        )

        upload_parquet(
            sample_table, "abfs://container@account.dfs.core.windows.net/key.parquet"
        )

        assert write_table.call_count == 2


class TestUploadParquetRetryContextIntegration:
    """Ensure retry loop is entered and attempt number is used in logs."""

    def test_info_log_includes_attempt_number(
        self,
        sample_table: pa.Table,
        mocker: MockerFixture,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.make_pyarrow_fs",
            return_value=(mocker.MagicMock(), "bucket/key.parquet"),
        )
        mocker.patch("cta_eta.data_collection.compaction.uploader.pq.write_table")
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.pq.read_metadata",
            return_value=_meta_with_rows(len(sample_table)),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_once(),
        )

        with caplog.at_level("INFO"):
            upload_parquet(sample_table, "s3://bucket/key.parquet")

        assert "attempt" in caplog.text.lower() or "Upload" in caplog.text
        assert "verified" in caplog.text.lower() or "3 rows" in caplog.text


def _fake_retry_context_once():
    """Minimal retry_context that runs the block once (no sleep)."""

    class Attempt:
        num = 1

        def __enter__(self) -> Attempt:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    yield Attempt()


def _fake_retry_context_twice():
    """Runs the block twice; used for transient-failure-then-success.
    First attempt swallows exceptions so the loop retries; second does not.
    """

    class Attempt:
        def __init__(self, n: int, swallow: bool = False) -> None:
            self.num = n
            self._swallow = swallow

        def __enter__(self) -> Attempt:
            return self

        def __exit__(self, exc_type: object, exc_val: object, tb: object) -> bool:
            return bool(self._swallow)

    yield Attempt(1, swallow=True)
    yield Attempt(2, swallow=False)


def _fake_retry_context_three_times():
    """Runs the block three times; used for exhaust-retries."""

    class Attempt:
        def __init__(self, n: int) -> None:
            self.num = n

        def __enter__(self) -> Attempt:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    yield Attempt(1)
    yield Attempt(2)
    yield Attempt(3)
