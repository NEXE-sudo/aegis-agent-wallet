"""Regression tests for workflow state transitions and audit consistency."""
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
