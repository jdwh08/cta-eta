"""Cloud uploader for compacted Parquet files.

Provides upload_parquet() with stamina retry (3 attempts, exponential backoff)
and post-upload row count verification via Parquet metadata. Uses fsspec +
pyarrow filesystem bridge for pluggable cloud targets (S3, GCS, local).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import fsspec
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import stamina

if TYPE_CHECKING:
    import pyarrow as pa

_log = logging.getLogger(__name__)


def make_pyarrow_fs(cloud_url: str) -> tuple[pafs.PyFileSystem, str]:
    """Build a pyarrow-compatible filesystem from a cloud URL.

    Wraps fsspec.url_to_fs() with pyarrow's FSSpecHandler bridge so that
    pq.write_table() and pq.read_metadata() can operate on any fsspec-
    compatible target (S3, GCS, local, etc.).

    Args:
        cloud_url: Full URL to the remote file, e.g.:
            's3://my-bucket/raw/train_positions/date=2026-02-17/data.parquet'
            'gs://my-bucket/raw/weather/date=2026-02-17/data.parquet'
            '/local/path/data.parquet'

    Returns:
        A tuple of (PyFileSystem, path_without_scheme) suitable for passing
        to pq.write_table(..., filesystem=pa_fs) and pq.read_metadata(path, ...).

    """
    fs, path = fsspec.url_to_fs(cloud_url)
    pa_fs = pafs.PyFileSystem(pafs.FSSpecHandler(fs))
    return pa_fs, path


def upload_parquet(
    table: pa.Table,
    cloud_url: str,
    *,
    reprocess: bool = False,
) -> None:
    """Upload a PyArrow Table as Snappy-compressed Parquet to cloud storage.

    Retries up to 3 times with exponential backoff (1s, 2s, 4s, max 30s).
    After each write attempt, reads back the remote file metadata to verify
    the uploaded row count matches the local table. Raises on final failure.

    If reprocess=True, checks whether the remote file already exists and logs
    the existing row count before overwriting.

    Args:
        table: PyArrow Table to write as Parquet.
        cloud_url: Full URL to the destination file (s3://, gs://, or local path).
        reprocess: If True, log existing row count before overwriting. Default False.

    Raises:
        RuntimeError: If all 3 upload attempts fail (stamina re-raises the last
            exception; callers should catch and trigger an alert).

    """
    pa_fs, path = make_pyarrow_fs(cloud_url)
    expected_rows = len(table)

    if reprocess:
        try:
            existing_meta = pq.read_metadata(path, filesystem=pa_fs)
            _log.warning(
                "Overwriting existing file at %s with %d rows (existing: %d rows)",
                path,
                expected_rows,
                existing_meta.num_rows,
            )
        except FileNotFoundError:
            _log.debug("No existing file at %s; proceeding with fresh upload", path)

    for attempt in stamina.retry_context(
        on=Exception,
        attempts=3,
        wait_initial=1.0,
        wait_max=30.0,
        wait_exp_base=2,
        timeout=None,
    ):
        with attempt:
            _log.info(
                "Upload attempt %d: writing %d rows to %s",
                attempt.num,
                expected_rows,
                path,
            )
            pq.write_table(table, path, filesystem=pa_fs, compression="snappy")
            # Verify by reading remote metadata only (no column data transferred)
            meta = pq.read_metadata(path, filesystem=pa_fs)
            if meta.num_rows != expected_rows:
                msg = (
                    f"Row count mismatch after upload to {path}: "
                    f"expected {expected_rows}, got {meta.num_rows}"
                )
                raise RuntimeError(msg)
            _log.info(
                "Upload verified: %d rows at %s (attempt %d)",
                expected_rows,
                path,
                attempt.num,
            )
