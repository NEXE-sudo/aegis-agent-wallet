"""SQLite-backed transaction state with atomic budget reservations and replay protection."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.policy.models import MAX_SQLITE_INTEGER

DEFAULT_DB_PATH = os.environ.get("AEGIS_DB_PATH", "aegis-workflow.sqlite3")
AUDIT_EVENT_TYPES = {
    "approval_expired",
    "approval_granted",
    "idempotency_conflict",
    "policy_revalidation_blocked",
    "policy_revalidation_requires_approval",
    "proposal_created",
    "proposal_integrity_mismatch",
    "proposal_replayed",
    "risk_escalation_requires_approval",
    "simulated_execution",
    "transaction_cancelled",
    "transaction_rejected",
    "onchain_transaction_signed",
    "onchain_transaction_submitted",
}
AUDIT_ACTOR_ROLES = {"agent", "owner", "system"}
AUDIT_STATUSES = {
    "approved",
    "awaiting_approval",
    "blocked",
    "cancelled",
    "executed_simulated",
    "ready",
    "rejected",
    "submitting_onchain",
    "submitted_onchain",
}


class OnchainSubmissionError(RuntimeError):
    """Raised when a signed testnet transaction cannot be safely submitted."""


def _proposal_fingerprint(proposal: dict[str, Any]) -> str:
    """Recompute the canonical fingerprint from the persisted proposal payload."""
    canonical = json.dumps(proposal, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_recipient(value: Any) -> str | None:
    """Use the same Unicode-aware canonicalization as application-side lookups."""
    if not isinstance(value, str):
        return None
    return value.strip().lower()


def _approval_expired(approved_at: str | None, now: datetime, expires_seconds: int) -> bool:
    if approved_at is None:
        return True
    try:
        approval_time = datetime.fromisoformat(approved_at)
        if approval_time.tzinfo is None:
            approval_time = approval_time.replace(tzinfo=UTC)
        elapsed = (now - approval_time).total_seconds()
        return elapsed < 0 or elapsed >= int(expires_seconds)
    except (TypeError, ValueError):
        return True




class WorkflowDataError(RuntimeError):
    """Raised when persisted workflow JSON is malformed or has an invalid shape."""


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
        connection.create_function(
            "normalize_recipient", 1, _normalize_recipient, deterministic=True
        )
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _init_db(self) -> None:
        with closing(self._connect()) as db:
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
                    signed_transaction TEXT,
                    executed_at TEXT,
                    recipient_normalized TEXT,
                    created_day TEXT,
                    executed_day TEXT,
                    accounting_integrity INTEGER NOT NULL DEFAULT 1,
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
                "signed_transaction": "TEXT",
                "executed_at": "TEXT",
                "recipient_normalized": "TEXT",
                "created_day": "TEXT",
                "executed_day": "TEXT",
                "accounting_integrity": "INTEGER NOT NULL DEFAULT 1",
                "chain_id": "INTEGER NOT NULL DEFAULT 0",
                "token_address": "TEXT NOT NULL DEFAULT ''",
                "amount_base_units": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, declaration in migrations.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE transactions ADD COLUMN {name} {declaration}")
            # Drop the old guard before canonicalizing legacy rows in Python.
            db.execute("DROP TRIGGER IF EXISTS transactions_accounting_integrity_guard")
            # Older simulated executions used updated_at as their execution timestamp.
            db.execute(
                """UPDATE transactions SET executed_at = updated_at
                   WHERE status IN ('executed_simulated', 'submitted_onchain')
                   AND executed_at IS NULL"""
            )
            # Backfill indexed spend keys for existing records without rewriting history.
            db.execute(
                "UPDATE transactions SET created_day = date(created_at) "
                "WHERE created_day IS NULL OR created_day = ''"
            )
            db.execute(
                "UPDATE transactions SET executed_day = date(executed_at) "
                "WHERE status IN ('executed_simulated', 'submitted_onchain') "
                "AND executed_at IS NOT NULL "
                "AND (executed_day IS NULL OR executed_day = '')"
            )
            rows = db.execute(
                "SELECT transaction_id, proposal_json FROM transactions"
            ).fetchall()
            for row in rows:
                try:
                    proposal = json.loads(row["proposal_json"])
                    recipient = proposal.get("recipient") if isinstance(proposal, dict) else None
                    if isinstance(recipient, str):
                        db.execute(
                            "UPDATE transactions SET recipient_normalized = ? WHERE transaction_id = ?",
                            (recipient.strip().lower(), row["transaction_id"]),
                        )
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
            # Backfill the legacy accounting columns for older records.
            rows = db.execute(
                "SELECT transaction_id, proposal_json FROM transactions "
                "WHERE chain_id = 0 OR token_address = '' OR amount_base_units = 0"
            ).fetchall()
            for row in rows:
                try:
                    proposal = json.loads(row["proposal_json"])
                    if not isinstance(proposal, dict):
                        continue
                    chain_id = proposal.get("chain_id")
                    token_address = proposal.get("token_address")
                    amount_base_units = proposal.get("amount_base_units")
                    token_decimals = proposal.get("token_decimals")
                    string_fields = ("agent_id", "token_symbol", "token_address", "recipient")
                    if (
                        any(not isinstance(proposal.get(name), str) for name in string_fields)
                        or any(
                            type(value) is not int
                            for value in (chain_id, amount_base_units, token_decimals)
                        )
                        or not 0 < chain_id <= MAX_SQLITE_INTEGER
                        or not 0 < amount_base_units <= MAX_SQLITE_INTEGER
                        or not 0 <= token_decimals <= 36
                    ):
                        continue
                    db.execute(
                        """UPDATE transactions SET chain_id = ?, token_address = ?,
                           amount_base_units = ? WHERE transaction_id = ?""",
                        (chain_id, token_address.lower(), amount_base_units, row["transaction_id"]),
                    )
                except (TypeError, ValueError, OverflowError, json.JSONDecodeError):
                    continue
            # Mark any legacy accounting inconsistency once, then keep this guard indexed.
            db.execute(
                """
                UPDATE transactions SET accounting_integrity = CASE
                    WHEN CASE
                    WHEN json_valid(proposal_json) = 0 THEN 1
                    WHEN json_type(proposal_json) != 'object' THEN 1
                    WHEN json_type(proposal_json, '$.chain_id') != 'integer' THEN 1
                    WHEN json_type(proposal_json, '$.amount_base_units') != 'integer' THEN 1
                    WHEN json_type(proposal_json, '$.token_address') != 'text' THEN 1
                    WHEN json_type(proposal_json, '$.recipient') != 'text' THEN 1
                    WHEN chain_id != json_extract(proposal_json, '$.chain_id') THEN 1
                    WHEN amount_base_units != json_extract(proposal_json, '$.amount_base_units') THEN 1
                    WHEN lower(trim(token_address)) !=
                         lower(trim(json_extract(proposal_json, '$.token_address'))) THEN 1
                    WHEN recipient_normalized IS NULL OR recipient_normalized !=
                         normalize_recipient(json_extract(proposal_json, '$.recipient')) THEN 1
                    WHEN created_day IS NULL OR created_day != date(created_at) THEN 1
                    WHEN status IN ('executed_simulated', 'submitted_onchain') AND (
                        executed_at IS NULL OR executed_day IS NULL
                        OR executed_day != date(executed_at)
                    ) THEN 1
                    ELSE 0
                END = 1 THEN 0 ELSE 1
                END
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_transactions_accounting_integrity ON transactions(accounting_integrity)")
            db.execute("DROP TRIGGER IF EXISTS transactions_accounting_integrity_guard")
            db.execute("""
                CREATE TRIGGER transactions_accounting_integrity_guard
                AFTER UPDATE OF proposal_json, chain_id, token_address, amount_base_units,
                    recipient_normalized, created_day, executed_day, created_at, executed_at, status
                ON transactions
                BEGIN
                    UPDATE transactions
                    SET accounting_integrity = CASE
                        WHEN CASE
                            WHEN NEW.proposal_json != OLD.proposal_json THEN 1
                            WHEN NEW.recipient_normalized != OLD.recipient_normalized THEN 1
                            WHEN json_valid(NEW.proposal_json) = 0 THEN 1
                            WHEN json_type(NEW.proposal_json) != 'object' THEN 1
                            WHEN json_type(NEW.proposal_json, '$.chain_id') != 'integer' THEN 1
                            WHEN json_type(NEW.proposal_json, '$.amount_base_units') != 'integer' THEN 1
                            WHEN json_type(NEW.proposal_json, '$.token_address') != 'text' THEN 1
                            WHEN json_type(NEW.proposal_json, '$.recipient') != 'text' THEN 1
                            WHEN NEW.chain_id != json_extract(NEW.proposal_json, '$.chain_id') THEN 1
                            WHEN NEW.amount_base_units != json_extract(NEW.proposal_json, '$.amount_base_units') THEN 1
                            WHEN lower(trim(NEW.token_address)) !=
                                 lower(trim(json_extract(NEW.proposal_json, '$.token_address'))) THEN 1
                            WHEN NEW.created_day IS NULL OR NEW.created_day != date(NEW.created_at) THEN 1
                            WHEN NEW.status IN ('executed_simulated', 'submitted_onchain') AND (
                                NEW.executed_at IS NULL OR NEW.executed_day IS NULL
                                OR NEW.executed_day != date(NEW.executed_at)
                            ) THEN 1
                            ELSE 0
                        END = 1 THEN 0
                        ELSE 1
                    END
                    WHERE transaction_id = NEW.transaction_id;
                END
            """)
            db.execute("DROP TRIGGER IF EXISTS transactions_status_insert_guard")
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS transactions_status_insert_guard
                BEFORE INSERT ON transactions
                WHEN NEW.status NOT IN (
                    'blocked', 'ready', 'awaiting_approval', 'approved', 'cancelled',
                    'rejected', 'executed_simulated', 'submitting_onchain',
                    'submitted_onchain'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'invalid transaction status');
                END
            """)
            db.execute("DROP TRIGGER IF EXISTS transactions_status_update_guard")
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS transactions_status_update_guard
                BEFORE UPDATE OF status ON transactions
                WHEN NEW.status NOT IN (
                    'blocked', 'ready', 'awaiting_approval', 'approved', 'cancelled',
                    'rejected', 'executed_simulated', 'submitting_onchain',
                    'submitted_onchain'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'invalid transaction status');
                END
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_transactions_fingerprint ON transactions(fingerprint)")
            db.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_idempotency
                ON transactions(idempotency_key) WHERE idempotency_key IS NOT NULL
            """)
            # Replace the original timestamp index only when its column layout is stale.
            daily_index_columns = tuple(
                row["name"] for row in db.execute(
                    "PRAGMA index_info(idx_transactions_daily_spend)"
                )
            )
            if daily_index_columns != ("chain_id", "token_address", "created_day", "status"):
                db.execute("DROP INDEX IF EXISTS idx_transactions_daily_spend")
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_transactions_daily_spend
                ON transactions(chain_id, token_address, created_day, status)
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_transactions_recipient_daily_spend
                ON transactions(
                    chain_id, token_address, recipient_normalized, created_day, status
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_transactions_execution_day
                ON transactions(chain_id, token_address, executed_day, status)
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_transactions_recipient_execution_day
                ON transactions(
                    chain_id, token_address, recipient_normalized, executed_day, status
                )
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
            db.execute("DROP TRIGGER IF EXISTS audit_events_validation_guard")
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS audit_events_validation_guard
                BEFORE INSERT ON audit_events
                WHEN NEW.event_type NOT IN (
                    'approval_expired', 'approval_granted', 'idempotency_conflict',
                    'policy_revalidation_blocked', 'policy_revalidation_requires_approval',
                    'proposal_created', 'proposal_integrity_mismatch', 'proposal_replayed',
                    'risk_escalation_requires_approval', 'simulated_execution',
                    'transaction_cancelled', 'transaction_rejected',
                    'onchain_transaction_signed', 'onchain_transaction_submitted'
                )
                OR NEW.actor_role NOT IN ('agent', 'owner', 'system')
                OR (
                    NEW.from_status IS NOT NULL AND NEW.from_status NOT IN (
                        'approved', 'awaiting_approval', 'blocked', 'cancelled',
                        'executed_simulated', 'ready', 'rejected', 'submitting_onchain',
                        'submitted_onchain'
                    )
                )
                OR (
                    NEW.to_status IS NOT NULL AND NEW.to_status NOT IN (
                        'approved', 'awaiting_approval', 'blocked', 'cancelled',
                        'executed_simulated', 'ready', 'rejected', 'submitting_onchain',
                        'submitted_onchain'
                    )
                )
                BEGIN
                    SELECT RAISE(ABORT, 'invalid audit event metadata');
                END
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

            # SQLite REPLACE can bypass the DELETE trigger's protection unless
            # recursive triggers are enabled. Reject inserts that reuse an event ID.
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS audit_events_no_replace
                BEFORE INSERT ON audit_events
                WHEN EXISTS (
                    SELECT 1 FROM audit_events WHERE event_id = NEW.event_id
                )
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
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT event_id, transaction_id, event_type, actor_role, from_status,
                          to_status, fingerprint, details_json, created_at
                   FROM audit_events WHERE transaction_id = ? ORDER BY event_id""",
                (transaction_id,),
            ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            if (
                type(event.get("event_id")) is not int
                or event["event_id"] <= 0
                or any(
                    not isinstance(event.get(name), str) or not event[name]
                    for name in ("transaction_id", "event_type", "actor_role", "created_at")
                )
                or event.get("event_type") not in AUDIT_EVENT_TYPES
                or event.get("actor_role") not in AUDIT_ACTOR_ROLES
                or any(
                    value is not None and not isinstance(value, str)
                    for value in (
                        event.get("from_status"),
                        event.get("to_status"),
                        event.get("fingerprint"),
                    )
                )
                or any(
                    value is not None and value not in AUDIT_STATUSES
                    for value in (event.get("from_status"), event.get("to_status"))
                )
            ):
                raise WorkflowDataError("Stored workflow data is invalid")
            try:
                details = json.loads(event.pop("details_json"))
            except (TypeError, ValueError) as exc:
                raise WorkflowDataError("Stored workflow data is invalid") from exc
            if not isinstance(details, dict):
                raise WorkflowDataError("Stored workflow data is invalid")
            event["details"] = details
            events.append(event)
        return events

    @staticmethod
    def _validate_accounting_consistency(record: dict[str, Any]) -> None:
        """Reject denormalized accounting fields that disagree with the validated proposal."""
        proposal = record["proposal"]
        if (
            type(record.get("chain_id")) is not int
            or type(record.get("amount_base_units")) is not int
            or not isinstance(record.get("token_address"), str)
            or record["chain_id"] != proposal["chain_id"]
            or record["amount_base_units"] != proposal["amount_base_units"]
            or record["token_address"].strip().lower()
            != proposal["token_address"].strip().lower()
        ):
            raise WorkflowDataError("Stored workflow data is invalid")

    @staticmethod
    def _assert_accounting_integrity(
        db: sqlite3.Connection, chain_id: int, token_address: str
    ) -> None:
        """Reject flagged rows that could affect today's spend for this chain/token.

        A corrupt historical or blocked row must not disable unrelated proposals.
        Check both denormalized accounting keys and the original proposal fields:
        a mismatch in either representation must not hide a relevant row.
        """
        today = _utc_now().date().isoformat()
        normalized_token = token_address.strip().lower()
        invalid = db.execute(
            """
            SELECT transaction_id FROM transactions
            WHERE accounting_integrity = 0
              AND (
                (
                  chain_id = ? AND lower(trim(token_address)) = ?
                  AND (
                    (status IN ('ready', 'awaiting_approval', 'approved')
                     AND (created_day = ? OR date(created_at) = ?))
                    OR
                    status = 'submitting_onchain'
                    OR
                    (status IN ('executed_simulated', 'submitted_onchain')
                     AND (executed_day = ? OR date(executed_at) = ?))
                  )
                )
                OR
                (
                  CASE
                    WHEN json_valid(proposal_json) = 0 THEN 0
                    WHEN json_type(proposal_json) != 'object' THEN 0
                    WHEN json_type(proposal_json, '$.chain_id') != 'integer' THEN 0
                    WHEN json_type(proposal_json, '$.token_address') != 'text' THEN 0
                    WHEN json_extract(proposal_json, '$.chain_id') != ? THEN 0
                    WHEN lower(trim(json_extract(proposal_json, '$.token_address'))) != ? THEN 0
                    WHEN status IN ('ready', 'awaiting_approval', 'approved')
                      AND (created_day = ? OR date(created_at) = ?) THEN 1
                    WHEN status = 'submitting_onchain' THEN 1
                    WHEN status IN ('executed_simulated', 'submitted_onchain')
                      AND (executed_day = ? OR date(executed_at) = ?) THEN 1
                    ELSE 0
                  END = 1
                )
              )
            LIMIT 1
            """,
            (
                chain_id, normalized_token, today, today, today, today,
                chain_id, normalized_token, today, today, today, today,
            ),
        ).fetchone()
        if invalid is not None:
            raise WorkflowDataError("Stored workflow data is invalid")

    @staticmethod
    def _sum_amount_rows(rows: Any) -> int:
        """Sum indexed spend values while preserving overflow-safe Python arithmetic."""
        total = 0
        for row in rows:
            amount = row["amount_base_units"]
            if type(amount) is not int or not 0 <= amount <= MAX_SQLITE_INTEGER:
                raise WorkflowDataError("Stored workflow data is invalid")
            total += amount
        return total

    @staticmethod
    def _daily_spend(
        db: sqlite3.Connection,
        chain_id: int,
        token_address: str,
        exclude_transaction_id: str | None = None,
    ) -> int:
        """Read only today's reservations and executions for this chain/token."""
        today = _utc_now().date().isoformat()
        reservation_statuses = ("ready", "awaiting_approval", "approved")
        status_placeholders = ",".join("?" for _ in reservation_statuses)
        normalized_token = token_address.strip().lower()
        query = (
            "SELECT amount_base_units FROM transactions "
            f"WHERE chain_id = ? AND token_address = ? AND "
            f"((created_day = ? AND status IN ({status_placeholders})) "
            "OR status = 'submitting_onchain')"
        )
        args: list[Any] = [chain_id, normalized_token, today, *reservation_statuses]
        if exclude_transaction_id is not None:
            query += " AND transaction_id != ?"
            args.append(exclude_transaction_id)
        query += (
            " UNION ALL SELECT amount_base_units FROM transactions "
            "WHERE chain_id = ? AND token_address = ? AND executed_day = ? "
            "AND status IN ('executed_simulated', 'submitted_onchain')"
        )
        args.extend([chain_id, normalized_token, today])
        if exclude_transaction_id is not None:
            query += " AND transaction_id != ?"
            args.append(exclude_transaction_id)
        return WorkflowStore._sum_amount_rows(db.execute(query, args))

    @staticmethod
    def _recipient_daily_spend(
        db: sqlite3.Connection,
        chain_id: int,
        token_address: str,
        recipient: str,
        exclude_transaction_id: str | None = None,
    ) -> int:
        """Read only today's spend for the normalized chain/token/recipient tuple."""
        today = _utc_now().date().isoformat()
        reservation_statuses = ("ready", "awaiting_approval", "approved")
        status_placeholders = ",".join("?" for _ in reservation_statuses)
        normalized_token = token_address.strip().lower()
        normalized_recipient = recipient.strip().lower()
        query = (
            "SELECT amount_base_units FROM transactions "
            f"WHERE chain_id = ? AND token_address = ? AND recipient_normalized = ? "
            f"AND ((created_day = ? AND status IN ({status_placeholders})) "
            "OR status = 'submitting_onchain')"
        )
        args: list[Any] = [
            chain_id, normalized_token, normalized_recipient, today, *reservation_statuses
        ]
        if exclude_transaction_id is not None:
            query += " AND transaction_id != ?"
            args.append(exclude_transaction_id)
        query += (
            " UNION ALL SELECT amount_base_units FROM transactions "
            "WHERE chain_id = ? AND token_address = ? AND recipient_normalized = ? "
            "AND executed_day = ? "
            "AND status IN ('executed_simulated', 'submitted_onchain')"
        )
        args.extend([chain_id, normalized_token, normalized_recipient, today])
        if exclude_transaction_id is not None:
            query += " AND transaction_id != ?"
            args.append(exclude_transaction_id)
        return WorkflowStore._sum_amount_rows(db.execute(query, args))

    def create_evaluated(
        self, proposal: dict[str, Any], idempotency_key: str | None,
        build_record: Callable[[int, int], dict[str, Any]],
        actor_role: str = "agent",
        policy_hash: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Serializes spend check + reservation so concurrent proposals cannot overspend."""
        with closing(self._connect()) as db:
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
                                **({"policy_hash": policy_hash} if policy_hash else {}),
                            },
                        )
                        db.execute("COMMIT")
                        return "idempotency_conflict", record
                    self._validate_accounting_consistency(record)
                    if _proposal_fingerprint(record["proposal"]) != _proposal_fingerprint(proposal):
                        self._append_audit(
                            db, record["transaction_id"], "idempotency_conflict", actor_role,
                            record["status"], record["status"], record["fingerprint"],
                            {
                                "reason": "key reused with a different proposal",
                                **({"policy_hash": policy_hash} if policy_hash else {}),
                            },
                        )
                        db.execute("COMMIT")
                        return "idempotency_conflict", record
                    self._append_audit(
                        db, record["transaction_id"], "proposal_replayed", actor_role,
                        record["status"], record["status"], record["fingerprint"],
                        {
                            "idempotency_key_present": True,
                            **({"policy_hash": policy_hash} if policy_hash else {}),
                        },
                    )
                    db.execute("COMMIT")
                    return "replayed", record

            # Idempotency replays validate the prior record first so proposal
            # tampering remains a conflict (409); new reservations fail closed.
            self._assert_accounting_integrity(
                db, int(proposal["chain_id"]), str(proposal["token_address"])
            )
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
                    idempotency_key, chain_id, token_address, amount_base_units,
                    recipient_normalized, created_day
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, date('now'))""",
                (
                    record["transaction_id"], record["fingerprint"],
                    json.dumps(record["proposal"], sort_keys=True),
                    record["policy_decision"], json.dumps(record["policy_reasons"]),
                    record["risk_score"], record["risk_level"], json.dumps(record["risk_reasons"]),
                    record["status"], idempotency_key, int(proposal["chain_id"]),
                    str(proposal["token_address"]).strip().lower(), int(proposal["amount_base_units"]),
                    str(proposal["recipient"]).strip().lower(),
                ),
            )
            self._append_audit(
                db, record["transaction_id"], "proposal_created", actor_role,
                None, record["status"], record["fingerprint"],
                {
                    "policy_decision": record["policy_decision"],
                    "risk_level": record["risk_level"],
                    "idempotency_key_present": bool(idempotency_key),
                    **(
                        {"policy_hash": record.get("policy_hash", policy_hash)}
                        if record.get("policy_hash", policy_hash)
                        else {}
                    ),
                },
            )
            db.execute("COMMIT")
        return "created", self.get(record["transaction_id"]) or record

    def create(self, record: dict[str, Any], actor_role: str = "system") -> None:
        proposal = record["proposal"]
        executed_at = _utc_now().isoformat() if record["status"] == "executed_simulated" else None
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO transactions (
                    transaction_id, fingerprint, proposal_json, policy_decision,
                    policy_reasons_json, risk_score, risk_level, risk_reasons_json, status,
                    chain_id, token_address, amount_base_units, executed_at, executed_day,
                    recipient_normalized, created_day
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, date(?), ?, date('now'))""",
                (
                    record["transaction_id"], record["fingerprint"],
                    json.dumps(proposal, sort_keys=True), record["policy_decision"],
                    json.dumps(record["policy_reasons"]), record["risk_score"],
                    record["risk_level"], json.dumps(record["risk_reasons"]), record["status"],
                    int(proposal.get("chain_id", 0)), str(proposal.get("token_address", "")).strip().lower(),
                    int(proposal.get("amount_base_units", 0)),
                    executed_at, executed_at, str(proposal.get("recipient", "")).strip().lower(),
                ),
            )
            self._append_audit(
                db, record["transaction_id"], "proposal_created", actor_role,
                None, record["status"], record["fingerprint"],
                {
                    "policy_decision": record["policy_decision"],
                    "risk_level": record["risk_level"],
                    **(
                        {"policy_hash": record["policy_hash"]}
                        if record.get("policy_hash")
                        else {}
                    ),
                },
            )
            db.execute("COMMIT")

    def daily_spend(self, chain_id: int, token_address: str,
                    exclude_transaction_id: str | None = None) -> int:
        with closing(self._connect()) as db:
            return self._daily_spend(db, chain_id, token_address, exclude_transaction_id)

    def get(self, transaction_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)).fetchone()
        return self._decode(row) if row else None

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the newest persisted workflow records for owner-facing inspection."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM transactions ORDER BY created_at DESC, transaction_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def approve(
        self, transaction_id: str, fingerprint: str, actor_role: str = "owner",
        approval_expires_seconds: int = 300,
    ) -> tuple[str, dict[str, Any] | None]:
        with closing(self._connect()) as db:
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
            self._validate_accounting_consistency(record)
            if record["fingerprint"] != fingerprint:
                db.execute("COMMIT")
                return "fingerprint_mismatch", record
            if record["status"] == "blocked":
                db.execute("COMMIT")
                return "blocked", record
            if record["status"] == "executed_simulated":
                db.execute("COMMIT")
                return "already_executed", record
            if record["status"] == "submitted_onchain":
                db.execute("COMMIT")
                return "already_submitted", record
            if record["status"] == "approved":
                now = _utc_now()
                approved_at = record.get("approved_at")
                expired = _approval_expired(approved_at, now, approval_expires_seconds)
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

    def cancel(
        self, transaction_id: str, actor_role: str = "owner"
    ) -> tuple[str, dict[str, Any] | None]:
        """Cancel a pending proposal and release its daily spend reservation."""
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)
            ).fetchone()
            if row is None:
                db.execute("COMMIT")
                return "not_found", None
            record = self._decode(row)
            if record["status"] == "cancelled":
                db.execute("COMMIT")
                return "already_cancelled", record
            if record["status"] not in {"ready", "awaiting_approval", "approved"}:
                db.execute("COMMIT")
                return "invalid_state", record
            db.execute(
                """UPDATE transactions SET status = 'cancelled',
                   approval_fingerprint = NULL, approved_at = NULL,
                   updated_at = CURRENT_TIMESTAMP WHERE transaction_id = ?""",
                (transaction_id,),
            )
            self._append_audit(
                db, transaction_id, "transaction_cancelled", actor_role,
                record["status"], "cancelled", record["fingerprint"],
                {"reservation_released": True},
            )
            db.execute("COMMIT")
        return "cancelled", self.get(transaction_id)

    def reject(
        self, transaction_id: str, actor_role: str = "owner"
    ) -> tuple[str, dict[str, Any] | None]:
        """Reject a pending proposal and release its daily spend reservation."""
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)
            ).fetchone()
            if row is None:
                db.execute("COMMIT")
                return "not_found", None
            record = self._decode(row)
            if record["status"] == "rejected":
                db.execute("COMMIT")
                return "already_rejected", record
            if record["status"] not in {"ready", "awaiting_approval", "approved"}:
                db.execute("COMMIT")
                return "invalid_state", record
            db.execute(
                """UPDATE transactions SET status = 'rejected',
                   approval_fingerprint = NULL, approved_at = NULL,
                   updated_at = CURRENT_TIMESTAMP WHERE transaction_id = ?""",
                (transaction_id,),
            )
            self._append_audit(
                db, transaction_id, "transaction_rejected", actor_role,
                record["status"], "rejected", record["fingerprint"],
                {"reservation_released": True},
            )
            db.execute("COMMIT")
        return "rejected", self.get(transaction_id)

    def execute_simulated(
        self,
        transaction_id: str,
        policy: Any,
        actor_role: str = "owner",
        policy_hash: str | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        return self._execute_with_validation(
            transaction_id,
            policy,
            actor_role,
            policy_hash=policy_hash,
        )

    def execute_onchain(
        self,
        transaction_id: str,
        policy: Any,
        prepare: Callable[[dict[str, Any]], tuple[str, str]],
        broadcast: Callable[[str], str],
        actor_role: str = "owner",
        policy_hash: str | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        from app.policy.canonical import hash_policy as compute_policy_hash

        active_policy_hash = policy_hash or compute_policy_hash(policy)
        pending = self._pending_submission(transaction_id)
        if pending is not None:
            status, raw_transaction, transaction_hash, record = pending
            if status == "not_found":
                return "not_found", None
            if status == "submitting_onchain":
                return self._broadcast_pending(
                    transaction_id,
                    raw_transaction,
                    transaction_hash,
                    broadcast,
                    active_policy_hash,
                )
            if status == "submitted_onchain":
                return "already_submitted", record
        return self._execute_with_validation(
            transaction_id,
            policy,
            actor_role,
            prepare,
            broadcast,
            active_policy_hash,
        )

    def _pending_submission(
        self, transaction_id: str
    ) -> tuple[str, str, str, dict[str, Any]] | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)
            ).fetchone()
        if row is None:
            return ("not_found", "", "", {})
        record = self._decode(row)
        if record["status"] not in {"submitting_onchain", "submitted_onchain"}:
            return None
        self._validate_accounting_consistency(record)
        if _proposal_fingerprint(record["proposal"]) != record["fingerprint"]:
            raise WorkflowDataError("Stored workflow data is invalid")
        raw_transaction = row["signed_transaction"]
        transaction_hash = record["execution_reference"]
        if (
            record["status"] == "submitting_onchain"
            and (not isinstance(raw_transaction, str) or not raw_transaction.startswith("0x"))
        ) or not isinstance(transaction_hash, str):
            raise WorkflowDataError("Stored workflow data is invalid")
        return record["status"], raw_transaction or "", transaction_hash, record

    def _broadcast_pending(
        self,
        transaction_id: str,
        raw_transaction: str,
        transaction_hash: str,
        broadcast: Callable[[str], str],
        policy_hash: str | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        returned_hash = broadcast(raw_transaction)
        if returned_hash.lower() != transaction_hash.lower():
            raise OnchainSubmissionError("RPC returned a different transaction hash")
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status, fingerprint, execution_reference FROM transactions "
                "WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
            if row is None:
                db.execute("ROLLBACK")
                raise WorkflowDataError("Stored workflow data is invalid")
            if (
                row["status"] == "submitted_onchain"
                and row["execution_reference"] == transaction_hash
            ):
                db.execute("COMMIT")
                return "already_submitted", self.get(transaction_id)
            if (
                row["status"] != "submitting_onchain"
                or row["execution_reference"] != transaction_hash
            ):
                db.execute("ROLLBACK")
                raise WorkflowDataError("Stored workflow data is invalid")
            executed_at = _utc_now().isoformat()
            db.execute(
                """UPDATE transactions SET status = 'submitted_onchain',
                   signed_transaction = NULL, executed_at = ?, executed_day = date(?),
                   updated_at = CURRENT_TIMESTAMP WHERE transaction_id = ?""",
                (executed_at, executed_at, transaction_id),
            )
            self._append_audit(
                db, transaction_id, "onchain_transaction_submitted", "owner",
                "submitting_onchain", "submitted_onchain", row["fingerprint"],
                {
                    "transaction_hash": transaction_hash,
                    **({"policy_hash": policy_hash} if policy_hash else {}),
                },
            )
            db.execute("COMMIT")
        return "submitted", self.get(transaction_id)

    def _execute_with_validation(
        self,
        transaction_id: str,
        policy: Any,
        actor_role: str = "owner",
        prepare_onchain: Callable[[dict[str, Any]], tuple[str, str]] | None = None,
        broadcast_onchain: Callable[[str], str] | None = None,
        policy_hash: str | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Revalidates policy and spend inside the same write lock as execution."""
        from app.policy.canonical import hash_policy as compute_policy_hash
        from app.policy.engine import evaluate_transaction
        from app.policy.models import PolicyDecision, TransactionProposal
        from app.risk.engine import assess_transaction_risk
        from app.risk.models import RiskLevel

        active_policy_hash = policy_hash or compute_policy_hash(policy)

        with closing(self._connect()) as db:
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
                    {
                        "operation": "execute",
                        "reason": "persisted proposal does not match fingerprint",
                        "policy_hash": active_policy_hash,
                    },
                )
                db.execute("COMMIT")
                return "proposal_integrity_mismatch", record
            self._validate_accounting_consistency(record)
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
                expired = _approval_expired(
                    approved_at, now, policy.approval_expires_seconds
                )
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
                            "policy_hash": active_policy_hash,
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
                    {
                        "policy_reasons": result.reasons,
                        "policy_hash": active_policy_hash,
                    },
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
                     "risk_reasons": risk_result.reasons,
                     "policy_hash": active_policy_hash},
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
                        {
                            "policy_reasons": result.reasons,
                            "policy_hash": active_policy_hash,
                        },
                    )
                    db.execute("COMMIT")
                    return "approval_required", self.get(transaction_id)
            # A stored risk-based approval requirement is still enforced by the state machine.
            if record["status"] == "awaiting_approval":
                db.execute("COMMIT")
                return "approval_required", record

            if prepare_onchain is not None or broadcast_onchain is not None:
                if prepare_onchain is None or broadcast_onchain is None:
                    raise OnchainSubmissionError("Onchain signing and submission must both be configured")
                raw_transaction, transaction_hash = prepare_onchain(record["proposal"])
                if (
                    not isinstance(raw_transaction, str)
                    or not raw_transaction.startswith("0x")
                    or not isinstance(transaction_hash, str)
                    or len(transaction_hash) != 66
                    or not transaction_hash.startswith("0x")
                ):
                    raise OnchainSubmissionError("Signed transaction data is invalid")
                db.execute(
                    """UPDATE transactions SET status = 'submitting_onchain',
                       execution_reference = ?, signed_transaction = ?,
                       updated_at = CURRENT_TIMESTAMP WHERE transaction_id = ?""",
                    (transaction_hash, raw_transaction, transaction_id),
                )
                self._append_audit(
                    db, transaction_id, "onchain_transaction_signed", actor_role,
                    record["status"], "submitting_onchain", record["fingerprint"],
                    {
                        "transaction_hash": transaction_hash,
                        "policy_hash": active_policy_hash,
                    },
                )
                db.execute("COMMIT")
                pending = (raw_transaction, transaction_hash)
            else:
                reference = "simulated:" + record["fingerprint"][:24]
                executed_at = _utc_now().isoformat()
                db.execute(
                    """UPDATE transactions SET status = 'executed_simulated',
                       execution_reference = ?, executed_at = ?, executed_day = date(?),
                       updated_at = CURRENT_TIMESTAMP WHERE transaction_id = ?""",
                    (reference, executed_at, executed_at, transaction_id),
                )
                self._append_audit(
                    db, transaction_id, "simulated_execution", actor_role,
                    record["status"], "executed_simulated", record["fingerprint"],
                    {
                        "execution_reference": reference,
                        "policy_hash": active_policy_hash,
                    },
                )
                db.execute("COMMIT")
                pending = None
        if pending is not None and broadcast_onchain is not None:
            return self._broadcast_pending(
                transaction_id,
                pending[0],
                pending[1],
                broadcast_onchain,
                active_policy_hash,
            )
        return "executed", self.get(transaction_id)

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        required_string_fields = (
            "transaction_id", "fingerprint", "created_at", "updated_at",
            "policy_decision", "risk_level", "status",
        )
        nullable_string_fields = (
            "approval_fingerprint", "approved_at", "execution_reference", "executed_at"
        )
        if (
            any(not isinstance(record.get(name), str) for name in required_string_fields)
            or any(
                record.get(name) is not None and not isinstance(record.get(name), str)
                for name in nullable_string_fields
            )
        ):
            raise WorkflowDataError("Stored workflow data is invalid")
        if (
            record["policy_decision"] not in {"allow", "require_approval", "block"}
            or record["risk_level"] not in {"low", "medium", "high"}
            or record["status"] not in {
                "blocked", "ready", "awaiting_approval", "approved", "cancelled",
                "rejected", "executed_simulated", "submitting_onchain",
                "submitted_onchain",
            }
            or type(record.get("risk_score")) is not int
            or not 0 <= record["risk_score"] <= 100
        ):
            raise WorkflowDataError("Stored workflow data is invalid")
        try:
            proposal = json.loads(record.pop("proposal_json"))
            policy_reasons = json.loads(record.pop("policy_reasons_json"))
            risk_reasons = json.loads(record.pop("risk_reasons_json"))
        except (TypeError, ValueError) as exc:
            raise WorkflowDataError("Stored workflow data is invalid") from exc
        if (
            not isinstance(proposal, dict)
            or not isinstance(policy_reasons, list)
            or not all(isinstance(reason, str) for reason in policy_reasons)
            or not isinstance(risk_reasons, list)
            or not all(isinstance(reason, str) for reason in risk_reasons)
        ):
            raise WorkflowDataError("Stored workflow data is invalid")
        string_fields = ("agent_id", "token_symbol", "token_address", "recipient")
        integer_fields = ("chain_id", "amount_base_units", "token_decimals")
        if any(not isinstance(proposal.get(name), str) for name in string_fields):
            raise WorkflowDataError("Stored workflow data is invalid")
        # bool is an int subclass in Python, so use exact type checks for persisted integers.
        if any(type(proposal.get(name)) is not int for name in integer_fields):
            raise WorkflowDataError("Stored workflow data is invalid")
        if (
            not 0 < proposal["chain_id"] <= MAX_SQLITE_INTEGER
            or not 0 < proposal["amount_base_units"] <= MAX_SQLITE_INTEGER
            or not 0 <= proposal["token_decimals"] <= 36
        ):
            raise WorkflowDataError("Stored workflow data is invalid")
        try:
            from app.policy.models import TransactionProposal

            TransactionProposal(**proposal)
        except (TypeError, ValueError) as exc:
            raise WorkflowDataError("Stored workflow data is invalid") from exc
        record["proposal"] = proposal
        record["policy_reasons"] = policy_reasons
        record["risk_reasons"] = risk_reasons
        signed_transaction = record.pop("signed_transaction", None)
        if (
            record["status"] == "submitting_onchain"
            and (not isinstance(signed_transaction, str) or not signed_transaction.startswith("0x"))
        ) or (
            record["status"] != "submitting_onchain" and signed_transaction is not None
        ):
            raise WorkflowDataError("Stored workflow data is invalid")
        return record


def _utc_now() -> datetime:
    """Clock seam for deterministic approval-expiry tests."""
    return datetime.now(UTC)
