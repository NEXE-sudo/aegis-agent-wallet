"""Regression tests for scoped accounting-integrity checks."""

import sqlite3

import pytest

from app.chain.base_sepolia import BASE_SEPOLIA_USDC_ADDRESS
from app.workflow.store import WorkflowDataError, WorkflowStore

CHAIN_ID = 84532
OTHER_CHAIN_ID = 1
RECIPIENT = "0x2222222222222222222222222222222222222222"


def _record(transaction_id: str, status: str = "ready") -> dict:
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
        "status": status,
    }


def _corrupt_amount(store: WorkflowStore, transaction_id: str) -> None:
    with store._connect() as db:
        db.execute(
            "UPDATE transactions SET amount_base_units = amount_base_units + 1 "
            "WHERE transaction_id = ?",
            (transaction_id,),
        )


def test_old_blocked_corrupt_row_does_not_block_current_accounting(tmp_path):
    store = WorkflowStore(tmp_path / "old-blocked-corrupt.sqlite3")
    store.create(_record("old-blocked", status="blocked"))
    with store._connect() as db:
        db.execute(
            """UPDATE transactions
               SET created_at = datetime('now', '-400 days'),
                   created_day = date('now', '-400 days')
               WHERE transaction_id = ?""",
            ("old-blocked",),
        )
    _corrupt_amount(store, "old-blocked")

    with store._connect() as db:
        WorkflowStore._assert_accounting_integrity(
            db, CHAIN_ID, BASE_SEPOLIA_USDC_ADDRESS
        )


def test_corrupt_current_reservation_fails_closed_for_its_chain_and_token(tmp_path):
    store = WorkflowStore(tmp_path / "current-corrupt.sqlite3")
    store.create(_record("current-reservation"))
    _corrupt_amount(store, "current-reservation")

    with store._connect() as db:
        with pytest.raises(WorkflowDataError, match="Stored workflow data is invalid"):
            WorkflowStore._assert_accounting_integrity(
                db, CHAIN_ID, BASE_SEPOLIA_USDC_ADDRESS
            )


def test_original_proposal_keys_catch_corruption_of_denormalized_keys(tmp_path):
    store = WorkflowStore(tmp_path / "key-mismatch.sqlite3")
    store.create(_record("key-mismatch"))
    with store._connect() as db:
        db.execute(
            "UPDATE transactions SET chain_id = ? WHERE transaction_id = ?",
            (OTHER_CHAIN_ID, "key-mismatch"),
        )

    with store._connect() as db:
        with pytest.raises(WorkflowDataError, match="Stored workflow data is invalid"):
            WorkflowStore._assert_accounting_integrity(
                db, CHAIN_ID, BASE_SEPOLIA_USDC_ADDRESS
            )


def test_corruption_for_an_unrelated_chain_does_not_block_accounting(tmp_path):
    store = WorkflowStore(tmp_path / "unrelated-corrupt.sqlite3")
    store.create(_record("unrelated-reservation"))
    with store._connect() as db:
        db.execute(
            "UPDATE transactions SET chain_id = ? WHERE transaction_id = ?",
            (OTHER_CHAIN_ID, "unrelated-reservation"),
        )

    with store._connect() as db:
        WorkflowStore._assert_accounting_integrity(
            db, OTHER_CHAIN_ID, "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )


def test_integrity_guard_clears_flag_after_accounting_fields_are_repaired(tmp_path):
    store = WorkflowStore(tmp_path / "repair-corrupt-row.sqlite3")
    store.create(_record("repairable"))
    _corrupt_amount(store, "repairable")

    with store._connect() as db:
        flagged = db.execute(
            "SELECT accounting_integrity FROM transactions WHERE transaction_id = ?",
            ("repairable",),
        ).fetchone()[0]
    assert flagged == 0

    with store._connect() as db:
        db.execute(
            "UPDATE transactions SET amount_base_units = amount_base_units - 1 "
            "WHERE transaction_id = ?",
            ("repairable",),
        )
        repaired = db.execute(
            "SELECT accounting_integrity FROM transactions WHERE transaction_id = ?",
            ("repairable",),
        ).fetchone()[0]
    assert repaired == 1

    with store._connect() as db:
        WorkflowStore._assert_accounting_integrity(
            db, CHAIN_ID, BASE_SEPOLIA_USDC_ADDRESS
        )
