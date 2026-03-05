"""CTA compaction job: CLI entry point and daily orchestration.

Reads IPC journal files produced by the daemon's JournalWriter, merges them
into a single Snappy-compressed Parquet file per daemon per day, uploads to
the configured storage backend, and archives source journals.

Usage:
    uv run python -m cta_eta.data_collection.compaction.compact
    cta-compact                           # run compaction (default, backward compat)
    cta-compact run --reprocess 2026-02-17  # explicit reprocess (replaces old --reprocess)
    cta-compact schema update             # promote observed schema to registry
    cta-compact schema update --daemon train_positions
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pyarrow as pa
import pyarrow.parquet as pq

from cta_eta.data_collection.compaction.archiver import archive_journals, prune_archive
from cta_eta.data_collection.compaction.ipc_reader import (
    discover_journals,
    read_ipc_with_repair,
)
from cta_eta.data_collection.compaction.schema_registry import (
    bootstrap_registry,
    classify_drift,
    load_registry,
    save_registry,
)
from cta_eta.data_collection.compaction.schemas import (
    TRAIN_POSITION_SCHEMA,
    WEATHER_SCHEMA,
)
from cta_eta.data_collection.compaction.uploader import upload_parquet
from cta_eta.data_collection.config import get_project_root, load_config
from cta_eta.data_collection.storage_cache.storage import create_storage_backend
from cta_eta.monitoring.alerting import send_email_alert
from cta_eta.monitoring.run_alerts import _build_email_config

if TYPE_CHECKING:
    from cta_eta.data_collection.compaction.schema_registry import DriftResult

logger = logging.getLogger(__name__)

# Maps dataset name to its expected schema
_DATASET_SCHEMAS: dict[str, pa.Schema] = {
    "train_positions": TRAIN_POSITION_SCHEMA,
    "weather": WEATHER_SCHEMA,
}

# Registry files live in src/cta_eta/schemas/
# parents[0]=compaction/, parents[1]=data_collection/
_REGISTRY_DIR = Path(__file__).resolve().parents[1] / "schemas"


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


def send_drift_alert(
    drift_result: DriftResult,
    daemon_name: str,
    date_str: str,
    config: dict[str, Any],
) -> None:
    """Send an email alert for breaking schema drift via email alerting.

    Uses config["alerting"] section. If the section is absent or not a dict,
    logs a warning and returns without raising (graceful degradation — alerting
    should not crash the compaction job).

    Args:
        drift_result: DriftResult with breaking_fields, removed_fields, nullability_changes.
        daemon_name: Daemon dataset name (e.g. "train_positions").
        date_str: ISO date string (e.g. "2026-02-17").
        config: Loaded config dict from load_config().

    """
    alerting_cfg = config.get("alerting", {})
    if not alerting_cfg or not isinstance(alerting_cfg, dict):
        logger.warning(
            "No [alerting] section in config — drift alert skipped "
            "(daemon=%s, date=%s)",
            daemon_name,
            date_str,
        )
        return

    email_config = _build_email_config(alerting_cfg)
    subject = f"CTA Schema Drift Detected: {daemon_name} {date_str}"
    body_parts = [
        f"Breaking schema drift detected for daemon '{daemon_name}' on {date_str}.\n\n"
    ]
    body_parts.extend(
        f"  CHANGED: {f.name}  {f.old_type} \u2192 {f.new_type}\n"
        for f in drift_result.breaking_fields
    )
    body_parts.extend(f"  REMOVED: {name}\n" for name in drift_result.removed_fields)
    body_parts.extend(
        f"  NULLABILITY: {f.name}  nullable={f.old_nullable} \u2192 nullable={f.new_nullable}\n"
        for f in drift_result.nullability_changes
    )
    body_parts.append(
        "\nNote: Compaction continued. The merged Parquet file is annotated with schema_drift=true.\n"
    )
    body_parts.append(
        "To resolve: review the drift, then run: cta-compact schema update\n"
    )
    body = "".join(body_parts)

    sent = send_email_alert(email_config, subject, body)
    if not sent:
        logger.warning(
            "Failed to send drift alert email for %s %s",
            daemon_name,
            date_str,
        )


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
    project_root = get_project_root(config)
    immediate = config.get("storage", {}).get("immediate", {})
    if not isinstance(immediate, dict):
        immediate = {}
    data_path_cfg = Path(str(immediate.get("data_path", "data/journals")))
    data_path = (
        data_path_cfg if data_path_cfg.is_absolute() else project_root / data_path_cfg
    )
    compaction_cfg = config.get("storage", {}).get("compaction", {})
    if not isinstance(compaction_cfg, dict):
        compaction_cfg = {}
    archive_cfg = Path(str(compaction_cfg.get("archive_path", "data/archive")))
    archive_base = (
        archive_cfg if archive_cfg.is_absolute() else project_root / archive_cfg
    )
    retention_days = int(compaction_cfg.get("journal_retention_days", 7))
    compaction_dir_cfg = Path(
        str(compaction_cfg.get("staging_path", "data/compaction"))
    )
    compaction_dir_base = (
        compaction_dir_cfg
        if compaction_dir_cfg.is_absolute()
        else project_root / compaction_dir_cfg
    )

    expected_schema = _DATASET_SCHEMAS.get(daemon_name)
    date_str = target_date.isoformat()

    # Step 1: Discover journals
    journal_files = discover_journals(data_path, daemon_name, target_date)
    journals_found = len(journal_files)

    # Load registry schema if available (used for drift-aware validation)
    registry_path = _REGISTRY_DIR / f"{daemon_name}.json"
    registry_schema = load_registry(registry_path)  # None if no registry yet

    if not journal_files:
        logger.warning(
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

    # Step 2: Read, repair, and validate journals
    check_schema = registry_schema if registry_schema is not None else expected_schema
    (
        tables,
        journals_repaired,
        journals_skipped,
        day_has_drift,
        day_drift_result,
    ) = _read_and_validate_journals(
        journal_files, daemon_name, date_str, config, check_schema
    )

    if not tables:
        logger.warning(
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

    # Step 4: Concatenate and write local staging Parquet (with drift metadata)
    merged, rows_written, upload_bytes = _write_merged_parquet_with_drift(
        tables,
        compaction_dir_base,
        daemon_name,
        date_str,
        day_has_drift,
        day_drift_result,
    )

    # Step 5: Upload to storage backend (with retry and row count verification)
    # If upload fails after all retries: return failed metrics WITHOUT archiving
    # journals. Journals stay in place for the next run (two-phase safety invariant).
    backend = create_storage_backend(config)
    upload_prefix = str(compaction_cfg.get("upload_prefix", "raw"))
    upload_path = f"{upload_prefix}/{daemon_name}/date={date_str}/data.parquet"
    try:
        upload_parquet(merged, backend, upload_path, reprocess=reprocess)
    except Exception as upload_exc:
        logger.exception(
            "Upload failed for %s on %s after all retries",
            daemon_name,
            date_str,
        )
        failed_metrics = CompactionMetrics(
            date=date_str,
            daemon=daemon_name,
            status="failed",
            journals_found=journals_found,
            journals_repaired=journals_repaired,
            journals_skipped=journals_skipped,
            rows_written=rows_written,
            upload_bytes=upload_bytes,
            elapsed_seconds=time.monotonic() - t0,
            error=str(upload_exc),
        )
        send_compaction_alert(failed_metrics, config)
        return failed_metrics

    # Bootstrap registry on first successful compaction (no-op if already exists)
    bootstrap_registry(registry_path, merged.schema, daemon_name)

    # Step 6: Archive journals ONLY after verified upload
    archive_journals(journal_files, archive_base / daemon_name, target_date)

    # Step 7: Prune old archives
    pruned = prune_archive(archive_base / daemon_name, retention_days)
    if pruned:
        logger.info(
            "Pruned %d old archive directories for %s", len(pruned), daemon_name
        )

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


def _read_and_validate_journals(  # noqa: C901
    journal_files: list[Path],
    daemon_name: str,
    date_str: str,
    config: dict[str, Any],
    check_schema: pa.Schema | None,
) -> tuple[list[pa.Table], int, int, bool, DriftResult | None]:
    """Read IPC journals, repair, and perform drift-aware schema validation."""
    tables: list[pa.Table] = []
    journals_repaired = 0
    journals_skipped = 0

    drift_alert_sent = False
    day_has_drift = False
    day_drift_result: DriftResult | None = None

    for journal_path in journal_files:
        batches, was_clean = read_ipc_with_repair(journal_path)

        if not batches:
            journals_skipped += 1
            logger.warning("Skipping corrupt journal (0 batches): %s", journal_path)
            continue

        if not was_clean:
            journals_repaired += 1
            logger.warning(
                "Repaired partial journal (%d batches salvaged): %s",
                len(batches),
                journal_path,
            )

        table = pa.Table.from_batches(batches)

        if check_schema is None:
            tables.append(table)
            continue

        drift = classify_drift(check_schema, table.schema)
        if drift.drift_type == "breaking":
            if not drift_alert_sent:  # only alert on first breaking journal
                send_drift_alert(drift, daemon_name, date_str, config)
                drift_alert_sent = True
                logger.warning(
                    "Breaking schema drift in %s (first occurrence for %s %s)",
                    journal_path.name,
                    daemon_name,
                    date_str,
                )
            day_has_drift = True
            day_drift_result = drift  # keep last drift for annotation
            # Cast breaking columns to registry/expected type before concat
            for breaking_field in drift.breaking_fields:
                if breaking_field.name not in table.schema.names:
                    continue

                col_idx = table.schema.get_field_index(breaking_field.name)
                registry_field = check_schema.field(breaking_field.name)
                try:
                    casted_col = table.column(col_idx).cast(registry_field.type)
                    table = table.set_column(col_idx, breaking_field.name, casted_col)
                except pa.ArrowInvalid:
                    logger.warning(
                        "Cannot cast %s from %s to %s — keeping as-is",
                        breaking_field.name,
                        breaking_field.new_type,
                        breaking_field.old_type,
                    )
        elif drift.drift_type == "additive":
            logger.info(
                "Additive schema drift in %s (new fields: %s)",
                journal_path.name,
                [f.name for f in drift.added_fields],
            )
        # In all drift cases: still append table (continue-on-drift policy)
        tables.append(table)

    return tables, journals_repaired, journals_skipped, day_has_drift, day_drift_result


def _write_merged_parquet_with_drift(  # noqa: PLR0913
    tables: list[pa.Table],
    compaction_dir_base: Path,
    daemon_name: str,
    date_str: str,
    day_has_drift: bool,
    day_drift_result: DriftResult | None,
) -> tuple[pa.Table, int, int]:
    """Concatenate tables, write local Parquet, and annotate drift metadata."""
    # promote_options="default" fills new fields with null for additive drift tables
    merged = pa.concat_tables(tables, promote_options="default")
    rows_written = len(merged)

    local_staging_dir = compaction_dir_base / daemon_name / f"date={date_str}"
    local_staging_dir.mkdir(parents=True, exist_ok=True)
    local_parquet = local_staging_dir / "data.parquet"
    pq.write_table(merged, local_parquet, compression="snappy")
    upload_bytes = local_parquet.stat().st_size

    # Annotate Parquet with drift metadata if any breaking drift occurred
    if day_has_drift and day_drift_result is not None:
        existing_meta = merged.schema.metadata or {}
        breaking = [
            {"name": f.name, "old_type": f.old_type, "new_type": f.new_type}
            for f in day_drift_result.breaking_fields
        ]
        removed = list(day_drift_result.removed_fields)
        nullability = [
            {
                "name": f.name,
                "old_nullable": f.old_nullable,
                "new_nullable": f.new_nullable,
            }
            for f in day_drift_result.nullability_changes
        ]
        drift_summary = json.dumps(
            {
                "breaking_fields": breaking,
                "removed_fields": removed,
                "nullability_changes": nullability,
            }
        )
        merged = merged.replace_schema_metadata(
            {
                **existing_meta,
                b"schema_drift": b"true",
                b"drift_summary": drift_summary.encode(),
            }
        )
        # Rewrite local Parquet with drift annotation
        pq.write_table(merged, local_parquet, compression="snappy")
        upload_bytes = local_parquet.stat().st_size  # update after rewrite

    return merged, rows_written, upload_bytes


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
        logger.debug("Wrote compaction sidecar to %s", sidecar_path)
    except OSError as exc:
        logger.warning("Failed to write compaction sidecar %s: %s", sidecar_path, exc)


def send_compaction_alert(metrics: CompactionMetrics, config: dict[str, Any]) -> None:
    """Send an email alert for a failed compaction run via Phase 9 alerting.

    Uses config["alerting"] section. If the section is absent or not a dict,
    logs a warning and returns without raising (graceful degradation — alerting
    should not crash the compaction job).

    Args:
        metrics: Failed CompactionMetrics to include in the alert body.
        config: Loaded config dict from load_config().

    """
    alerting_cfg = config.get("alerting", {})
    if not alerting_cfg or not isinstance(alerting_cfg, dict):
        logger.warning(
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
        logger.warning(
            "Failed to send compaction alert email for %s %s",
            metrics.daemon,
            metrics.date,
        )


def cmd_schema_update(args: argparse.Namespace) -> None:
    """Promote latest observed Parquet schema to the registry.

    Finds the most recent compacted Parquet, reads its schema, overwrites the
    registry file for the given daemon, and attempts a git commit.

    Args:
        args: Parsed CLI args. Uses args.daemon (default: all daemons).

    """
    config = load_config()
    compaction_cfg = config.get("storage", {}).get("compaction", {})
    if not isinstance(compaction_cfg, dict):
        compaction_cfg = {}
    compaction_dir = Path(str(compaction_cfg.get("staging_path", "data/compaction")))

    daemon_names = (
        ["train_positions", "weather"] if args.daemon == "all" else [args.daemon]
    )

    for daemon_name in daemon_names:
        # Find most recent local Parquet for this daemon
        daemon_dir = compaction_dir / daemon_name
        if not daemon_dir.exists():
            print(f"No compaction data found for {daemon_name} in {daemon_dir}")  # noqa: T201
            continue

        # Sort date=YYYY-MM-DD dirs descending, take first with a data.parquet
        date_dirs = sorted(daemon_dir.glob("date=*"), reverse=True)
        latest_parquet = None
        for date_dir in date_dirs:
            candidate = date_dir / "data.parquet"
            if candidate.exists():
                latest_parquet = candidate
                break

        if latest_parquet is None:
            print(f"No Parquet file found for {daemon_name}")  # noqa: T201
            continue

        # Read schema from latest Parquet
        schema = pq.read_schema(latest_parquet)
        registry_path = _REGISTRY_DIR / f"{daemon_name}.json"
        save_registry(registry_path, schema, daemon_name)
        print(f"Registry updated: {registry_path}")  # noqa: T201


def main(argv: list[str] | None = None) -> None:
    """Run the daily compaction job for all daemon datasets.

    Args:
        argv: Argument list for CLI parsing. Defaults to sys.argv[1:] if None.

    Subcommands:
        (no subcommand): Run daily compaction. Backward compatible with systemd service.
        run [--reprocess YYYY-MM-DD]: Explicitly run compaction. Replaces old top-level
            --reprocess flag. NOTE: 'cta-compact --reprocess DATE' no longer works;
            use 'cta-compact run --reprocess DATE' for manual reprocessing.
        schema update [--daemon DAEMON]: Promote observed schema to registry.

    """
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    parser = argparse.ArgumentParser(
        prog="cta-compact",
        description="Compact IPC journal files into daily Parquet for cloud upload.",
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    # "run" subcommand (explicit, replaces old top-level --reprocess)
    # NOTE: 'cta-compact --reprocess DATE' no longer works; use 'cta-compact run --reprocess DATE'
    run_parser = subparsers.add_parser(
        "run",
        help="Run daily compaction (default when no subcommand given)",
    )
    run_parser.add_argument(
        "--reprocess",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Re-compact a specific past date (default: yesterday).",
    )

    # "schema" subcommand
    schema_parser = subparsers.add_parser(
        "schema",
        help="Schema registry management",
    )
    schema_sub = schema_parser.add_subparsers(dest="schema_command")
    update_parser = schema_sub.add_parser(
        "update",
        help="Promote observed schema to registry",
    )
    update_parser.add_argument(
        "--daemon",
        default="all",
        choices=["all", "train_positions", "weather"],
        help="Daemon to update (default: all)",
    )
    update_parser.set_defaults(func=cmd_schema_update)

    args = parser.parse_args(argv)

    if args.subcommand == "schema":
        if not hasattr(args, "func"):
            schema_parser.print_help()
            return
        args.func(args)
        return

    # Default behavior: no subcommand or "run" subcommand → run compaction
    # (systemd service uses 'cta-compact' with no args — backward compatible)
    config = load_config()
    project_root = get_project_root(config)
    reprocess_date: date | None = getattr(args, "reprocess", None)
    target_date = reprocess_date or (datetime.now(tz=UTC) - timedelta(days=1)).date()
    is_reprocess = reprocess_date is not None

    if is_reprocess:
        logger.info("Reprocessing date: %s", target_date.isoformat())
    else:
        logger.info("Compacting date: %s", target_date.isoformat())

    datasets = ["train_positions", "weather"]
    compaction_cfg = config.get("storage", {}).get("compaction", {})
    if not isinstance(compaction_cfg, dict):
        compaction_cfg = {}
    compaction_dir_cfg = Path(
        str(compaction_cfg.get("staging_path", "data/compaction"))
    )
    compaction_dir = (
        compaction_dir_cfg
        if compaction_dir_cfg.is_absolute()
        else project_root / compaction_dir_cfg
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
            logger.info(
                "Compaction %s for %s: status=%s rows=%d elapsed=%.1fs",
                daemon_name,
                target_date.isoformat(),
                metrics.status,
                metrics.rows_written,
                metrics.elapsed_seconds,
            )
        except Exception as exc:
            logger.exception(
                "Compaction failed for %s on %s",
                daemon_name,
                target_date.isoformat(),
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
