"""Journal file archival and retention pruning.

Provides two functions:
- archive_journals: move compacted IPC journal files to a date-partitioned
  archive directory after verified cloud upload.
- prune_archive: delete archive directories older than the configured
  retention window.
"""

from __future__ import annotations

import logging
import shutil
from datetime import date, timedelta
from pathlib import Path

_log = logging.getLogger(__name__)


def archive_journals(
    journal_files: list[Path],
    archive_base: Path,
    target_date: date,
) -> None:
    """Move compacted journal files to a date-partitioned archive directory.

    Called ONLY after the cloud upload has been verified (row count matches).
    Creates the archive subdirectory if it does not exist.

    Args:
        journal_files: List of IPC journal Paths to archive. May be empty
            (no-op if daemon was down for the day).
        archive_base: Root archive directory (e.g. Path("data/archive")).
        target_date: The compaction target date; used to build the hive-style
            subdirectory name: ``date=YYYY-MM-DD``.

    """
    archive_dir = archive_base / f"date={target_date.isoformat()}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    for journal_file in journal_files:
        dest = archive_dir / journal_file.name
        shutil.move(str(journal_file), dest)
    _log.info(
        "Archived %d journal file(s) to %s",
        len(journal_files),
        archive_dir,
    )


def prune_archive(archive_base: Path, retention_days: int = 7) -> list[Path]:
    """Delete archive directories older than the configured retention window.

    Globs ``archive_base / "date=*"`` directories and removes any whose
    date is strictly before ``date.today() - retention_days``.

    Silently skips directories with unparseable names (ValueError) and
    suppresses OSError on deletion failures (logs at WARNING level).

    Args:
        archive_base: Root archive directory to prune.
        retention_days: Number of days to retain archived journals.
            Directories older than this threshold are deleted. Default 7.

    Returns:
        List of archive directory Paths that were successfully pruned.

    """
    cutoff = date.today() - timedelta(days=retention_days)
    pruned: list[Path] = []

    for archive_dir in sorted(archive_base.glob("date=*")):
        dir_name = archive_dir.name
        try:
            dir_date = date.fromisoformat(dir_name.removeprefix("date="))
        except ValueError:
            _log.debug("Skipping archive dir with unparseable name: %s", dir_name)
            continue

        if dir_date < cutoff:
            try:
                shutil.rmtree(archive_dir)
                pruned.append(archive_dir)
                _log.info(
                    "Pruned archive directory %s (older than %d days)",
                    archive_dir,
                    retention_days,
                )
            except OSError as exc:
                _log.warning(
                    "Failed to prune archive directory %s: %s", archive_dir, exc
                )

    return pruned
