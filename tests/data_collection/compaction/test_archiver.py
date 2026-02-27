"""Unit tests for archiver: journal archival and retention pruning.

All tests use tmp_path for filesystem state (atomic, no side effects).
date.today() is mocked in prune_archive tests for deterministic cutoff.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

### OWN MODULES
from cta_eta.data_collection.compaction.archiver import archive_journals, prune_archive

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


class TestArchiveJournals:
    """Tests for archive_journals: moving journal files to date-partitioned archive."""

    def test_empty_journal_list_creates_archive_dir_only(self, tmp_path: Path) -> None:
        """Empty list creates archive subdir and does nothing (no-op for the day)."""
        archive_base = tmp_path / "archive"
        target = date(2026, 2, 17)

        archive_journals([], archive_base, target)

        archive_dir = archive_base / "date=2026-02-17"
        assert archive_dir.is_dir()
        assert list(archive_dir.iterdir()) == []

    def test_single_journal_file_moved_into_archive(self, tmp_path: Path) -> None:
        """One journal file is moved to archive_base/date=YYYY-MM-DD/."""
        archive_base = tmp_path / "archive"
        journal = tmp_path / "journal_120000_000001.ipc"
        journal.write_bytes(b"fake ipc")
        target = date(2026, 2, 17)

        archive_journals([journal], archive_base, target)

        archive_dir = archive_base / "date=2026-02-17"
        dest = archive_dir / "journal_120000_000001.ipc"
        assert dest.is_file()
        assert dest.read_bytes() == b"fake ipc"
        assert not journal.exists()

    def test_multiple_journal_files_all_moved(self, tmp_path: Path) -> None:
        """Multiple journal files are all moved under the same date partition."""
        archive_base = tmp_path / "archive"
        journals = [
            tmp_path / "journal_120000_000001.ipc",
            tmp_path / "journal_120000_000002.ipc",
        ]
        for i, p in enumerate(journals):
            p.write_bytes(f"batch{i}".encode())
        target = date(2026, 2, 18)

        archive_journals(journals, archive_base, target)

        archive_dir = archive_base / "date=2026-02-18"
        assert archive_dir.is_dir()
        for i, name in enumerate(
            ["journal_120000_000001.ipc", "journal_120000_000002.ipc"]
        ):
            dest = archive_dir / name
            assert dest.is_file()
            assert dest.read_bytes() == f"batch{i}".encode()
        for p in journals:
            assert not p.exists()

    def test_archive_base_parents_created(self, tmp_path: Path) -> None:
        """archive_base can be a nested path; parents are created."""
        archive_base = tmp_path / "a" / "b" / "archive"
        journal = tmp_path / "single.ipc"
        journal.write_bytes(b"x")
        target = date(2026, 2, 19)

        archive_journals([journal], archive_base, target)

        assert (archive_base / "date=2026-02-19" / "single.ipc").is_file()

    def test_dest_uses_journal_name_only(self, tmp_path: Path) -> None:
        """Destination is archive_dir / journal_file.name (path prefix discarded)."""
        archive_base = tmp_path / "archive"
        nested = tmp_path / "daemon" / "year=2026" / "month=02" / "day=17"
        nested.mkdir(parents=True)
        journal = nested / "journal_120000_000001.ipc"
        journal.write_bytes(b"nested")
        target = date(2026, 2, 17)

        archive_journals([journal], archive_base, target)

        dest = archive_base / "date=2026-02-17" / "journal_120000_000001.ipc"
        assert dest.is_file()
        assert dest.read_bytes() == b"nested"


class TestPruneArchive:
    """Tests for prune_archive: deleting archive dirs older than retention."""

    def test_empty_archive_base_returns_empty_list(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """When no date=* dirs exist, returns []."""
        archive_base = tmp_path / "archive"
        archive_base.mkdir(parents=True)

        mock_date = mocker.patch("cta_eta.data_collection.compaction.archiver.date")
        mock_date.today.return_value = date(2026, 2, 25)
        mock_date.fromisoformat = date.fromisoformat

        result = prune_archive(archive_base, retention_days=7)

        assert result == []

    def test_dirs_within_retention_not_pruned(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Directories with date >= cutoff are left in place."""
        archive_base = tmp_path / "archive"
        archive_base.mkdir(parents=True)
        (archive_base / "date=2026-02-20").mkdir()
        (archive_base / "date=2026-02-24").mkdir()

        mock_d = mocker.patch("cta_eta.data_collection.compaction.archiver.date")
        mock_d.today.return_value = date(2026, 2, 25)
        mock_d.fromisoformat = date.fromisoformat

        result = prune_archive(archive_base, retention_days=7)

        assert result == []
        assert (archive_base / "date=2026-02-20").is_dir()
        assert (archive_base / "date=2026-02-24").is_dir()

    def test_dirs_older_than_retention_pruned(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Directories with date < cutoff are removed and returned."""
        archive_base = tmp_path / "archive"
        archive_base.mkdir(parents=True)
        old1 = archive_base / "date=2026-02-10"
        old2 = archive_base / "date=2026-02-15"
        old1.mkdir()
        old2.mkdir()

        mock_d = mocker.patch("cta_eta.data_collection.compaction.archiver.date")
        mock_d.today.return_value = date(2026, 2, 25)
        mock_d.fromisoformat = date.fromisoformat

        result = prune_archive(archive_base, retention_days=7)

        assert set(result) == {old1, old2}
        assert not old1.exists()
        assert not old2.exists()

    def test_cutoff_boundary_dir_not_pruned(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Dir with date exactly equal to cutoff (today - retention_days) is not pruned."""
        archive_base = tmp_path / "archive"
        archive_base.mkdir(parents=True)
        boundary = archive_base / "date=2026-02-18"
        boundary.mkdir()

        mock_dt = mocker.patch("cta_eta.data_collection.compaction.archiver.datetime")
        mock_dt.now.return_value = datetime(2026, 2, 25, tzinfo=UTC)

        mock_d = mocker.patch("cta_eta.data_collection.compaction.archiver.date")
        mock_d.fromisoformat = date.fromisoformat

        result = prune_archive(archive_base, retention_days=7)

        assert result == []
        assert boundary.is_dir()

    def test_custom_retention_days(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """retention_days is respected when different from default."""
        archive_base = tmp_path / "archive"
        archive_base.mkdir(parents=True)
        (archive_base / "date=2026-02-20").mkdir()

        mock_d = mocker.patch("cta_eta.data_collection.compaction.archiver.date")
        mock_d.today.return_value = date(2026, 2, 25)
        mock_d.fromisoformat = date.fromisoformat

        result = prune_archive(archive_base, retention_days=3)

        assert len(result) == 1
        assert result[0].name == "date=2026-02-20"
        assert not (archive_base / "date=2026-02-20").exists()

    def test_unparseable_dir_name_skipped(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Directories not matching date=YYYY-MM-DD are skipped (no ValueError)."""
        archive_base = tmp_path / "archive"
        archive_base.mkdir(parents=True)
        (archive_base / "date=2026-02-10").mkdir()
        (archive_base / "date=not-a-date").mkdir()
        (archive_base / "date=").mkdir()

        mock_d = mocker.patch("cta_eta.data_collection.compaction.archiver.date")
        mock_d.today.return_value = date(2026, 2, 25)
        mock_d.fromisoformat = date.fromisoformat

        result = prune_archive(archive_base, retention_days=7)

        assert len(result) == 1
        assert result[0].name == "date=2026-02-10"
        assert not (archive_base / "date=2026-02-10").exists()
        assert (archive_base / "date=not-a-date").is_dir()
        assert (archive_base / "date=").is_dir()

    def test_rmtree_oserror_suppressed_not_in_pruned(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """When shutil.rmtree raises OSError, dir is not appended to pruned and loop continues."""
        real_rmtree = shutil.rmtree
        archive_base = tmp_path / "archive"
        archive_base.mkdir(parents=True)
        ok_dir = archive_base / "date=2026-02-01"
        fail_dir = archive_base / "date=2026-02-02"
        ok_dir.mkdir()
        fail_dir.mkdir()

        mock_d = mocker.patch("cta_eta.data_collection.compaction.archiver.date")
        mock_d.today.return_value = date(2026, 2, 25)
        mock_d.fromisoformat = date.fromisoformat

        def rmtree_side_effect(path: Path) -> None:
            if path == fail_dir:
                raise OSError(13, "Permission denied")
            real_rmtree(path)

        mocker.patch(
            "cta_eta.data_collection.compaction.archiver.shutil.rmtree",
            side_effect=rmtree_side_effect,
        )

        result = prune_archive(archive_base, retention_days=7)

        assert result == [ok_dir]
        assert not ok_dir.exists()
        assert fail_dir.exists()
