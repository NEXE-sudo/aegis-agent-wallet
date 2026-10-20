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
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from agent.autonomy import Mandate, Payee, PayeeHistory, PaymentTask, decide_payments
from app.auth import require_agent, require_owner
from app.policy.models import TransactionProposal


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
                    payload_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'decided')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS governance_decisions (
                    decision_id TEXT PRIMARY KEY,
                    sequence INTEGER UNIQUE,
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
            # Additive migration for local databases created by earlier branch revisions.
            db.execute("DROP TRIGGER IF EXISTS governance_decisions_no_update")
            db.execute("DROP TRIGGER IF EXISTS governance_decisions_no_delete")
            decision_columns = {row["name"] for row in db.execute("PRAGMA table_info(governance_decisions)")}
            if "sequence" not in decision_columns:
                db.execute("ALTER TABLE governance_decisions ADD COLUMN sequence INTEGER")
                old_rows = db.execute(
                    "SELECT decision_id FROM governance_decisions ORDER BY created_at, decision_id"
                ).fetchall()
                for index, row in enumerate(old_rows, start=1):
                    db.execute(
                        "UPDATE governance_decisions SET sequence = ? WHERE decision_id = ?",
                        (index, row["decision_id"]),
                    )
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_governance_decision_sequence ON governance_decisions(sequence)")
            task_columns = {row["name"] for row in db.execute("PRAGMA table_info(governance_tasks)")}
            if "payload_hash" not in task_columns:
                db.execute("ALTER TABLE governance_tasks ADD COLUMN payload_hash TEXT")
                task_rows = db.execute("SELECT task_id, payload_json FROM governance_tasks").fetchall()
                for row in task_rows:
                    task_payload = json.loads(row["payload_json"])
                    db.execute(
                        "UPDATE governance_tasks SET payload_hash = ? WHERE task_id = ?",
                        (payload_hash(task_payload), row["task_id"]),
                    )
            db.executescript("""
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
                       (task_id, payload_json, payload_hash, status, created_at, updated_at)
                       VALUES (?, ?, ?, 'pending', ?, ?)""",
                    (task_id, canonical_json(stored), digest, now, now),
                )
                self._append_audit(db, "task_created", task_id, digest, now)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {**stored, "status": "pending"}

    def list_tasks(self, pending_only: bool = False) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            sql = "SELECT task_id, payload_json, payload_hash, status FROM governance_tasks"
            if pending_only:
                sql += " WHERE status = 'pending'"
            sql += " ORDER BY created_at, task_id"
            rows = db.execute(sql).fetchall()
        result = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload_hash(payload) != row["payload_hash"]:
                raise GovernanceDataError("Stored task hash does not match its payload")
            result.append({**payload, "status": row["status"]})
        return result

    def get_decision(self, task_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT decision_json, payload_hash FROM governance_decisions WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        decision = json.loads(row["decision_json"])
        if payload_hash(decision) != row["payload_hash"]:
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
                    "SELECT sequence, record_hash FROM governance_decisions ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
                previous = previous_row["record_hash"] if previous_row else "0" * 64
                sequence = (previous_row["sequence"] if previous_row else 0) + 1
                record = {
                    "decision_id": str(uuid.uuid4()),
                    "sequence": sequence,
                    "task_id": task_id,
                    "decision": decision,
                    "payload_hash": digest,
                    "previous_hash": previous,
                    "created_at": now,
                }
                record_hash = payload_hash(record)
                db.execute(
                    """INSERT INTO governance_decisions
                       (decision_id, sequence, task_id, decision_json, payload_hash, previous_hash, record_hash, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (record["decision_id"], sequence, task_id, canonical_json(decision), digest, previous, record_hash, now),
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
                """SELECT t.payload_json, t.payload_hash, t.status, d.decision_json, d.created_at
                   FROM governance_tasks t LEFT JOIN governance_decisions d ON d.task_id = t.task_id"""
            ).fetchall()
        for row in rows:
            task = json.loads(row["payload_json"])
            if payload_hash(task) != row["payload_hash"]:
                raise GovernanceDataError("Stored task hash does not match its payload")
            if task.get("payee_id") != payee_id:
                continue
            decision = json.loads(row["decision_json"]) if row["decision_json"] else None
            if decision is None:
                continue
            invoice_ids.add(task["invoice_id"])
            if decision.get("action") == "propose" and type(decision.get("amount")) is int:
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
            events = db.execute(
                "SELECT sequence, event_type, entity_id, payload_hash, previous_hash, record_hash, created_at "
                "FROM governance_audit ORDER BY sequence"
            ).fetchall()
            decisions = db.execute(
                "SELECT sequence, decision_id, task_id, decision_json, payload_hash, previous_hash, record_hash, created_at "
                "FROM governance_decisions ORDER BY sequence"
            ).fetchall()
            mandates = db.execute(
                "SELECT mandate_id, payload_json, payload_hash FROM governance_mandates"
            ).fetchall()
            payees = db.execute(
                "SELECT payee_id, payload_json, payload_hash FROM governance_payees"
            ).fetchall()
            tasks = db.execute(
                "SELECT task_id, payload_json, payload_hash FROM governance_tasks"
            ).fetchall()
        previous = "0" * 64
        latest_event_hash: dict[tuple[str, str], str] = {}
        for row in events:
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
            latest_event_hash[(row["event_type"], row["entity_id"])] = row["payload_hash"]

        for row in mandates:
            if (
                payload_hash(json.loads(row["payload_json"])) != row["payload_hash"]
                or latest_event_hash.get(("mandate_saved", row["mandate_id"])) != row["payload_hash"]
            ):
                return {"valid": False, "events_checked": len(events), "reason": "mandate_integrity"}
        for row in payees:
            if (
                payload_hash(json.loads(row["payload_json"])) != row["payload_hash"]
                or latest_event_hash.get(("payee_saved", row["payee_id"])) != row["payload_hash"]
            ):
                return {"valid": False, "events_checked": len(events), "reason": "payee_integrity"}
        for row in tasks:
            if (
                payload_hash(json.loads(row["payload_json"])) != row["payload_hash"]
                or latest_event_hash.get(("task_created", row["task_id"])) != row["payload_hash"]
            ):
                return {"valid": False, "events_checked": len(events), "reason": "task_integrity"}

        decision_previous = "0" * 64
        for index, row in enumerate(decisions, start=1):
            decision = json.loads(row["decision_json"])
            record = {
                "decision_id": row["decision_id"],
                "sequence": row["sequence"],
                "task_id": row["task_id"],
                "decision": decision,
                "payload_hash": row["payload_hash"],
                "previous_hash": row["previous_hash"],
                "created_at": row["created_at"],
            }
            if (
                payload_hash(decision) != row["payload_hash"]
                or row["previous_hash"] != decision_previous
                or payload_hash(record) != row["record_hash"]
                or latest_event_hash.get(("decision_recorded", row["task_id"])) != row["payload_hash"]
            ):
                return {
                    "valid": False,
                    "events_checked": len(events),
                    "decisions_checked": index - 1,
                    "reason": "decision_integrity",
                }
            decision_previous = row["record_hash"]
        return {
            "valid": True,
            "events_checked": len(events),
            "decisions_checked": len(decisions),
            "head_hash": previous,
            "decision_head_hash": decision_previous,
        }


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


class MandateInput(BaseModel):
    mandate_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    allowed_categories: list[str] = Field(min_length=1, max_length=32)
    allowed_tokens: list[str] = Field(min_length=1, max_length=1)
    monthly_budget_base_units: int = Field(gt=0, le=2**63 - 1)
    per_payment_limit_base_units: int = Field(gt=0, le=2**63 - 1)
    approval_threshold_base_units: int = Field(ge=0, le=2**63 - 1)
    treasury_buffer_base_units: int = Field(ge=0, le=2**63 - 1)
    # Owner-maintained planning value, not a live chain balance.
    available_balance_base_units: int = Field(ge=0, le=2**63 - 1)

    @field_validator("allowed_categories")
    @classmethod
    def validate_categories(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().lower() for value in values]
        if any(not value or len(value) > 64 for value in normalized):
            raise ValueError("Categories must be non-empty and at most 64 characters")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Categories must be unique")
        return normalized

    @field_validator("allowed_tokens")
    @classmethod
    def validate_tokens(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().upper() for value in values]
        if any(not value or len(value) > 16 for value in normalized):
            raise ValueError("Token symbols must be non-empty and at most 16 characters")
        return normalized


class PayeeInput(BaseModel):
    payee_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=128)
    address: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    chain_id: Literal[84532]
    token_address: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    token_symbol: str = Field(min_length=1, max_length=16)
    token_decimals: int = Field(ge=0, le=36)
    category: str = Field(min_length=1, max_length=64)
    per_tx_limit_base_units: int = Field(gt=0, le=2**63 - 1)
    daily_limit_base_units: int = Field(gt=0, le=2**63 - 1)
    verified: bool = False

    @field_validator("address", "token_address")
    @classmethod
    def lowercase_addresses(cls, value: str) -> str:
        return value.lower()

    @field_validator("name", "category", "token_symbol")
    @classmethod
    def normalize_text(cls, value: str, info) -> str:
        value = value.strip()
        if info.field_name == "token_symbol":
            return value.upper()
        if info.field_name == "category":
            return value.lower()
        return value


class TaskInput(BaseModel):
    task_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    invoice_id: str = Field(min_length=1, max_length=128)
    payee_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    token_symbol: str = Field(min_length=1, max_length=16)
    amount_base_units: int = Field(gt=0, le=2**63 - 1)
    due_at: datetime | None = None
    description: str = Field(default="", max_length=4000)

    @field_validator("invoice_id", "payee_id", "token_symbol")
    @classmethod
    def normalize_task_fields(cls, value: str, info) -> str:
        value = value.strip()
        return value.upper() if info.field_name == "token_symbol" else value

    @field_validator("due_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("due_at must include a timezone")
        return value


router = APIRouter(tags=["owner governance"])


@router.get("/governance/mandate", dependencies=[Depends(require_owner)])
def read_active_mandate(request: Request) -> dict[str, Any]:
    record = request.app.state.governance_store.get_active_mandate()
    if record is None:
        raise HTTPException(status_code=404, detail="No active mandate is configured")
    return record


@router.put("/governance/mandate", dependencies=[Depends(require_owner)])
def save_active_mandate(payload: MandateInput, request: Request) -> dict[str, Any]:
    policy = request.app.state.controller.policy
    if not set(payload.allowed_tokens).issubset(policy.allowed_token_symbols):
        raise HTTPException(status_code=422, detail="Mandate tokens must be allowed by the active wallet policy")
    return request.app.state.governance_store.put_mandate(payload.model_dump())


@router.get("/governance/payees", dependencies=[Depends(require_owner)])
def read_payees(request: Request) -> list[dict[str, Any]]:
    return request.app.state.governance_store.list_payees()


@router.put("/governance/payees/{payee_id}", dependencies=[Depends(require_owner)])
def save_payee(payee_id: str, payload: PayeeInput, request: Request) -> dict[str, Any]:
    if payee_id != payload.payee_id:
        raise HTTPException(status_code=422, detail="Path payee_id must match the payload")
    policy = request.app.state.controller.policy
    trusted = policy.trusted_tokens.get(payload.token_address)
    if (
        payload.chain_id not in policy.allowed_chain_ids
        or payload.token_address not in policy.allowed_token_addresses
        or trusted is None
        or trusted.symbol != payload.token_symbol
        or trusted.decimals != payload.token_decimals
    ):
        raise HTTPException(status_code=422, detail="Payee token metadata must match an allowed trusted token")
    return request.app.state.governance_store.put_payee(payload.model_dump())


@router.post("/governance/tasks", status_code=201, dependencies=[Depends(require_owner)])
def create_governance_task(payload: TaskInput, request: Request) -> dict[str, Any]:
    payee = request.app.state.governance_store.get_payee(payload.payee_id)
    if payee and payload.token_symbol != payee["token_symbol"]:
        raise HTTPException(status_code=422, detail="Task token must match the registered payee token")
    return request.app.state.governance_store.create_task(payload.model_dump(mode="json"))


@router.get("/governance/tasks", dependencies=[Depends(require_owner)])
def read_governance_tasks(request: Request, pending_only: bool = False) -> list[dict[str, Any]]:
    return request.app.state.governance_store.list_tasks(pending_only=pending_only)


@router.get("/governance/audit/verify", dependencies=[Depends(require_owner)])
def verify_governance_audit(request: Request) -> dict[str, Any]:
    return request.app.state.governance_store.verify_audit_chain()


@router.post("/agent/governance/decide-pending", dependencies=[Depends(require_agent)])
def decide_pending_governance_tasks(request: Request) -> dict[str, Any]:
    governance = request.app.state.governance_store
    controller = request.app.state.controller
    try:
        evaluated = evaluate_pending_tasks(governance)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    results: list[dict[str, Any]] = []
    payees = {item["payee_id"]: item for item in governance.list_payees()}
    for item in evaluated:
        task = item.pop("task")
        task_id = task["task_id"]
        proposal_record = None
        if item["action"] == "propose":
            payee = payees.get(item["payee_id"])
            if payee is None:
                item["action"] = "escalate"
                item["amount"] = None
                item["rationale"] = "Payee registry changed during evaluation; owner review required."
                item["evidence_refs"] = list(item["evidence_refs"]) + ["registry:changed_during_evaluation"]
            else:
                proposal = TransactionProposal(
                    agent_id=controller.policy.agent_id,
                    chain_id=payee["chain_id"],
                    token_symbol=payee["token_symbol"],
                    token_address=payee["token_address"],
                    recipient=payee["address"],
                    amount_base_units=item["amount"],
                    token_decimals=payee["token_decimals"],
                )
                _, proposal_record = controller.propose(
                    proposal, idempotency_key=f"governance-task-{task_id}"
                )
        stored = {
            **item,
            "transaction_id": proposal_record["transaction_id"] if proposal_record else None,
            "transaction_status": proposal_record["status"] if proposal_record else None,
        }
        stored_record = governance.record_decision(task_id, stored)
        results.append(stored_record)
    return {"processed": len(results), "decisions": results}
