"""Regression tests for Unicode-safe accounting recipient normalization."""

import json

from app.chain.base_sepolia import BASE_SEPOLIA_USDC_ADDRESS
from app.workflow.store import WorkflowStore

CHAIN_ID = 84532
RECIPIENT = "0x2222222222222222222222222222222222222222"


def _record(transaction_id: str) -> dict:
    proposal = {
        "agent_id": "devops-01",
        "chain_id": CHAIN_ID,
        "token_symbol": "USDC",
        "token_address": BASE_SEPOLIA_USDC_ADDRESS,
        "recipient": RECIPIENT,
        "amount_base_units": 100,
        "token_decimals": 6,
    }
    return {
        "transaction_id": transaction_id,
        "fingerprint": "a" * 64,
        "proposal": proposal,
        "policy_decision": "allow",
        "policy_reasons": [],
        "risk_score": 0,
        "risk_level": "low",
        "risk_reasons": [],
        "status": "ready",
    }


def test_startup_recanonicalizes_legacy_unicode_whitespace_recipient(tmp_path):
    db_path = tmp_path / "legacy-recipient.sqlite3"
    store = WorkflowStore(db_path)
    store.create(_record("legacy-recipient"))
    padded_recipient = f"\u2003{RECIPIENT}\t\n"
    with store._connect() as db:
        db.execute("DROP TRIGGER transactions_accounting_integrity_guard")
        proposal = json.loads(
            db.execute(
                "SELECT proposal_json FROM transactions WHERE transaction_id = ?",
                ("legacy-recipient",),
            ).fetchone()[0]
        )
        proposal["recipient"] = padded_recipient
        db.execute(
            """UPDATE transactions
               SET proposal_json = ?, recipient_normalized = ?, accounting_integrity = 0
               WHERE transaction_id = ?""",
            (
                json.dumps(proposal, sort_keys=True, separators=(",", ":")),
                RECIPIENT,
                "legacy-recipient",
            ),
        )

    # Reopening runs the upgrade migration and recomputes the derived index and flag.
    upgraded = WorkflowStore(db_path)
    with upgraded._connect() as db:
        row = db.execute(
            """SELECT recipient_normalized, accounting_integrity
               FROM transactions WHERE transaction_id = ?""",
            ("legacy-recipient",),
        ).fetchone()
    assert row["recipient_normalized"] == RECIPIENT
    assert row["accounting_integrity"] == 1


def test_trigger_uses_unicode_aware_normalization_and_can_clear_flag(tmp_path):
    store = WorkflowStore(tmp_path / "recipient-trigger.sqlite3")
    store.create(_record("recipient-trigger"))
    padded_recipient = f"\u2003{RECIPIENT}\t\n"
    with store._connect() as db:
        proposal = json.loads(
            db.execute(
                "SELECT proposal_json FROM transactions WHERE transaction_id = ?",
                ("recipient-trigger",),
            ).fetchone()[0]
        )
        proposal["recipient"] = padded_recipient
        db.execute(
            """UPDATE transactions
               SET proposal_json = ?, recipient_normalized = ?
               WHERE transaction_id = ?""",
            (
                json.dumps(proposal, sort_keys=True, separators=(",", ":")),
                "incorrect",
                "recipient-trigger",
            ),
        )
        flagged = db.execute(
            "SELECT accounting_integrity FROM transactions WHERE transaction_id = ?",
            ("recipient-trigger",),
        ).fetchone()[0]
    assert flagged == 0

    # Startup migration canonicalizes the derived key and restores the flag.
    repaired_store = WorkflowStore(store.db_path)
    with repaired_store._connect() as db:
        repaired = db.execute(
            "SELECT recipient_normalized, accounting_integrity FROM transactions "
            "WHERE transaction_id = ?",
            ("recipient-trigger",),
        ).fetchone()
    assert repaired["recipient_normalized"] == RECIPIENT
    assert repaired["accounting_integrity"] == 1
