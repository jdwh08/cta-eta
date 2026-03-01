"""Cloud uploader for compacted Parquet files.

Provides upload_parquet() with stamina retry (3 attempts, exponential backoff)
and post-upload row count verification via Parquet metadata. Uses StorageBackend
(put/get/exists) for pluggable local and cloud storage.
"""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

import pyarrow.parquet as pq
import stamina

if TYPE_CHECKING:
    import pyarrow as pa

    from cta_eta.data_collection.storage_cache.storage import StorageBackend

logger = logging.getLogger(__name__)


def upload_parquet(
    table: pa.Table,
    backend: StorageBackend,
    path: str,
    *,
    reprocess: bool = False,
) -> None:
    """Upload a PyArrow Table as Snappy-compressed Parquet to storage.

    Retries up to 3 times with exponential backoff (1s, 2s, 4s, max 30s).
    After each write attempt, reads back the stored file metadata to verify
    the uploaded row count matches the local table. Raises on final failure.

    If reprocess=True, checks whether the path already exists and logs
    the existing row count before overwriting.

    Args:
        table: PyArrow Table to write as Parquet.
        backend: Storage backend (local or cloud) for put/get/exists.
        path: Relative path within the backend (e.g. raw/train_positions/date=2026-02-17/data.parquet).
        reprocess: If True, log existing row count before overwriting. Default False.

    Raises:
        RuntimeError: If all 3 upload attempts fail or row count mismatch after upload.

    """
    expected_rows = len(table)

    if reprocess and backend.exists(path):
        try:
            existing_data = backend.get(path)
            existing_meta = pq.read_metadata(io.BytesIO(existing_data))
            logger.warning(
                "Overwriting existing file at %s with %d rows (existing: %d rows)",
                path,
                expected_rows,
                existing_meta.num_rows,
            )
        except (FileNotFoundError, OSError):
            logger.debug("No existing file at %s; proceeding with fresh upload", path)

    for attempt in stamina.retry_context(
        on=Exception,
        attempts=3,
        wait_initial=1.0,
        wait_max=30.0,
        wait_exp_base=2,
        timeout=None,
    ):
        with attempt:
            logger.info(
                "Upload attempt %d: writing %d rows to %s",
                attempt.num,
                expected_rows,
                path,
            )
            buf = io.BytesIO()
            pq.write_table(table, buf, compression="snappy")
            data = buf.getvalue()
            backend.put(path, data)
            read_back = backend.get(path)
            meta = pq.read_metadata(io.BytesIO(read_back))
            if meta.num_rows != expected_rows:
                msg = (
                    f"Row count mismatch after upload to {path}: "
                    f"expected {expected_rows}, got {meta.num_rows}"
                )
                raise RuntimeError(msg)
            logger.info(
                "Upload verified: %d rows at %s (attempt %d)",
                expected_rows,
                path,
                attempt.num,
            )
