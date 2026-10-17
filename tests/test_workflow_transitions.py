"""Regression tests for workflow state transitions and audit consistency."""
import sqlite3

import pytest

from app.chain.base_sepolia import BASE_SEPOLIA_USDC_ADDRESS
from app.policy.models import TransactionProposal
from app.workflow.controller import TransactionController
from app.workflow.store import WorkflowStore


def _proposal(**overrides) -> TransactionProposal:
    payload = {
        "agent_id": "devops-01",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": BASE_SEPOLIA_USDC_ADDRESS,
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 1_000_000,
        "token_decimals": 6,
    }
    payload.update(overrides)
    return TransactionProposal(**payload)


def _workflow(tmp_path, policy):
    store = WorkflowStore(tmp_path / "workflow-transitions.sqlite3")
    return store, TransactionController(policy, store)


def test_ready_transaction_executes_once_and_terminal_state_is_audited(tmp_path):
    from app.main import DEMO_POLICY

    store, controller = _workflow(tmp_path, DEMO_POLICY)
    outcome, record = controller.propose(_proposal())
    assert outcome == "created"
    assert record["status"] == "ready"

    outcome, executed = controller.execute(record["transaction_id"])
    assert outcome == "executed"
    assert executed["status"] == "executed_simulated"

    outcome, repeated = controller.execute(record["transaction_id"])
    assert outcome == "already_executed"
    assert repeated["status"] == "executed_simulated"

    events = store.audit_events(record["transaction_id"])
    assert [event["event_type"] for event in events] == [
        "proposal_created",
        "simulated_execution",
    ]
    assert [(event["from_status"], event["to_status"]) for event in events] == [
        (None, "ready"),
        ("ready", "executed_simulated"),
    ]


def test_approval_required_path_rejects_early_execution_then_completes(tmp_path):
    from app.main import DEMO_POLICY

    store, controller = _workflow(tmp_path, DEMO_POLICY)
    outcome, record = controller.propose(
        _proposal(recipient="0x4444444444444444444444444444444444444444")
    )
    assert outcome == "created"
    assert record["status"] == "awaiting_approval"

    outcome, unchanged = controller.execute(record["transaction_id"])
    assert outcome == "approval_required"
    assert unchanged["status"] == "awaiting_approval"
    assert [event["event_type"] for event in store.audit_events(record["transaction_id"])] == [
        "proposal_created"
    ]

    outcome, approved = controller.approve(record["transaction_id"], record["fingerprint"])
    assert outcome == "approved"
    assert approved["status"] == "approved"

    outcome, executed = controller.execute(record["transaction_id"])
    assert outcome == "executed"
    assert executed["status"] == "executed_simulated"

    events = store.audit_events(record["transaction_id"])
    assert [event["event_type"] for event in events] == [
        "proposal_created",
        "approval_granted",
        "simulated_execution",
    ]
    assert [(event["from_status"], event["to_status"]) for event in events] == [
        (None, "awaiting_approval"),
        ("awaiting_approval", "approved"),
        ("approved", "executed_simulated"),
    ]


def test_blocked_transaction_cannot_be_approved_or_executed(tmp_path):
    from app.main import DEMO_POLICY

    store, controller = _workflow(tmp_path, DEMO_POLICY)
    outcome, record = controller.propose(_proposal(chain_id=1))
    assert outcome == "created"
    assert record["status"] == "blocked"

    outcome, unchanged = controller.approve(record["transaction_id"], record["fingerprint"])
    assert outcome == "blocked"
    assert unchanged["status"] == "blocked"

    outcome, unchanged = controller.execute(record["transaction_id"])
    assert outcome == "blocked"
    assert unchanged["status"] == "blocked"

    events = store.audit_events(record["transaction_id"])
    assert [event["event_type"] for event in events] == ["proposal_created"]
    assert [(event["from_status"], event["to_status"]) for event in events] == [
        (None, "blocked")
    ]


def test_ready_transaction_cannot_be_approved_without_an_approval_requirement(tmp_path):
    from app.main import DEMO_POLICY

    store, controller = _workflow(tmp_path, DEMO_POLICY)
    outcome, record = controller.propose(_proposal())
    assert outcome == "created"
    assert record["status"] == "ready"

    outcome, unchanged = controller.approve(record["transaction_id"], record["fingerprint"])
    assert outcome == "approval_not_required"
    assert unchanged["status"] == "ready"
    assert [event["event_type"] for event in store.audit_events(record["transaction_id"])] == [
        "proposal_created"
    ]


def test_cancelling_pending_transaction_is_terminal_and_audited(tmp_path):
    from app.main import DEMO_POLICY

    store, controller = _workflow(tmp_path, DEMO_POLICY)
    outcome, record = controller.propose(
        _proposal(recipient="0x4444444444444444444444444444444444444444")
    )
    assert outcome == "created"
    assert record["status"] == "awaiting_approval"

    outcome, cancelled = controller.cancel(record["transaction_id"])

    assert outcome == "cancelled"
    assert cancelled["status"] == "cancelled"
    assert store.daily_spend(84532, record["proposal"]["token_address"]) == 0
    assert [event["event_type"] for event in store.audit_events(record["transaction_id"])] == [
        "proposal_created",
        "transaction_cancelled",
    ]
    assert store.audit_events(record["transaction_id"])[-1]["details"] == {
        "reservation_released": True
    }
    assert controller.cancel(record["transaction_id"])[0] == "already_cancelled"


def test_rejecting_pending_transaction_is_terminal_idempotent_and_audited(tmp_path):
    from app.main import DEMO_POLICY

    store, controller = _workflow(tmp_path, DEMO_POLICY)
    outcome, record = controller.propose(
        _proposal(recipient="0x4444444444444444444444444444444444444444")
    )
    assert outcome == "created"
    assert record["status"] == "awaiting_approval"

    outcome, rejected = controller.reject(record["transaction_id"])
    assert outcome == "rejected"
    assert rejected["status"] == "rejected"
    assert store.daily_spend(84532, record["proposal"]["token_address"]) == 0
    with store._connect() as db:
        recipient_spend = store._recipient_daily_spend(
            db,
            84532,
            record["proposal"]["token_address"],
            record["proposal"]["recipient"],
        )
    assert recipient_spend == 0

    outcome, repeated = controller.reject(record["transaction_id"])
    assert outcome == "already_rejected"
    assert repeated["status"] == "rejected"

    events = store.audit_events(record["transaction_id"])
    assert [event["event_type"] for event in events] == [
        "proposal_created",
        "transaction_rejected",
    ]
    assert (events[-1]["from_status"], events[-1]["to_status"]) == (
        "awaiting_approval",
        "rejected",
    )
    assert events[-1]["details"] == {"reservation_released": True}


def test_sqlite_guards_accept_rejected_and_reject_unknown_status_and_audit_type(tmp_path):
    from app.main import DEMO_POLICY

    store, controller = _workflow(tmp_path, DEMO_POLICY)
    _, record = controller.propose(_proposal())

    with store._connect() as db:
        db.execute(
            "UPDATE transactions SET status = 'rejected' WHERE transaction_id = ?",
            (record["transaction_id"],),
        )
        with pytest.raises(sqlite3.IntegrityError, match="invalid transaction status"):
            db.execute(
                "UPDATE transactions SET status = 'unknown' WHERE transaction_id = ?",
                (record["transaction_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="invalid audit event metadata"):
            db.execute(
                """INSERT INTO audit_events
                   (transaction_id, event_type, actor_role, from_status, to_status)
                   VALUES (?, 'unknown-event', 'owner', 'ready', 'rejected')""",
                (record["transaction_id"],),
            )


def test_pause_blocks_new_proposals_and_disables_approve_execute(tmp_path):
    from app.main import DEMO_POLICY

    store, controller = _workflow(tmp_path, DEMO_POLICY)
    store.set_pause_state(True, "maintenance window", actor_role="owner")

    outcome, blocked = controller.propose(_proposal())
    assert outcome == "created"
    assert blocked["status"] == "blocked"
    assert any("pause" in reason.lower() for reason in blocked["policy_reasons"])

    assert controller.approve(blocked["transaction_id"], blocked["fingerprint"])[0] == "wallet_paused"
    assert controller.execute(blocked["transaction_id"])[0] == "wallet_paused"

    store.set_pause_state(False, "traffic restored", actor_role="owner")
    outcome, record = controller.propose(_proposal())
    assert outcome == "created"
    assert record["status"] == "ready"


def test_pause_control_log_is_append_only_and_pending_cap_releases_with_rejection(tmp_path, monkeypatch):
    from app.main import DEMO_POLICY

    monkeypatch.setenv("AEGIS_MAX_PENDING_PROPOSALS", "1")
    store = WorkflowStore(tmp_path / "pause-cap.sqlite3")
    controller = TransactionController(DEMO_POLICY, store)

    store.set_pause_state(True, "maintenance", actor_role="owner")
    with store._connect() as db:
        with pytest.raises(sqlite3.IntegrityError, match="control events are append-only"):
            db.execute("UPDATE control_events SET reason = 'tampered' WHERE event_id = 1")
        with pytest.raises(sqlite3.IntegrityError, match="control events are append-only"):
            db.execute("DELETE FROM control_events WHERE event_id = 1")

    store.set_pause_state(False, "clear", actor_role="owner")
    first = controller.propose(_proposal())[1]
    assert first["status"] == "ready"

    second = controller.propose(_proposal(recipient="0x4444444444444444444444444444444444444444"))
    assert second[0] == "pending_cap_exceeded"
    assert second[1] is None

    controller.reject(first["transaction_id"])
    third = controller.propose(_proposal(recipient="0x4444444444444444444444444444444444444444"))
    assert third[0] == "created"
    assert third[1]["status"] == "awaiting_approval"

    events = store.control_events()
    assert [event["action"] for event in events] == ["pause", "resume"]
