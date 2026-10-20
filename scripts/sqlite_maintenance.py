"""Safe offline backup, verification, and restore for an Aegis SQLite database."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from urllib.parse import quote


def verify_database(path: Path) -> tuple[bool, str]:
    """Run SQLite integrity_check without writing to the database."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        return False, f"database file does not exist: {resolved}"
    try:
        uri = f"file:{quote(str(resolved))}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                return False, f"integrity_check failed: {result[0] if result else 'no result'}"
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if not tables:
                return False, "database contains no application tables"
    except (OSError, sqlite3.Error, ValueError) as exc:
        return False, f"database could not be verified: {exc}"
    return True, "integrity_check passed"


def _copy_database(source: Path, temporary: Path) -> None:
    uri = f"file:{quote(str(source.resolve()))}?mode=ro"
    with (
        closing(sqlite3.connect(uri, uri=True, timeout=10)) as src,
        closing(sqlite3.connect(temporary)) as dst,
    ):
        src.backup(dst)


def _temporary_path(destination: Path, suffix: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=suffix, dir=destination.parent
    )
    os.close(fd)
    return Path(name)


def backup_database(source: Path, destination: Path) -> None:
    """Create a consistent snapshot and refuse to overwrite an existing backup."""
    source, destination = source.expanduser().resolve(), destination.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source database does not exist: {source}")
    if source == destination:
        raise ValueError("backup destination must differ from source database")
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite existing backup: {destination}")
    temporary = _temporary_path(destination, ".tmp")
    try:
        _copy_database(source, temporary)
        valid, message = verify_database(temporary)
        if not valid:
            raise sqlite3.DatabaseError(f"generated backup failed validation: {message}")
        # Exclusive hard-link creation avoids a check-then-overwrite race.
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def restore_database(backup: Path, destination: Path, *, replace: bool = False) -> None:
    """Validate and stage a backup before atomically replacing the destination."""
    backup, destination = backup.expanduser().resolve(), destination.expanduser().resolve()
    if not backup.is_file():
        raise FileNotFoundError(f"backup file does not exist: {backup}")
    if backup == destination:
        raise ValueError("restore source and destination must differ")
    valid, message = verify_database(backup)
    if not valid:
        raise sqlite3.DatabaseError(f"refusing to restore invalid backup: {message}")
    if destination.exists() and not replace:
        raise FileExistsError(
            f"destination already exists: {destination}; pass --replace only after stopping the app"
        )
    if destination.exists() and replace:
        for suffix in ("-wal", "-shm"):
            if Path(f"{destination}{suffix}").exists():
                raise RuntimeError(
                    f"refusing restore while {destination}{suffix} exists; stop the app and "
                    "resolve SQLite sidecar files first"
                )
    temporary = _temporary_path(destination, ".restore")
    try:
        _copy_database(backup, temporary)
        valid, message = verify_database(temporary)
        if not valid:
            raise sqlite3.DatabaseError(f"staged restore failed validation: {message}")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="create and validate a consistent backup")
    backup.add_argument("source", type=Path)
    backup.add_argument("destination", type=Path)
    verify = commands.add_parser("verify", help="verify an existing database")
    verify.add_argument("database", type=Path)
    restore = commands.add_parser("restore", help="restore a validated backup")
    restore.add_argument("backup", type=Path)
    restore.add_argument("destination", type=Path)
    restore.add_argument("--replace", action="store_true", help="replace an existing destination")
    args = parser.parse_args()
    try:
        if args.command == "backup":
            backup_database(args.source, args.destination)
            print(f"Backup created and verified: {args.destination}")
        elif args.command == "verify":
            valid, message = verify_database(args.database)
            print(message)
            return 0 if valid else 1
        else:
            restore_database(args.backup, args.destination, replace=args.replace)
            print(f"Restore completed and verified: {args.destination}")
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
