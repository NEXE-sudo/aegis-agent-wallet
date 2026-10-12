# SQLite Backup, Restore, and Local Reset

This guide applies to the local, simulation-only Aegis Agent Wallet prototype. The SQLite database contains transaction workflow state and audit history. Treat the database and every backup as sensitive application data.

## Before you begin

- Run commands from the same working directory used to resolve a relative `AEGIS_DB_PATH`.
- Confirm the actual database path. If `AEGIS_DB_PATH` is unset, the default is `aegis-workflow.sqlite3`.
- Stop the API process before restore or reset operations. Do not replace, rename, or delete a database while the application is using it.
- Keep backups outside version control and protect them with appropriate filesystem permissions. Never upload a database or backup to an issue or public repository.

The examples below use macOS/Linux shell syntax and the `sqlite3` command-line tool. Replace paths with your actual paths.

## Create a consistent backup

SQLite's `.backup` command uses SQLite's backup API rather than copying a potentially active database file byte-for-byte.

With the API stopped, run:

```bash
DB_PATH="${AEGIS_DB_PATH:-aegis-workflow.sqlite3}"
BACKUP_PATH="./aegis-workflow-backup.sqlite3"

sqlite3 "$DB_PATH" ".backup '$BACKUP_PATH'"
sqlite3 "$BACKUP_PATH" "PRAGMA integrity_check;"
```

The integrity check should print `ok`. If the command fails or reports anything other than `ok`, do not rely on that backup as a recovery copy. Keep more than one backup when the data matters, and store copies separately from the working database.

## Restore into a separate database file

Prefer restoring into a new path first. This leaves the current database untouched while you validate the recovery copy.

1. Stop the API process.
2. Create and validate the backup as described above, if the current database is still available.
3. Restore the selected backup to a new file:

```bash
RESTORE_PATH="./aegis-workflow-restored.sqlite3"
sqlite3 "$BACKUP_PATH" ".backup '$RESTORE_PATH'"
sqlite3 "$RESTORE_PATH" "PRAGMA integrity_check;"
```

4. Continue only if the integrity check prints `ok`. Point the application at the restored file and start it:

```bash
export AEGIS_DB_PATH="./aegis-workflow-restored.sqlite3"
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

5. Verify expected records and application behavior before treating the restored file as your working database.

Restoring an older backup rolls workflow and audit history back to the state captured in that backup. Do not merge an old database into a newer one by manually editing SQLite tables.

## Reset local demo data safely

A reset discards the active demo's visible workflow state and audit history. For a reversible reset, **use a new database path rather than deleting the existing database**.

Stop the API, then choose a fresh filename:

```bash
export AEGIS_DB_PATH="./aegis-workflow-demo-reset.sqlite3"
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The application will initialize a new database at that path. The previous database remains available at its original path. Make sure the new path does not already contain data you intend to keep.

To return to the previous database, stop the API and set `AEGIS_DB_PATH` back to the previous path before restarting. Keep in mind that changing paths switches the database being used; it does not copy records between databases.

## Troubleshooting and limitations

- **Database is locked or busy:** stop other application processes using that database and retry. Do not force-delete SQLite files to clear a lock.
- **Integrity check does not return `ok`:** preserve the original and backup files, avoid further writes, and recover from a known-good backup.
- **Insufficient disk space or permission errors:** check the destination directory and available space before retrying.
- **WAL or shared-memory files exist:** stop all processes using the database before maintenance. Do not selectively remove SQLite sidecar files while a process is active.
- **A backup is not a security boundary:** anyone with access to it may be able to inspect persisted proposals and audit history. Protect, retain, and delete backup copies according to your data-handling needs.

These steps are for local prototype operations, not a production backup policy. The project remains simulation-only; database restore does not establish on-chain execution or transaction finality.
