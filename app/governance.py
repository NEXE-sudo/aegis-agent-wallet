"""Persisted owner governance and hash-chained agent decision records.

The agent can process owner-created tasks, but it cannot create or modify mandates,
payees, task payment fields, or governance records. Existing transaction policy and
AegisVault enforcement remain authoritative.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.autonomy import Decision, Mandate, Payee, PayeeHistory, PaymentTask, decide_payments


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    return datetime.now(UTC)


class GovernanceStore:
    """SQLite persistence for mandates, payees, queued tasks, and decision evidence."""

    def __init__(self, db_path: str | Path) -> None:
        raw_path = str(db_path)
        if not raw_path.strip():
            raise ValueError("db_path cannot be empty")
        self.db_path = raw_path if raw_path == ":memory:" else str(Path(raw_path).expanduser())
        if self.db_path != ":memory:":
            Path(self.db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 10000")
        return db

    def _init_db(self) -> None:
        with closing(self._connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS governance_mandates (
                    mandate_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    active INTEGER NOT NULL CHECK (active IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_governance_mandate
                    ON governance_mandates(active) WHERE active = 1;
                CREATE TABLE IF NOT EXISTS governance_payees (
                    payee_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS governance_tasks (
                    task_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'decided')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS governance_decisions (
                    decision_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE REFERENCES governance_tasks(task_id),
                    decision_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    record_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS governance_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    record_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS governance_audit_no_update
                    BEFORE UPDATE ON governance_audit
                    BEGIN SELECT RAISE(ABORT, 'governance audit is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS governance_audit_no_delete
                    BEFORE DELETE ON governance_audit
                    BEGIN SELECT RAISE(ABORT, 'governance audit is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS governance_decisions_no_update
                    BEFORE UPDATE ON governance_decisions
                    BEGIN SELECT RAISE(ABORT, 'governance decisions are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS governance_decisions_no_delete
                    BEFORE DELETE ON governance_decisions
                    BEGIN SELECT RAISE(ABORT, 'governance decisions are append-only'); END;
            """)

    @staticmethod
    def _append_audit(
        db: sqlite3.Connection, event_type: str, entity_id: str, digest: str, created_at: str
    ) -> str:
        row = db.execute(
            "SELECT record_hash FROM governance_audit ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous = row["record_hash"] if row else "0" * 64
        sequence_row = db.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM governance_audit"
        ).fetchone()
        record = {
            "sequence": sequence_row["next_sequence"],
            "event_type": event_type,
            "entity_id": entity_id,
            "payload_hash": digest,
            "previous_hash": previous,
            "created_at": created_at,
        }
        record_digest = payload_hash(record)
        db.execute(
            """INSERT INTO governance_audit
               (sequence, event_type, entity_id, payload_hash, previous_hash, record_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (record["sequence"], event_type, entity_id, digest, previous, record_digest, created_at),
        )
        return record_digest

    def put_mandate(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now().isoformat()
        digest = payload_hash(payload)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("UPDATE governance_mandates SET active = 0 WHERE active = 1")
                db.execute(
                    """INSERT INTO governance_mandates
                       (mandate_id, payload_json, payload_hash, active, created_at, updated_at)
                       VALUES (?, ?, ?, 1, ?, ?)
                       ON CONFLICT(mandate_id) DO UPDATE SET
                       payload_json = excluded.payload_json, payload_hash = excluded.payload_hash,
                       active = 1, updated_at = excluded.updated_at""",
                    (payload["mandate_id"], canonical_json(payload), digest, now, now),
                )
                self._append_audit(db, "mandate_saved", payload["mandate_id"], digest, now)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {**payload, "payload_hash": digest, "updated_at": now}

    def get_active_mandate(self) -> dict[str, Any] | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT payload_json, payload_hash, updated_at FROM governance_mandates WHERE active = 1"
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        if payload_hash(payload) != row["payload_hash"]:
            raise GovernanceDataError("Stored mandate hash does not match its payload")
        return {**payload, "payload_hash": row["payload_hash"], "updated_at": row["updated_at"]}

    def put_payee(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now().isoformat()
        digest = payload_hash(payload)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                previous = db.execute(
                    "SELECT created_at FROM governance_payees WHERE payee_id = ?",
                    (payload["payee_id"],),
                ).fetchone()
                created_at = previous["created_at"] if previous else now
                db.execute(
                    """INSERT INTO governance_payees
                       (payee_id, payload_json, payload_hash, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(payee_id) DO UPDATE SET
                       payload_json = excluded.payload_json, payload_hash = excluded.payload_hash,
                       updated_at = excluded.updated_at""",
                    (payload["payee_id"], canonical_json(payload), digest, created_at, now),
                )
                self._append_audit(db, "payee_saved", payload["payee_id"], digest, now)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {**payload, "payload_hash": digest, "updated_at": now}

    def get_payee(self, payee_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT payload_json, payload_hash, updated_at FROM governance_payees WHERE payee_id = ?",
                (payee_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        if payload_hash(payload) != row["payload_hash"]:
            raise GovernanceDataError("Stored payee hash does not match its payload")
        return {**payload, "payload_hash": row["payload_hash"], "updated_at": row["updated_at"]}

    def list_payees(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT payee_id FROM governance_payees ORDER BY payee_id"
            ).fetchall()
        return [payee for row in rows if (payee := self.get_payee(row["payee_id"])) is not None]

    def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = payload.get("task_id") or str(uuid.uuid4())
        now = utc_now().isoformat()
        stored = {**payload, "task_id": task_id, "created_at": now}
        digest = payload_hash(stored)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    """INSERT INTO governance_tasks
                       (task_id, payload_json, status, created_at, updated_at)
                       VALUES (?, ?, 'pending', ?, ?)""",
                    (task_id, canonical_json(stored), now, now),
                )
                self._append_audit(db, "task_created", task_id, digest, now)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {**stored, "status": "pending"}

    def list_tasks(self, pending_only: bool = False) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            sql = "SELECT task_id, payload_json, status FROM governance_tasks"
            if pending_only:
                sql += " WHERE status = 'pending'"
            sql += " ORDER BY created_at, task_id"
            rows = db.execute(sql).fetchall()
        return [{**json.loads(row["payload_json"]), "status": row["status"]} for row in rows]

    def get_decision(self, task_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT decision_json, record_hash FROM governance_decisions WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        decision = json.loads(row["decision_json"])
        if payload_hash(decision) != row["record_hash"]:
            raise GovernanceDataError("Stored decision hash does not match its payload")
        return decision

    def record_decision(self, task_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        now = utc_now().isoformat()
        digest = payload_hash(decision)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                task_row = db.execute(
                    "SELECT status FROM governance_tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
                if task_row is None:
                    raise KeyError(task_id)
                if task_row["status"] != "pending":
                    existing = db.execute(
                        "SELECT decision_json FROM governance_decisions WHERE task_id = ?",
                        (task_id,),
                    ).fetchone()
                    if existing:
                        db.rollback()
                        return json.loads(existing["decision_json"])
                    raise GovernanceDataError("Task status is inconsistent with decision history")
                previous_row = db.execute(
                    "SELECT record_hash FROM governance_decisions ORDER BY created_at DESC, decision_id DESC LIMIT 1"
                ).fetchone()
                previous = previous_row["record_hash"] if previous_row else "0" * 64
                record = {
                    "decision_id": str(uuid.uuid4()),
                    "task_id": task_id,
                    "decision": decision,
                    "payload_hash": digest,
                    "previous_hash": previous,
                    "created_at": now,
                }
                record_hash = payload_hash(record)
                db.execute(
                    """INSERT INTO governance_decisions
                       (decision_id, task_id, decision_json, payload_hash, previous_hash, record_hash, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (record["decision_id"], task_id, canonical_json(decision), digest, previous, record_hash, now),
                )
                db.execute(
                    "UPDATE governance_tasks SET status = 'decided', updated_at = ? WHERE task_id = ?",
                    (now, task_id),
                )
                self._append_audit(db, "decision_recorded", task_id, digest, now)
                db.commit()
                return {**record, "record_hash": record_hash}
            except Exception:
                db.rollback()
                raise

    def monthly_proposed_spend(self, year_month: str) -> int:
        total = 0
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT decision_json, created_at FROM governance_decisions WHERE substr(created_at, 1, 7) = ?",
                (year_month,),
            ).fetchall()
        for row in rows:
            decision = json.loads(row["decision_json"])
            if decision.get("action") == "propose" and type(decision.get("amount")) is int:
                total += decision["amount"]
        return total

    def history_for_payee(self, payee_id: str, *, day: str) -> PayeeHistory:
        invoice_ids: set[str] = set()
        recent_amounts: list[int] = []
        paid_today = 0
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT t.payload_json, t.status, d.decision_json, d.created_at
                   FROM governance_tasks t LEFT JOIN governance_decisions d ON d.task_id = t.task_id"""
            ).fetchall()
        for row in rows:
            task = json.loads(row["payload_json"])
            if task.get("payee_id") != payee_id:
                continue
            invoice_ids.add(task["invoice_id"])
            decision = json.loads(row["decision_json"]) if row["decision_json"] else None
            if decision and decision.get("action") == "propose" and type(decision.get("amount")) is int:
                recent_amounts.append(decision["amount"])
                if row["created_at"] and row["created_at"][:10] == day:
                    paid_today += decision["amount"]
        return PayeeHistory(
            payee_id=payee_id,
            invoice_ids=frozenset(invoice_ids),
            paid_today=paid_today,
            recent_amounts=tuple(recent_amounts[-10:]),
        )

    def verify_audit_chain(self) -> dict[str, Any]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT sequence, event_type, entity_id, payload_hash, previous_hash, record_hash, created_at "
                "FROM governance_audit ORDER BY sequence"
            ).fetchall()
        previous = "0" * 64
        for row in rows:
            record = {
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "entity_id": row["entity_id"],
                "payload_hash": row["payload_hash"],
                "previous_hash": row["previous_hash"],
                "created_at": row["created_at"],
            }
            if row["previous_hash"] != previous or payload_hash(record) != row["record_hash"]:
                return {"valid": False, "events_checked": row["sequence"] - 1}
            previous = row["record_hash"]
        return {"valid": True, "events_checked": len(rows), "head_hash": previous}


class GovernanceDataError(RuntimeError):
    """Persisted governance data failed an integrity check."""


def evaluate_pending_tasks(store: GovernanceStore, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Evaluate owner-created pending tasks; callers persist results and create proposals."""
    current = now or utc_now()
    mandate_record = store.get_active_mandate()
    if mandate_record is None:
        raise LookupError("No active owner mandate is configured")
    mandate = Mandate(
        mandate_id=mandate_record["mandate_id"],
        allowed_categories=frozenset(mandate_record["allowed_categories"]),
        allowed_tokens=frozenset(mandate_record["allowed_tokens"]),
        monthly_budget=mandate_record["monthly_budget_base_units"],
        per_payment_limit=mandate_record["per_payment_limit_base_units"],
        approval_threshold=mandate_record["approval_threshold_base_units"],
        treasury_buffer=mandate_record["treasury_buffer_base_units"],
    )
    payee_records = {item["payee_id"]: item for item in store.list_payees()}
    payees = {
        payee_id: Payee(
            payee_id=payee_id,
            name=item["name"],
            address=item["address"],
            allowed_tokens=frozenset([item["token_symbol"]]),
            category=item["category"],
            per_tx_limit=item["per_tx_limit_base_units"],
            daily_limit=item["daily_limit_base_units"],
            verified=item["verified"],
        )
        for payee_id, item in payee_records.items()
    }
    pending = store.list_tasks(pending_only=True)
    tasks = [
        PaymentTask(
            task_id=item["task_id"],
            invoice_id=item["invoice_id"],
            payee_id=item["payee_id"],
            token=item["token_symbol"],
            amount=item["amount_base_units"],
            created_at=datetime.fromisoformat(item["created_at"]),
            due_at=datetime.fromisoformat(item["due_at"]) if item.get("due_at") else None,
            description=item.get("description", ""),
        )
        for item in pending
    ]
    histories = {
        payee_id: store.history_for_payee(payee_id, day=current.date().isoformat())
        for payee_id in {task.payee_id for task in tasks}
    }
    return [
        {
            **decision.__dict__,
            "evidence_refs": list(decision.evidence_refs),
            "mandate_id": mandate.mandate_id,
            "mandate_hash": mandate_record["payload_hash"],
            "task": next(item for item in pending if item["task_id"] == decision.task_id),
        }
        for decision in decide_payments(
            tasks,
            mandate,
            payees,
            histories,
            available_balance=mandate_record["available_balance_base_units"],
            reserved_monthly_spend=store.monthly_proposed_spend(current.strftime("%Y-%m")),
            now=current,
        )
    ]
