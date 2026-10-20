# Database recovery and governance export verification

These offline utilities support recovery of the local SQLite prototype. Stop the API and any process that writes to the database before restoring it. Protect backups like the source database: they may contain workflow history, governance records, and (in testnet mode) signed transaction bytes.

## Create and verify a backup

From the repository root:

```bash
uv run --locked python -m scripts.sqlite_maintenance backup \
  "$AEGIS_DB_PATH" "./backups/aegis-workflow-$(date +%Y%m%d-%H%M%S).sqlite3"
uv run --locked python -m scripts.sqlite_maintenance verify \
  "./backups/aegis-workflow-YYYYMMDD-HHMMSS.sqlite3"
```

The backup uses SQLite's online backup API for a consistent snapshot, then validates it with `PRAGMA integrity_check`. Existing destination files are never overwritten. Store backups on protected storage with enough free space. The tool does not encrypt backups or upload them elsewhere.

## Restore a backup

First stop the API and every worker using the database. Verify the backup, then restore to a new path to inspect it before replacing the active database:

```bash
uv run --locked python -m scripts.sqlite_maintenance verify ./backups/aegis-workflow.sqlite3
uv run --locked python -m scripts.sqlite_maintenance restore \
  ./backups/aegis-workflow.sqlite3 ./aegis-restore-check.sqlite3
uv run --locked python -m scripts.sqlite_maintenance verify ./aegis-restore-check.sqlite3
```

To replace an existing destination, pass `--replace` only after stopping the application. The tool refuses replacement when SQLite `-wal` or `-shm` sidecar files are present; investigate with the application stopped rather than deleting them blindly. Restore stages and validates a temporary copy beside the destination before atomic replacement. Keep a separate pre-restore backup of important destination data.

`integrity_check` checks SQLite structural consistency; it does not prove data is current, semantically correct, authentic, or complete. A consistent backup can still contain compromised or stale records.

## Verify a governance export

Save an owner-authenticated export from `GET /governance/export?format=json` or `GET /governance/export?format=csv` locally, then run:

```bash
uv run --locked python -m scripts.verify_governance_export ./governance-export.json
uv run --locked python -m scripts.verify_governance_export ./governance-export.csv
```

The verifier checks JSON schema shape and the export's declared `integrity.valid` flag, or CSV required columns and per-row `governance_integrity_valid` flags. It does **not** recompute the database audit chain, cryptographically authenticate the file, prove who exported it, or protect against an attacker who rewrites the database and recomputes its hashes. Run `GET /governance/audit/verify` against the active database as a separate owner-authenticated check. CSV formula neutralisation remains the responsibility of the existing export endpoint.

## Checks

```bash
uv run --locked ruff check .
uv run --locked python -m pytest -q
```

These utilities are offline and do not sign, broadcast, or query chain transactions. Simulation remains the default; the optional AegisVault integration is unaudited and Base Sepolia-only.
