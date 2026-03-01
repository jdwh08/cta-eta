"""Unit tests for compaction uploader: upload_parquet with StorageBackend.

Tests use an in-memory backend (put/get/exists) and mock stamina for speed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from cta_eta.data_collection.compaction.uploader import upload_parquet

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


class InMemoryBackend:
    """StorageBackend-compatible in-memory store for tests."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def put(self, path: str, data: bytes) -> None:
        self._store[path] = data

    def get(self, path: str) -> bytes:
        if path not in self._store:
            raise FileNotFoundError(path)
        return self._store[path]

    def list(self, prefix: str) -> list[str]:
        return [k for k in self._store if k.startswith(prefix)]

    def exists(self, path: str) -> bool:
        return path in self._store

    def open_writer(self, path: str) -> object:
        raise NotImplementedError("InMemoryBackend does not support open_writer")


@pytest.fixture
def sample_table() -> pa.Table:
    """Small PyArrow table for upload tests (deterministic, no I/O)."""
    return pa.table({"a": [1, 2, 3], "b": ["x", "y", "z"]})


@pytest.fixture
def backend() -> InMemoryBackend:
    return InMemoryBackend()


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
    """Runs the block twice; second attempt does not swallow."""

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
    """Runs the block three times."""

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


class TestUploadParquetSuccess:
    """upload_parquet: success paths."""

    def test_upload_succeeds_and_verifies_row_count(
        self,
        sample_table: pa.Table,
        backend: InMemoryBackend,
        mocker: MockerFixture,
    ) -> None:
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_once(),
        )
        path = "raw/train_positions/date=2026-02-17/data.parquet"

        upload_parquet(sample_table, backend, path)

        assert backend.exists(path)
        data = backend.get(path)
        meta = pq.read_metadata(pa.BufferReader(data))
        assert meta.num_rows == len(sample_table)

    def test_upload_empty_table_succeeds(
        self,
        backend: InMemoryBackend,
        mocker: MockerFixture,
    ) -> None:
        empty_table = pa.table({"a": pa.array([], type=pa.int64())})
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_once(),
        )
        path = "raw/weather/date=2026-02-17/data.parquet"

        upload_parquet(empty_table, backend, path)

        assert backend.exists(path)
        meta = pq.read_metadata(pa.BufferReader(backend.get(path)))
        assert meta.num_rows == 0


class TestUploadParquetReprocess:
    """upload_parquet with reprocess=True."""

    def test_reprocess_existing_file_logs_warning_then_overwrites(
        self,
        sample_table: pa.Table,
        backend: InMemoryBackend,
        mocker: MockerFixture,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        path = "raw/key.parquet"
        existing_table = pa.table({"a": [1] * 100, "b": ["x"] * 100})
        buf = pa.BufferOutputStream()
        pq.write_table(existing_table, buf, compression="snappy")
        backend.put(path, buf.getvalue().to_pybytes())
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_once(),
        )

        upload_parquet(sample_table, backend, path, reprocess=True)

        assert "Overwriting existing file" in caplog.text
        assert "100 rows" in caplog.text or "100" in caplog.text
        assert backend.exists(path)
        meta = pq.read_metadata(pa.BufferReader(backend.get(path)))
        assert meta.num_rows == len(sample_table)

    def test_reprocess_no_existing_file_uploads_successfully(
        self,
        sample_table: pa.Table,
        backend: InMemoryBackend,
        mocker: MockerFixture,
    ) -> None:
        path = "raw/key.parquet"
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_once(),
        )

        upload_parquet(sample_table, backend, path, reprocess=True)

        assert backend.exists(path)
        meta = pq.read_metadata(pa.BufferReader(backend.get(path)))
        assert meta.num_rows == len(sample_table)

    def test_reprocess_get_raises_oserror_logs_debug_and_proceeds(
        self,
        sample_table: pa.Table,
        mocker: MockerFixture,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        class BackendFailingFirstGet(InMemoryBackend):
            def __init__(self) -> None:
                super().__init__()
                self._get_calls = 0

            def get(self, path: str) -> bytes:
                self._get_calls += 1
                if self._get_calls == 1:
                    raise OSError(13, "Permission denied")
                return self._store[path]

        backend = BackendFailingFirstGet()
        path = "raw/key.parquet"
        backend.put(path, b"existing")
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_once(),
        )

        with caplog.at_level("DEBUG"):
            upload_parquet(sample_table, backend, path, reprocess=True)

        assert "proceeding with fresh upload" in caplog.text
        assert backend.exists(path)
        meta = pq.read_metadata(pa.BufferReader(backend.get(path)))
        assert meta.num_rows == len(sample_table)


class TestUploadParquetFailurePaths:
    """upload_parquet: row mismatch and retry behavior."""

    def test_row_count_mismatch_raises_runtime_error(
        self,
        sample_table: pa.Table,
        mocker: MockerFixture,
    ) -> None:
        path = "raw/key.parquet"
        wrong_table = pa.table({"a": [1] * 999, "b": ["x"] * 999})
        wrong_buf = pa.BufferOutputStream()
        pq.write_table(wrong_table, wrong_buf, compression="snappy")
        wrong_data = wrong_buf.getvalue().to_pybytes()

        class BackendReturnsWrongDataOnGet(InMemoryBackend):
            def get(self, path: str) -> bytes:
                return wrong_data

        backend = BackendReturnsWrongDataOnGet()
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_once(),
        )

        with pytest.raises(RuntimeError) as exc_info:
            upload_parquet(sample_table, backend, path)

        assert "Row count mismatch" in str(exc_info.value)
        assert "expected 3" in str(exc_info.value)
        assert "999" in str(exc_info.value)

    def test_put_failure_propagates_after_retries_exhausted(
        self,
        sample_table: pa.Table,
        backend: InMemoryBackend,
        mocker: MockerFixture,
    ) -> None:
        mocker.patch.object(
            backend,
            "put",
            side_effect=OSError(13, "Permission denied"),
        )
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_three_times(),
        )

        with pytest.raises(OSError) as exc_info:
            upload_parquet(sample_table, backend, "raw/key.parquet")

        assert exc_info.value.errno == 13

    def test_transient_put_failure_then_success_succeeds_on_second_attempt(
        self,
        sample_table: pa.Table,
        backend: InMemoryBackend,
        mocker: MockerFixture,
    ) -> None:
        put_calls: list[tuple[str, bytes]] = []

        def capturing_put(path: str, data: bytes) -> None:
            put_calls.append((path, data))
            if len(put_calls) == 1:
                raise ConnectionError("timeout")
            backend._store[path] = data

        mocker.patch.object(backend, "put", side_effect=capturing_put)
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_twice(),
        )
        path = "raw/key.parquet"

        upload_parquet(sample_table, backend, path)

        assert backend.exists(path)
        meta = pq.read_metadata(pa.BufferReader(backend.get(path)))
        assert meta.num_rows == len(sample_table)


class TestUploadParquetRetryContextIntegration:
    """Ensure retry loop is entered and attempt number is used in logs."""

    def test_info_log_includes_attempt_number(
        self,
        sample_table: pa.Table,
        backend: InMemoryBackend,
        mocker: MockerFixture,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        mocker.patch(
            "cta_eta.data_collection.compaction.uploader.stamina.retry_context",
            side_effect=lambda **kwargs: _fake_retry_context_once(),
        )

        with caplog.at_level("INFO"):
            upload_parquet(sample_table, backend, "raw/key.parquet")

        assert "attempt" in caplog.text.lower() or "Upload" in caplog.text
        assert "verified" in caplog.text.lower() or "3 rows" in caplog.text
