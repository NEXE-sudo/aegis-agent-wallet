"""Regression tests for additive SQLite workflow-store migrations."""

import json
import sqlite3

from app.workflow.store import WorkflowStore


def test_legacy_database_migration_preserves_transaction_and_backfills_accounting(tmp_path):
    db_path = tmp_path / "legacy-workflow.sqlite3"
    proposal = {
        "agent_id": "devops-01",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": "0xAbCd000000000000000000000000000000000123",
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 1_250_000,
        "token_decimals": 6,
    }

    # This is the pre-accounting schema: it has no idempotency, approval timestamp,
    # chain, token-address, or amount columns.
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE transactions (
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
            """
        )
        db.execute(
            """
            INSERT INTO transactions (
                transaction_id, fingerprint, proposal_json, policy_decision,
                policy_reasons_json, risk_score, risk_level, risk_reasons_json,
                status, approval_fingerprint, execution_reference
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-transaction",
                "a" * 64,
                json.dumps(proposal),
                "allow",
                json.dumps(["legacy policy result"]),
                12,
                "low",
                json.dumps(["legacy risk result"]),
                "approved",
                "a" * 64,
                None,
            ),
        )

    store = WorkflowStore(db_path)

    record = store.get("legacy-transaction")
    assert record is not None
    assert record["transaction_id"] == "legacy-transaction"
    assert record["fingerprint"] == "a" * 64
    assert record["proposal"] == proposal
    assert record["policy_decision"] == "allow"
    assert record["policy_reasons"] == ["legacy policy result"]
    assert record["risk_score"] == 12
    assert record["risk_level"] == "low"
    assert record["risk_reasons"] == ["legacy risk result"]
    assert record["status"] == "approved"
    assert record["approval_fingerprint"] == "a" * 64

    with sqlite3.connect(db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(transactions)")}
        accounting = db.execute(
            """
            SELECT chain_id, token_address, amount_base_units, idempotency_key, approved_at
            FROM transactions WHERE transaction_id = ?
            """,
            ("legacy-transaction",),
        ).fetchone()

    assert {
        "idempotency_key", "approved_at", "chain_id", "token_address", "amount_base_units"
    } <= columns
    assert accounting == (
        84532,
        proposal["token_address"].lower(),
        1_250_000,
        None,
        None,
    )
    assert store.daily_spend(84532, proposal["token_address"]) == 1_250_000
