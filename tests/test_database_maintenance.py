"""Regression tests for safe SQLite maintenance commands."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts import sqlite_maintenance


def make_database(path: Path, value: str = "original") -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records VALUES (?)", (value,))


def read_value(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT value FROM records").fetchone()[0]


def test_backup_is_consistent_and_verified(tmp_path: Path) -> None:
    source, backup = tmp_path / "source.sqlite", tmp_path / "backup.sqlite"
    make_database(source)
    sqlite_maintenance.backup_database(source, backup)
    assert sqlite_maintenance.verify_database(backup) == (True, "integrity_check passed")
    assert read_value(backup) == "original"


def test_backup_refuses_overwrite_and_source_destination_collision(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    make_database(source)
    with pytest.raises(ValueError, match="differ"):
        sqlite_maintenance.backup_database(source, source)
    backup = tmp_path / "backup.sqlite"
    backup.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError, match="overwrite"):
        sqlite_maintenance.backup_database(source, backup)
    assert backup.read_text(encoding="utf-8") == "existing"


def test_restore_requires_explicit_replace_and_restores_data(tmp_path: Path) -> None:
    backup, target = tmp_path / "backup.sqlite", tmp_path / "target.sqlite"
    make_database(backup, "restored")
    make_database(target, "keep")
    with pytest.raises(FileExistsError, match="--replace"):
        sqlite_maintenance.restore_database(backup, target)
    assert read_value(target) == "keep"
    sqlite_maintenance.restore_database(backup, target, replace=True)
    assert read_value(target) == "restored"


def test_corrupt_backup_never_replaces_target(tmp_path: Path) -> None:
    backup, target = tmp_path / "corrupt.sqlite", tmp_path / "target.sqlite"
    backup.write_bytes(b"not a sqlite database")
    make_database(target, "keep")
    with pytest.raises(sqlite3.DatabaseError, match="invalid backup"):
        sqlite_maintenance.restore_database(backup, target, replace=True)
    assert read_value(target) == "keep"


def test_restore_refuses_wal_or_shm_sidecars(tmp_path: Path) -> None:
    backup, target = tmp_path / "backup.sqlite", tmp_path / "target.sqlite"
    make_database(backup, "restored")
    make_database(target, "keep")
    Path(f"{target}-wal").touch()
    with pytest.raises(RuntimeError, match="sidecar"):
        sqlite_maintenance.restore_database(backup, target, replace=True)
    assert read_value(target) == "keep"


def test_verify_rejects_empty_and_missing_databases(tmp_path: Path) -> None:
    empty = tmp_path / "empty.sqlite"
    with sqlite3.connect(empty):
        pass
    assert not sqlite_maintenance.verify_database(empty)[0]
    assert not sqlite_maintenance.verify_database(tmp_path / "missing.sqlite")[0]
