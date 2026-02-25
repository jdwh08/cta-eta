"""CTA compaction job: CLI entry point and daily orchestration.

Reads IPC journal files produced by the daemon's JournalWriter, merges them
into a single Snappy-compressed Parquet file per daemon per day, uploads to
cloud storage (fsspec-compatible URL), and archives source journals.

Usage:
    uv run python -m cta_eta.data_collection.compaction.compact
    uv run python -m cta_eta.data_collection.compaction.compact --reprocess 2026-02-17
    cta-compact  # installed entry point
    cta-compact --reprocess 2026-02-17
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from cta_eta.data_collection.compaction.archiver import archive_journals, prune_archive
from cta_eta.data_collection.compaction.ipc_reader import (
    discover_journals,
    read_ipc_with_repair,
)
from cta_eta.data_collection.compaction.schemas import (
    TRAIN_POSITION_SCHEMA,
    WEATHER_SCHEMA,
)
from cta_eta.data_collection.compaction.uploader import upload_parquet
from cta_eta.data_collection.config import load_config

_log = logging.getLogger(__name__)

# Maps dataset name to its expected schema
_DATASET_SCHEMAS: dict[str, pa.Schema] = {
    "train_positions": TRAIN_POSITION_SCHEMA,
    "weather": WEATHER_SCHEMA,
}


@dataclass
class CompactionMetrics:
    """Metrics for one daemon's compaction run."""

    date: str  # "2026-02-17"
    daemon: str  # "train_positions" or "weather"
    status: str  # "success" | "partial" | "failed"
    journals_found: int
    journals_repaired: int  # files where was_clean=False but had valid batches
    journals_skipped: int  # files yielding 0 batches (corrupt header)
    rows_written: int
    upload_bytes: int  # os.path.getsize of local Parquet before upload
    elapsed_seconds: float
    error: str | None = None


def _compact_one_daemon(
    daemon_name: str,
    target_date: date,
    config: dict[str, Any],
    *,
    reprocess: bool = False,
) -> CompactionMetrics:
    """Orchestrate compaction for a single daemon's journals on a given date.

    Steps:
    1. Discover journal files for the date.
    2. Read and repair each journal, collecting valid batches.
    3. Validate each table's schema against the expected schema.
    4. Concatenate tables and write a local staging Parquet file.
    5. Upload to cloud storage (with retry and row count verification).
    6. Archive journal files (ONLY after verified upload).
    7. Prune old archive directories.

    Args:
        daemon_name: "train_positions" or "weather".
        target_date: The date to compact journals for.
        config: Loaded config dict from load_config().
        reprocess: If True, pass reprocess=True to upload_parquet().

    Returns:
        CompactionMetrics with status="success", "partial", or "failed".

    Raises:
        Exception: Any unexpected error propagates up to main() for metrics capture.

    """
    t0 = time.monotonic()
    data_path = Path(config.get("storage", {}).get("data_path", "data"))
    compaction_cfg = config.get("compaction", {})
    cloud_url_base = str(compaction_cfg.get("cloud_url", ""))
    archive_base = Path(str(compaction_cfg.get("archive_path", "data/archive")))
    retention_days = int(compaction_cfg.get("journal_retention_days", 7))
    compaction_dir_base = Path(str(compaction_cfg.get("compaction_dir", "data/compaction")))

    expected_schema = _DATASET_SCHEMAS.get(daemon_name)
    date_str = target_date.isoformat()

    # Step 1: Discover journals
    journal_files = discover_journals(data_path, daemon_name, target_date)
    journals_found = len(journal_files)

    if not journal_files:
        _log.warning(
            "No journal files found for %s on %s — daemon may have been down",
            daemon_name,
            date_str,
        )
        return CompactionMetrics(
            date=date_str,
            daemon=daemon_name,
            status="partial",
            journals_found=0,
            journals_repaired=0,
            journals_skipped=0,
            rows_written=0,
            upload_bytes=0,
            elapsed_seconds=time.monotonic() - t0,
        )

    # Step 2: Read and repair each journal
    tables: list[pa.Table] = []
    journals_repaired = 0
    journals_skipped = 0

    for journal_path in journal_files:
        batches, was_clean = read_ipc_with_repair(journal_path)

        if not batches:
            journals_skipped += 1
            _log.warning("Skipping corrupt journal (0 batches): %s", journal_path)
            continue

        if not was_clean:
            journals_repaired += 1
            _log.warning(
                "Repaired partial journal (%d batches salvaged): %s",
                len(batches),
                journal_path,
            )

        table = pa.Table.from_batches(batches)

        # Step 3: Validate schema before concat (Pitfall 3 — silent type promotion)
        if expected_schema is not None and not table.schema.equals(expected_schema):
            _log.warning(
                "Schema mismatch in %s — skipping (expected %s, got %s)",
                journal_path.name,
                expected_schema,
                table.schema,
            )
            journals_skipped += 1
            continue

        tables.append(table)

    if not tables:
        _log.warning(
            "All journals were corrupt/skipped for %s on %s",
            daemon_name,
            date_str,
        )
        return CompactionMetrics(
            date=date_str,
            daemon=daemon_name,
            status="partial",
            journals_found=journals_found,
            journals_repaired=journals_repaired,
            journals_skipped=journals_skipped,
            rows_written=0,
            upload_bytes=0,
            elapsed_seconds=time.monotonic() - t0,
        )

    # Step 4: Concatenate and write local staging Parquet
    merged = pa.concat_tables(tables)
    rows_written = len(merged)

    local_staging_dir = compaction_dir_base / daemon_name / f"date={date_str}"
    local_staging_dir.mkdir(parents=True, exist_ok=True)
    local_parquet = local_staging_dir / "data.parquet"
    pq.write_table(merged, local_parquet, compression="snappy")
    upload_bytes = os.path.getsize(local_parquet)

    # Step 5: Upload to cloud storage (with retry and row count verification)
    cloud_url = f"{cloud_url_base}/{daemon_name}/date={date_str}/data.parquet"
    upload_parquet(merged, cloud_url, reprocess=reprocess)

    # Step 6: Archive journals ONLY after verified upload
    archive_journals(journal_files, archive_base / daemon_name, target_date)

    # Step 7: Prune old archives
    pruned = prune_archive(archive_base / daemon_name, retention_days)
    if pruned:
        _log.info("Pruned %d old archive directories for %s", len(pruned), daemon_name)

    return CompactionMetrics(
        date=date_str,
        daemon=daemon_name,
        status="success",
        journals_found=journals_found,
        journals_repaired=journals_repaired,
        journals_skipped=journals_skipped,
        rows_written=rows_written,
        upload_bytes=upload_bytes,
        elapsed_seconds=time.monotonic() - t0,
    )


def _write_sidecar(metrics: CompactionMetrics, compaction_dir: Path) -> None:
    """Write JSON sidecar metrics file for cta-monitor to read.

    Always called in a finally block so the sidecar exists even on failure.

    Args:
        metrics: CompactionMetrics instance to serialize.
        compaction_dir: Directory to write the sidecar JSON file.

    """
    sidecar_path = compaction_dir / f"compaction-{metrics.date}-{metrics.daemon}.json"
    try:
        sidecar_path.write_text(json.dumps(asdict(metrics), indent=2))
        _log.debug("Wrote compaction sidecar to %s", sidecar_path)
    except OSError as exc:
        _log.warning("Failed to write compaction sidecar %s: %s", sidecar_path, exc)


def send_compaction_alert(metrics: CompactionMetrics, config: dict[str, Any]) -> None:
    """Send an email alert for a failed compaction run via Phase 9 alerting.

    Uses config["alerting"] section. If the section is absent or not a dict,
    logs a warning and returns without raising (graceful degradation — alerting
    should not crash the compaction job).

    Args:
        metrics: Failed CompactionMetrics to include in the alert body.
        config: Loaded config dict from load_config().

    """
    from cta_eta.monitoring.alerting import send_email_alert
    from cta_eta.monitoring.run_alerts import _build_email_config  # noqa: PLC2701

    alerting_cfg = config.get("alerting", {})
    if not alerting_cfg or not isinstance(alerting_cfg, dict):
        _log.warning(
            "No [alerting] section in config — compaction failure alert skipped "
            "(daemon=%s, date=%s)",
            metrics.daemon,
            metrics.date,
        )
        return

    email_config = _build_email_config(alerting_cfg)
    subject = f"CTA Compaction Failed: {metrics.daemon} {metrics.date}"
    body = (
        f"Compaction failed for daemon '{metrics.daemon}' on {metrics.date}.\n\n"
        f"Error: {metrics.error or 'unknown'}\n"
        f"Journals skipped (corrupt): {metrics.journals_skipped}\n"
        f"Journals found: {metrics.journals_found}\n"
        f"Status: {metrics.status}\n"
    )
    sent = send_email_alert(email_config, subject, body)
    if not sent:
        _log.warning(
            "Failed to send compaction alert email for %s %s",
            metrics.daemon,
            metrics.date,
        )


def main(argv: list[str] | None = None) -> None:
    """Run the daily compaction job for all daemon datasets.

    Args:
        argv: Argument list for CLI parsing. Defaults to sys.argv[1:] if None.

    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(
        prog="cta-compact",
        description="Compact IPC journal files into daily Parquet for cloud upload.",
    )
    parser.add_argument(
        "--reprocess",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Re-compact a specific past date (default: yesterday).",
    )
    args = parser.parse_args(argv)

    config = load_config()
    reprocess_date: date | None = args.reprocess
    target_date = reprocess_date or (date.today() - timedelta(days=1))
    is_reprocess = reprocess_date is not None

    if is_reprocess:
        _log.info("Reprocessing date: %s", target_date.isoformat())
    else:
        _log.info("Compacting date: %s", target_date.isoformat())

    datasets = ["train_positions", "weather"]
    compaction_dir = Path(
        str(config.get("compaction", {}).get("compaction_dir", "data/compaction"))
    )
    compaction_dir.mkdir(parents=True, exist_ok=True)

    for daemon_name in datasets:
        t0 = time.monotonic()
        metrics: CompactionMetrics | None = None
        try:
            metrics = _compact_one_daemon(
                daemon_name, target_date, config, reprocess=is_reprocess
            )
            metrics.elapsed_seconds = time.monotonic() - t0
            _log.info(
                "Compaction %s for %s: status=%s rows=%d elapsed=%.1fs",
                daemon_name,
                target_date.isoformat(),
                metrics.status,
                metrics.rows_written,
                metrics.elapsed_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            _log.error(
                "Compaction failed for %s on %s: %s",
                daemon_name,
                target_date.isoformat(),
                exc,
                exc_info=True,
            )
            metrics = CompactionMetrics(
                date=target_date.isoformat(),
                daemon=daemon_name,
                status="failed",
                journals_found=0,
                journals_repaired=0,
                journals_skipped=0,
                rows_written=0,
                upload_bytes=0,
                elapsed_seconds=time.monotonic() - t0,
                error=str(exc),
            )
            send_compaction_alert(metrics, config)
        finally:
            if metrics is not None:
                _write_sidecar(metrics, compaction_dir)


if __name__ == "__main__":
    main()
