"""SQLite-backed transaction state with atomic budget reservations and replay protection."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = os.environ.get("AEGIS_DB_PATH", "aegis-workflow.sqlite3")
RESERVED_STATUSES = ("ready", "awaiting_approval", "approved", "executed_simulated")

def _proposal_fingerprint(proposal: dict[str, Any]) -> str:
    """Recompute the canonical fingerprint from the persisted proposal payload."""
    canonical = json.dumps(proposal, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()




class WorkflowStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        raw_path = str(db_path)
        if not raw_path.strip():
            raise ValueError("db_path cannot be empty")
        self.db_path = str(Path(raw_path).expanduser()) if raw_path != ":memory:" else raw_path
        if self.db_path != ":memory:":
            Path(self.db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
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
                    approved_at TEXT,
                    execution_reference TEXT,
                    idempotency_key TEXT,
                    chain_id INTEGER NOT NULL DEFAULT 0,
                    token_address TEXT NOT NULL DEFAULT '',
                    amount_base_units INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Additive migration keeps existing local prototype databases intact.
            existing = {row["name"] for row in db.execute("PRAGMA table_info(transactions)")}
            migrations = {
                "idempotency_key": "TEXT",
                "approved_at": "TEXT",
                "chain_id": "INTEGER NOT NULL DEFAULT 0",
                "token_address": "TEXT NOT NULL DEFAULT ''",
                "amount_base_units": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, declaration in migrations.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE transactions ADD COLUMN {name} {declaration}")
            # Backfill the new accounting columns for older records.
            rows = db.execute(
                "SELECT transaction_id, proposal_json FROM transactions "
                "WHERE chain_id = 0 OR token_address = '' OR amount_base_units = 0"
            ).fetchall()
            for row in rows:
                try:
                    proposal = json.loads(row["proposal_json"])
                    db.execute(
                        """UPDATE transactions SET chain_id = ?, token_address = ?,
                           amount_base_units = ? WHERE transaction_id = ?""",
                        (int(proposal.get("chain_id", 0)), str(proposal.get("token_address", "")).lower(),
                         int(proposal.get("amount_base_units", 0)), row["transaction_id"]),
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
            db.execute("CREATE INDEX IF NOT EXISTS idx_transactions_fingerprint ON transactions(fingerprint)")
            db.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_idempotency
                ON transactions(idempotency_key) WHERE idempotency_key IS NOT NULL
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_transactions_daily_spend
                ON transactions(chain_id, token_address, created_at, status)
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_role TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT,
                    fingerprint TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_events_transaction
                ON audit_events(transaction_id, event_id)
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS audit_events_no_update
                BEFORE UPDATE ON audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'audit events are append-only');
                END
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
                BEFORE DELETE ON audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'audit events are append-only');
                END
            """)

    @staticmethod
    def _append_audit(
        db: sqlite3.Connection,
        transaction_id: str,
        event_type: str,
        actor_role: str,
        from_status: str | None,
        to_status: str | None,
        fingerprint: str | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Append an audit event inside the caller's existing SQLite transaction."""
        db.execute(
            """INSERT INTO audit_events (
                transaction_id, event_type, actor_role, from_status, to_status,
                fingerprint, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                transaction_id, event_type, actor_role, from_status, to_status, fingerprint,
                json.dumps(details or {}, sort_keys=True),
            ),
        )

    def audit_events(self, transaction_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT event_id, transaction_id, event_type, actor_role, from_status,
                          to_status, fingerprint, details_json, created_at
                   FROM audit_events WHERE transaction_id = ? ORDER BY event_id""",
                (transaction_id,),
            ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["details"] = json.loads(event.pop("details_json"))
            events.append(event)
        return events

    @staticmethod
    def _daily_spend(
        db: sqlite3.Connection,
        chain_id: int,
        token_address: str,
        exclude_transaction_id: str | None = None,
    ) -> int:
        statuses = ",".join("?" for _ in RESERVED_STATUSES)
        query = (
            "SELECT amount_base_units FROM transactions "
            f"WHERE date(created_at) = date('now') AND chain_id = ? "
            f"AND lower(token_address) = ? AND status IN ({statuses})"
        )
        args: list[Any] = [chain_id, token_address.lower(), *RESERVED_STATUSES]
        if exclude_transaction_id is not None:
            query += " AND transaction_id != ?"
            args.append(exclude_transaction_id)
        # SQLite SUM(INTEGER) can overflow even when every stored amount is valid.
        return sum(int(row["amount_base_units"]) for row in db.execute(query, args).fetchall())

    @staticmethod
    def _recipient_daily_spend(
        db: sqlite3.Connection,
        chain_id: int,
        token_address: str,
        recipient: str,
        exclude_transaction_id: str | None = None,
    ) -> int:
        statuses = ",".join("?" for _ in RESERVED_STATUSES)
        query = (
            "SELECT amount_base_units FROM transactions "
            "WHERE date(created_at) = date('now') AND chain_id = ? "
            "AND lower(token_address) = ? "
            "AND lower(json_extract(proposal_json, '$.recipient')) = ? "
            f"AND status IN ({statuses})"
        )
        args: list[Any] = [
            chain_id, token_address.lower(), recipient.strip().lower(), *RESERVED_STATUSES
        ]
        if exclude_transaction_id is not None:
            query += " AND transaction_id != ?"
            args.append(exclude_transaction_id)
        return sum(int(row["amount_base_units"]) for row in db.execute(query, args).fetchall())

    def create_evaluated(
        self, proposal: dict[str, Any], idempotency_key: str | None,
        build_record: Callable[[int, int], dict[str, Any]],
        actor_role: str = "agent",
    ) -> tuple[str, dict[str, Any]]:
        """Serializes spend check + reservation so concurrent proposals cannot overspend."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                prior = db.execute(
                    "SELECT * FROM transactions WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if prior:
                    record = self._decode(prior)
                    if _proposal_fingerprint(record["proposal"]) != record["fingerprint"]:
                        self._append_audit(
                            db, record["transaction_id"], "proposal_integrity_mismatch", actor_role,
                            record["status"], record["status"], record["fingerprint"],
                            {
                                "operation": "idempotency_replay",
                                "reason": "persisted proposal does not match fingerprint",
                            },
                        )
                        db.execute("COMMIT")
                        return "idempotency_conflict", record
                    if _proposal_fingerprint(record["proposal"]) != _proposal_fingerprint(proposal):
                        self._append_audit(
                            db, record["transaction_id"], "idempotency_conflict", actor_role,
                            record["status"], record["status"], record["fingerprint"],
                            {"reason": "key reused with a different proposal"},
                        )
                        db.execute("COMMIT")
                        return "idempotency_conflict", record
                    if record["fingerprint"] != build_record(0, 0)["fingerprint"]:
                        self._append_audit(
                            db, record["transaction_id"], "idempotency_conflict", actor_role,
                            record["status"], record["status"], record["fingerprint"],
                            {"reason": "key reused with a different proposal"},
                        )
                        db.execute("COMMIT")
                        return "idempotency_conflict", record
                    self._append_audit(
                        db, record["transaction_id"], "proposal_replayed", actor_role,
                        record["status"], record["status"], record["fingerprint"],
                        {"idempotency_key_present": True},
                    )
                    db.execute("COMMIT")
                    return "replayed", record

            spent = self._daily_spend(
                db, int(proposal["chain_id"]), str(proposal["token_address"])
            )
            recipient_spent = self._recipient_daily_spend(
                db, int(proposal["chain_id"]), str(proposal["token_address"]),
                str(proposal["recipient"]),
            )
            record = build_record(spent, recipient_spent)
            db.execute(
                """INSERT INTO transactions (
                    transaction_id, fingerprint, proposal_json, policy_decision,
                    policy_reasons_json, risk_score, risk_level, risk_reasons_json, status,
                    idempotency_key, chain_id, token_address, amount_base_units
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["transaction_id"], record["fingerprint"],
                    json.dumps(record["proposal"], sort_keys=True),
                    record["policy_decision"], json.dumps(record["policy_reasons"]),
                    record["risk_score"], record["risk_level"], json.dumps(record["risk_reasons"]),
                    record["status"], idempotency_key, int(proposal["chain_id"]),
                    str(proposal["token_address"]).lower(), int(proposal["amount_base_units"]),
                ),
            )
            self._append_audit(
                db, record["transaction_id"], "proposal_created", actor_role,
                None, record["status"], record["fingerprint"],
                {
                    "policy_decision": record["policy_decision"],
                    "risk_level": record["risk_level"],
                    "idempotency_key_present": bool(idempotency_key),
                },
            )
            db.execute("COMMIT")
        return "created", self.get(record["transaction_id"]) or record

    def create(self, record: dict[str, Any], actor_role: str = "system") -> None:
        proposal = record["proposal"]
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO transactions (
                    transaction_id, fingerprint, proposal_json, policy_decision,
                    policy_reasons_json, risk_score, risk_level, risk_reasons_json, status,
                    chain_id, token_address, amount_base_units
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["transaction_id"], record["fingerprint"],
                    json.dumps(proposal, sort_keys=True), record["policy_decision"],
                    json.dumps(record["policy_reasons"]), record["risk_score"],
                    record["risk_level"], json.dumps(record["risk_reasons"]), record["status"],
                    int(proposal.get("chain_id", 0)), str(proposal.get("token_address", "")).lower(),
                    int(proposal.get("amount_base_units", 0)),
                ),
            )
            self._append_audit(
                db, record["transaction_id"], "proposal_created", actor_role,
                None, record["status"], record["fingerprint"],
                {"policy_decision": record["policy_decision"], "risk_level": record["risk_level"]},
            )
            db.execute("COMMIT")

    def daily_spend(self, chain_id: int, token_address: str,
                    exclude_transaction_id: str | None = None) -> int:
        with self._connect() as db:
            return self._daily_spend(db, chain_id, token_address, exclude_transaction_id)

    def get(self, transaction_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)).fetchone()
        return self._decode(row) if row else None

    def approve(
        self, transaction_id: str, fingerprint: str, actor_role: str = "owner",
        approval_expires_seconds: int = 300,
    ) -> tuple[str, dict[str, Any] | None]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)).fetchone()
            if row is None:
                db.execute("COMMIT")
                return "not_found", None
            record = self._decode(row)
            if _proposal_fingerprint(record["proposal"]) != record["fingerprint"]:
                self._append_audit(
                    db, transaction_id, "proposal_integrity_mismatch", actor_role,
                    record["status"], record["status"], record["fingerprint"],
                    {"operation": "approve", "reason": "persisted proposal does not match fingerprint"},
                )
                db.execute("COMMIT")
                return "proposal_integrity_mismatch", record
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
                now = _utc_now()
                approved_at = record.get("approved_at")
                expired = approved_at is None
                if approved_at is not None:
                    try:
                        approval_time = datetime.fromisoformat(approved_at)
                        if approval_time.tzinfo is None:
                            approval_time = approval_time.replace(tzinfo=UTC)
                        elapsed = (now - approval_time).total_seconds()
                        expired = elapsed < 0 or elapsed >= int(approval_expires_seconds)
                    except (TypeError, ValueError):
                        expired = True
                if expired:
                    db.execute(
                        """UPDATE transactions SET status = 'awaiting_approval',
                           approval_fingerprint = NULL, approved_at = NULL,
                           updated_at = CURRENT_TIMESTAMP WHERE transaction_id = ?""",
                        (transaction_id,),
                    )
                    self._append_audit(
                        db, transaction_id, "approval_expired", actor_role,
                        "approved", "awaiting_approval", record["fingerprint"],
                        {
                            "operation": "approve",
                            "approved_at": approved_at,
                            "expired_at": now.isoformat(),
                            "approval_expires_seconds": int(approval_expires_seconds),
                        },
                    )
                    db.execute("COMMIT")
                    return "approval_expired", self.get(transaction_id)
                db.execute("COMMIT")
                return "already_approved", record
            if record["status"] != "awaiting_approval":
                db.execute("COMMIT")
                return "approval_not_required", record
            approved_at = _utc_now().isoformat()
            db.execute(
                """UPDATE transactions SET status = 'approved', approval_fingerprint = ?,
                   approved_at = ?, updated_at = CURRENT_TIMESTAMP WHERE transaction_id = ?""",
                (fingerprint, approved_at, transaction_id),
            )
            self._append_audit(
                db, transaction_id, "approval_granted", actor_role, record["status"], "approved",
                fingerprint, {"confirmation": "APPROVE", "approval_expires_seconds": approval_expires_seconds},
            )
            db.execute("COMMIT")
        return "approved", self.get(transaction_id)

    def execute_simulated(
        self, transaction_id: str, policy: Any, actor_role: str = "owner"
    ) -> tuple[str, dict[str, Any] | None]:
        """Revalidates policy and spend inside the same write lock as execution."""
        from app.policy.engine import evaluate_transaction
        from app.policy.models import PolicyDecision, TransactionProposal
        from app.risk.engine import assess_transaction_risk
        from app.risk.models import RiskLevel

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)).fetchone()
            if row is None:
                db.execute("COMMIT")
                return "not_found", None
            record = self._decode(row)
            if _proposal_fingerprint(record["proposal"]) != record["fingerprint"]:
                self._append_audit(
                    db, transaction_id, "proposal_integrity_mismatch", actor_role,
                    record["status"], record["status"], record["fingerprint"],
                    {"operation": "execute", "reason": "persisted proposal does not match fingerprint"},
                )
                db.execute("COMMIT")
                return "proposal_integrity_mismatch", record
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
            if record["status"] == "approved":
                now = _utc_now()
                approved_at = record.get("approved_at")
                expired = approved_at is None
                if approved_at is not None:
                    try:
                        approval_time = datetime.fromisoformat(approved_at)
                        if approval_time.tzinfo is None:
                            approval_time = approval_time.replace(tzinfo=UTC)
                        elapsed = (now - approval_time).total_seconds()
                        expired = elapsed < 0 or elapsed >= int(policy.approval_expires_seconds)
                    except (TypeError, ValueError):
                        expired = True
                if expired:
                    db.execute(
                        """UPDATE transactions SET status = 'awaiting_approval',
                           approval_fingerprint = NULL, approved_at = NULL,
                           updated_at = CURRENT_TIMESTAMP WHERE transaction_id = ?""",
                        (transaction_id,),
                    )
                    self._append_audit(
                        db, transaction_id, "approval_expired", actor_role,
                        "approved", "awaiting_approval", record["fingerprint"],
                        {
                            "approved_at": approved_at,
                            "expired_at": now.isoformat(),
                            "approval_expires_seconds": int(policy.approval_expires_seconds),
                        },
                    )
                    db.execute("COMMIT")
                    return "approval_required", self.get(transaction_id)

            proposal = TransactionProposal(**record["proposal"])
            spent = self._daily_spend(
                db, proposal.chain_id, proposal.token_address, exclude_transaction_id=transaction_id
            )
            recipient_spent = self._recipient_daily_spend(
                db, proposal.chain_id, proposal.token_address, proposal.recipient,
                exclude_transaction_id=transaction_id,
            )
            result = evaluate_transaction(proposal, policy, spent, recipient_spent)
            if result.decision == PolicyDecision.BLOCK:
                db.execute(
                    """UPDATE transactions SET status = 'blocked', policy_decision = ?,
                       policy_reasons_json = ?, approval_fingerprint = NULL, approved_at = NULL,
                       updated_at = CURRENT_TIMESTAMP WHERE transaction_id = ?""",
                    (result.decision.value, json.dumps(result.reasons), transaction_id),
                )
                self._append_audit(
                    db, transaction_id, "policy_revalidation_blocked", actor_role,
                    record["status"], "blocked", record["fingerprint"],
                    {"policy_reasons": result.reasons},
                )
                db.execute("COMMIT")
                return "blocked", self.get(transaction_id)
            risk_result = assess_transaction_risk(proposal, policy, spent)
            risk_changed = (
                risk_result.score != record["risk_score"]
                or risk_result.level.value != record["risk_level"]
                or risk_result.reasons != record["risk_reasons"]
            )
            if risk_result.level == RiskLevel.HIGH and (
                record["status"] == "ready"
                or (record["status"] == "approved" and risk_changed)
            ):
                db.execute(
                    """UPDATE transactions SET status = 'awaiting_approval',
                       policy_decision = ?, policy_reasons_json = ?, risk_score = ?,
                       risk_level = ?, risk_reasons_json = ?, approval_fingerprint = NULL,
                       approved_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE transaction_id = ?""",
                    (
                        result.decision.value, json.dumps(result.reasons), risk_result.score,
                        risk_result.level.value, json.dumps(risk_result.reasons), transaction_id,
                    ),
                )
                self._append_audit(
                    db, transaction_id, "risk_escalation_requires_approval", actor_role,
                    record["status"], "awaiting_approval", record["fingerprint"],
                    {"risk_score": risk_result.score, "risk_level": risk_result.level.value,
                     "risk_reasons": risk_result.reasons},
                )
                db.execute("COMMIT")
                return "approval_required", self.get(transaction_id)
            if result.decision == PolicyDecision.REQUIRE_APPROVAL:
                # A still-valid approval may satisfy the same policy requirement that existed
                # when the proposal was approved. A newly introduced requirement invalidates it.
                previously_required = record["policy_decision"] == PolicyDecision.REQUIRE_APPROVAL.value
                if record["status"] != "approved" or not previously_required:
                    db.execute(
                        """UPDATE transactions SET status = 'awaiting_approval', policy_decision = ?,
                           policy_reasons_json = ?, approval_fingerprint = NULL, approved_at = NULL,
                           updated_at = CURRENT_TIMESTAMP WHERE transaction_id = ?""",
                        (result.decision.value, json.dumps(result.reasons), transaction_id),
                    )
                    self._append_audit(
                        db, transaction_id, "policy_revalidation_requires_approval", actor_role,
                        record["status"], "awaiting_approval", record["fingerprint"],
                        {"policy_reasons": result.reasons},
                    )
                    db.execute("COMMIT")
                    return "approval_required", self.get(transaction_id)
            # A stored risk-based approval requirement is still enforced by the state machine.
            if record["status"] == "awaiting_approval":
                db.execute("COMMIT")
                return "approval_required", record

            reference = "simulated:" + record["fingerprint"][:24]
            db.execute(
                """UPDATE transactions SET status = 'executed_simulated',
                   execution_reference = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE transaction_id = ?""",
                (reference, transaction_id),
            )
            self._append_audit(
                db, transaction_id, "simulated_execution", actor_role,
                record["status"], "executed_simulated", record["fingerprint"],
                {"execution_reference": reference},
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


def _utc_now() -> datetime:
    """Clock seam for deterministic approval-expiry tests."""
    return datetime.now(UTC)
