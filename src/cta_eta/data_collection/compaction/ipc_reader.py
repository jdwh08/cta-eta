"""IPC journal file discovery and partial-repair reader.

Provides two functions:
- discover_journals: find all journal_*.ipc files for a given date under the
  hive-style path structure produced by JournalWriter.
- read_ipc_with_repair: read an IPC stream file batch-by-batch, salvaging
  all valid batches even when the file is truncated or has corrupt trailing bytes.

Path structure matches JournalWriter._open_new_journal():
    {data_path}/{dataset_name}/year=YYYY/month=MM/day=DD/journal_HHMMSS_usec.ipc
"""

from datetime import date
from pathlib import Path

import pyarrow as pa
from pyarrow import ipc


def discover_journals(
    data_path: Path, dataset_name: str, target_date: date
) -> list[Path]:
    """Discover all IPC journal files for a given date.

    Searches for journal_*.ipc files under the hive-style partition directory
    produced by JournalWriter:
        {data_path}/{dataset_name}/year=YYYY/month=MM/day=DD/

    Args:
        data_path: Base directory where journal files are written.
        dataset_name: Dataset subdirectory name (e.g. "train_positions", "weather").
        target_date: The date to search journals for.

    Returns:
        Sorted list of journal file Paths. Returns empty list if the directory
        does not exist or contains no matching files.

    """
    day_dir = (
        data_path
        / dataset_name
        / f"year={target_date.year}"
        / f"month={target_date.month:02d}"
        / f"day={target_date.day:02d}"
    )
    if not day_dir.exists():
        return []
    return sorted(day_dir.glob("journal_*.ipc"))


def read_ipc_with_repair(path: Path) -> tuple[list[pa.RecordBatch], bool]:
    """Read an IPC stream file, salvaging valid batches on corruption.

    Reads batches one at a time, catching ArrowInvalid/OSError to recover all
    batches written before any corruption. Handles five cases:

    1. Normal closed file (EOS marker present): returns (all batches, True)
    2. Crash file (missing close(), no EOS): returns (all batches, True)
       — pyarrow 22.0.0 raises StopIteration (not ArrowInvalid) for this case
    3. Corrupt trailing bytes: returns (valid batches before corruption, False)
    4. Corrupt header (schema unreadable): returns ([], False)
    5. Empty file: returns ([], False)

    Args:
        path: Path to the IPC stream file to read.

    Returns:
        A tuple of (batches, was_clean) where:
        - batches: All valid RecordBatch objects read before any error.
        - was_clean: True if read completed without ArrowInvalid (StopIteration
          from missing EOS marker is treated as clean — same as normal close).

    """
    try:
        reader = ipc.open_stream(path)
    except pa.lib.ArrowInvalid:
        # Header or schema corrupt — cannot read any batches
        return [], False

    batches: list[pa.RecordBatch] = []
    while True:
        try:
            batch = reader.read_next_batch()
            batches.append(batch)
        except StopIteration:
            # Clean termination: EOS marker found OR graceful missing-EOS
            # (verified pyarrow 22.0.0 behavior for daemon crash files)
            return batches, True
        except (pa.lib.ArrowInvalid, OSError):
            # Corruption detected at batch boundary — salvage what was read.
            # pyarrow raises ArrowInvalid for metadata corruption and OSError
            # for body read failures (both indicate truncated/corrupt data).
            return batches, False
