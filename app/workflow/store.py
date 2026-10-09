"""SQLite-backed transaction state with atomic transitions and replay protection."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = os.environ.get("AEGIS_DB_PATH", "aegis-workflow.sqlite3")


class WorkflowStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    transaction_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    policy_decision TEXT NOT NULL,
                    policy_reasons_json TEXT NOT NULL,
                    risk_score INTEGER NOT NULL,
                    risk_level TEXT NOT NULL,
                    risk_reasons_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    approval_fingerprint TEXT,
                    execution_reference TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_transactions_fingerprint
                ON transactions(fingerprint)
            """)

    def create(self, record: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO transactions (
                    transaction_id, fingerprint, proposal_json, policy_decision,
                    policy_reasons_json, risk_score, risk_level, risk_reasons_json, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["transaction_id"], record["fingerprint"],
                    json.dumps(record["proposal"], sort_keys=True),
                    record["policy_decision"], json.dumps(record["policy_reasons"]),
                    record["risk_score"], record["risk_level"],
                    json.dumps(record["risk_reasons"]), record["status"],
                ),
            )

    def get(self, transaction_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)
            ).fetchone()
        return self._decode(row) if row else None

    def approve(self, transaction_id: str, fingerprint: str) -> tuple[str, dict[str, Any] | None]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)
            ).fetchone()
            if row is None:
                db.execute("COMMIT")
                return "not_found", None
            record = self._decode(row)
            if record["fingerprint"] != fingerprint:
                db.execute("COMMIT")
                return "fingerprint_mismatch", record
            if record["status"] == "blocked":
                db.execute("COMMIT")
                return "blocked", record
            if record["status"] == "executed_simulated":
                db.execute("COMMIT")
                return "already_executed", record
            if record["status"] == "approved":
                db.execute("COMMIT")
                return "already_approved", record
            if record["status"] != "awaiting_approval":
                db.execute("COMMIT")
                return "approval_not_required", record
            db.execute(
                """UPDATE transactions SET status = 'approved', approval_fingerprint = ?,
                   updated_at = CURRENT_TIMESTAMP WHERE transaction_id = ?""",
                (fingerprint, transaction_id),
            )
            db.execute("COMMIT")
        return "approved", self.get(transaction_id)

    def execute_simulated(self, transaction_id: str) -> tuple[str, dict[str, Any] | None]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)
            ).fetchone()
            if row is None:
                db.execute("COMMIT")
                return "not_found", None
            record = self._decode(row)
            if record["status"] == "blocked":
                db.execute("COMMIT")
                return "blocked", record
            if record["status"] == "executed_simulated":
                db.execute("COMMIT")
                return "already_executed", record
            if record["status"] == "awaiting_approval":
                db.execute("COMMIT")
                return "approval_required", record
            if record["status"] not in {"ready", "approved"}:
                db.execute("COMMIT")
                return "invalid_state", record
            if record["status"] == "approved" and record["approval_fingerprint"] != record["fingerprint"]:
                db.execute("COMMIT")
                return "approval_mismatch", record
            # This milestone deliberately simulates execution. No signing or RPC calls occur.
            reference = "simulated:" + record["fingerprint"][:24]
            db.execute(
                """UPDATE transactions SET status = 'executed_simulated',
                   execution_reference = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE transaction_id = ?""",
                (reference, transaction_id),
            )
            db.execute("COMMIT")
        return "executed", self.get(transaction_id)

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["proposal"] = json.loads(record.pop("proposal_json"))
        record["policy_reasons"] = json.loads(record.pop("policy_reasons_json"))
        record["risk_reasons"] = json.loads(record.pop("risk_reasons_json"))
        return record
