"""Regression tests for additive SQLite workflow-store migrations."""

import json
import sqlite3

from app.workflow.store import WorkflowDataError, WorkflowStore


def test_migration_skips_malformed_and_non_object_legacy_proposals(tmp_path):
    db_path = tmp_path / "malformed-legacy.sqlite3"
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
        for transaction_id, raw_proposal in [
            ("malformed-json", "{not-json"),
            ("non-object", json.dumps(["not", "an", "object"])),
            ("missing-fields", json.dumps({"agent_id": "devops-01"})),
            (
                "infinite-chain-id",
                json.dumps({
                    "chain_id": float("inf"),
                    "token_address": "0xabcdef",
                    "amount_base_units": 100,
                }),
            ),
            (
                "oversized-amount",
                json.dumps({
                    "chain_id": 84532,
                    "token_address": "0xabcdef",
                    "amount_base_units": 2**100,
                }),
            ),
            (
                "negative-chain-id",
                json.dumps({
                    "agent_id": "devops-01",
                    "chain_id": -1,
                    "token_symbol": "USDC",
                    "token_address": "0xabcdef",
                    "recipient": "0x2222222222222222222222222222222222222222",
                    "amount_base_units": 100,
                    "token_decimals": 6,
                }),
            ),
            (
                "boolean-chain-id",
                json.dumps({
                    "agent_id": "devops-01",
                    "chain_id": True,
                    "token_symbol": "USDC",
                    "token_address": "0xabcdef",
                    "recipient": "0x2222222222222222222222222222222222222222",
                    "amount_base_units": 100,
                    "token_decimals": 6,
                }),
            ),
            (
                "string-amount",
                json.dumps({
                    "agent_id": "devops-01",
                    "chain_id": 84532,
                    "token_symbol": "USDC",
                    "token_address": "0xabcdef",
                    "recipient": "0x2222222222222222222222222222222222222222",
                    "amount_base_units": "100",
                    "token_decimals": 6,
                }),
            ),
            (
                "non-string-token-address",
                json.dumps({
                    "agent_id": "devops-01",
                    "chain_id": 84532,
                    "token_symbol": "USDC",
                    "token_address": {"unexpected": "object"},
                    "recipient": "0x2222222222222222222222222222222222222222",
                    "amount_base_units": 100,
                    "token_decimals": 6,
                }),
            ),
            (
                "zero-amount",
                json.dumps({
                    "agent_id": "devops-01",
                    "chain_id": 84532,
                    "token_symbol": "USDC",
                    "token_address": "0xabcdef",
                    "recipient": "0x2222222222222222222222222222222222222222",
                    "amount_base_units": 0,
                    "token_decimals": 6,
                }),
            ),
        ]:
            db.execute(
                """
                INSERT INTO transactions (
                    transaction_id, fingerprint, proposal_json, policy_decision,
                    policy_reasons_json, risk_score, risk_level, risk_reasons_json, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction_id, "b" * 64, raw_proposal, "allow",
                    "[]", 0, "low", "[]", "ready",
                ),
            )

    # Startup should not crash or fabricate accounting data for unusable proposals.
    store = WorkflowStore(db_path)

    with sqlite3.connect(db_path) as db:
        rows = {
            row[0]: (row[1], row[2], row[3])
            for row in db.execute(
                "SELECT transaction_id, chain_id, token_address, amount_base_units FROM transactions"
            )
        }

    assert rows == {
        "malformed-json": (0, "", 0),
        "non-object": (0, "", 0),
        "missing-fields": (0, "", 0),
        "infinite-chain-id": (0, "", 0),
        "oversized-amount": (0, "", 0),
        "negative-chain-id": (0, "", 0),
        "boolean-chain-id": (0, "", 0),
        "string-amount": (0, "", 0),
        "non-string-token-address": (0, "", 0),
        "zero-amount": (0, "", 0),
    }
    # Migration tolerates legacy rows, while reads reject invalid persisted records.
    for transaction_id in (
        "malformed-json",
        "non-object",
        "missing-fields",
        "infinite-chain-id",
        "oversized-amount",
        "negative-chain-id",
        "boolean-chain-id",
        "string-amount",
        "non-string-token-address",
        "zero-amount",
    ):
        try:
            store.get(transaction_id)
        except WorkflowDataError:
            pass
        else:
            raise AssertionError(f"Expected invalid persisted data for {transaction_id}")


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

    # Reopening an already-migrated database must not alter existing data or schema.
    reopened_store = WorkflowStore(db_path)
    reopened_record = reopened_store.get("legacy-transaction")
    assert reopened_record == record
    assert reopened_store.daily_spend(84532, proposal["token_address"]) == 1_250_000

    with sqlite3.connect(db_path) as db:
        reopened_columns = {
            row[1] for row in db.execute("PRAGMA table_info(transactions)")
        }
        audit_count = db.execute(
            "SELECT COUNT(*) FROM audit_events WHERE transaction_id = ?",
            ("legacy-transaction",),
        ).fetchone()[0]

    assert reopened_columns == columns
    assert audit_count == 0



def test_partial_migration_preserves_existing_columns_and_backfills_missing_accounting(tmp_path):
    db_path = tmp_path / "partially-migrated.sqlite3"
    proposal = {
        "agent_id": "devops-01",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": "0xAbCd000000000000000000000000000000000123",
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 2_500_000,
        "token_decimals": 6,
    }

    # Simulate an interrupted/older migration: idempotency_key and chain_id
    # already exist, while approved_at and the remaining accounting columns do not.
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
                idempotency_key TEXT,
                chain_id INTEGER NOT NULL DEFAULT 0,
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
                status, idempotency_key, chain_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "partial-legacy-transaction",
                "c" * 64,
                json.dumps(proposal),
                "allow",
                json.dumps(["existing policy reason"]),
                7,
                "low",
                json.dumps(["existing risk reason"]),
                "ready",
                "preserve-this-idempotency-key",
                84532,
            ),
        )

    store = WorkflowStore(db_path)
    record = store.get("partial-legacy-transaction")
    assert record is not None
    assert record["transaction_id"] == "partial-legacy-transaction"
    assert record["idempotency_key"] == "preserve-this-idempotency-key"
    assert record["policy_reasons"] == ["existing policy reason"]
    assert record["risk_reasons"] == ["existing risk reason"]
    assert record["proposal"] == proposal

    with sqlite3.connect(db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(transactions)")}
        accounting = db.execute(
            """
            SELECT chain_id, token_address, amount_base_units, idempotency_key, approved_at
            FROM transactions WHERE transaction_id = ?
            """,
            ("partial-legacy-transaction",),
        ).fetchone()
        idempotency_index = db.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'index' AND name = 'idx_transactions_idempotency'
            """
        ).fetchone()

    assert {
        "idempotency_key", "approved_at", "chain_id", "token_address", "amount_base_units"
    } <= columns
    assert accounting == (
        84532,
        proposal["token_address"].lower(),
        2_500_000,
        "preserve-this-idempotency-key",
        None,
    )
    assert idempotency_index is not None
    assert "UNIQUE INDEX" in idempotency_index[0].upper()
    assert store.daily_spend(84532, proposal["token_address"]) == 2_500_000
