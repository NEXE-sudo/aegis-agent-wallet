from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.workflow.store as store_module
from app import main
from app.chain.base_sepolia import BASE_SEPOLIA_USDC_ADDRESS
from app.workflow.controller import TransactionController
from app.workflow.store import WorkflowStore

AGENT_HEADERS = {"Authorization": "Bearer demo-agent-token"}
OWNER_HEADERS = {"Authorization": "Bearer demo-owner-token"}
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def isolated_workflow(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_AGENT_TOKEN", "demo-agent-token")
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", "demo-owner-token")
    local_store = WorkflowStore(tmp_path / "demo-workflow.sqlite3")
    monkeypatch.setattr(main, "store", local_store)
    monkeypatch.setattr(
        main, "controller", TransactionController(main.DEMO_POLICY, local_store)
    )


def test_documented_unknown_recipient_requires_approval_before_execution():
    proposal = {
        "agent_id": "devops-01",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": BASE_SEPOLIA_USDC_ADDRESS,
        "recipient": "0x4444444444444444444444444444444444444444",
        "amount_base_units": 1_000_000,
        "token_decimals": 6,
    }

    proposed = client.post(
        "/transactions/propose",
        headers=AGENT_HEADERS,
        json=proposal,
    )

    assert proposed.status_code == 201
    record = proposed.json()
    assert record["status"] == "awaiting_approval"

    executed = client.post(
        f"/transactions/{record['transaction_id']}/execute",
        headers=OWNER_HEADERS,
    )

    assert executed.status_code == 409
    assert executed.json() == {
        "detail": "Human approval is required before execution"
    }



def test_documented_allowed_recipient_can_execute_as_simulation():
    proposal = {
        "agent_id": "devops-01",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": BASE_SEPOLIA_USDC_ADDRESS,
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 1_000_000,
        "token_decimals": 6,
    }

    proposed = client.post(
        "/transactions/propose",
        headers={**AGENT_HEADERS, "Idempotency-Key": "demo-allowed-001"},
        json=proposal,
    )

    assert proposed.status_code == 201
    record = proposed.json()
    assert record["status"] == "ready"

    executed = client.post(
        f"/transactions/{record['transaction_id']}/execute",
        headers=OWNER_HEADERS,
    )

    assert executed.status_code == 200
    result = executed.json()
    assert result["status"] == "executed_simulated"
    assert result["execution_reference"].startswith("simulated:")



def test_documented_demo_token_symbol_mismatch_cannot_be_approved_or_executed():
    proposal = {
        "agent_id": "devops-01",
        "chain_id": 84532,
        "token_symbol": "FAKE",
        "token_address": BASE_SEPOLIA_USDC_ADDRESS,
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 1_000_000,
        "token_decimals": 6,
    }

    proposed = client.post(
        "/transactions/propose",
        headers={**AGENT_HEADERS, "Idempotency-Key": "demo-blocked-001"},
        json=proposal,
    )

    assert proposed.status_code == 201
    record = proposed.json()
    assert record["status"] == "blocked"
    assert record["policy_decision"] == "block"

    approval = client.post(
        f"/transactions/{record['transaction_id']}/approve",
        headers=OWNER_HEADERS,
        json={
            "transaction_fingerprint": record["fingerprint"],
            "confirmation": "APPROVE",
        },
    )
    assert approval.status_code == 409

    executed = client.post(
        f"/transactions/{record['transaction_id']}/execute",
        headers=OWNER_HEADERS,
    )
    assert executed.status_code == 403



def test_documented_expired_approval_requires_reapproval_before_simulated_execution(monkeypatch):
    now = [datetime(2026, 10, 10, 12, 0, tzinfo=UTC)]
    monkeypatch.setattr(store_module, "_utc_now", lambda: now[0])
    proposal = {
        "agent_id": "devops-01",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": BASE_SEPOLIA_USDC_ADDRESS,
        "recipient": "0x4444444444444444444444444444444444444444",
        "amount_base_units": 1_000_000,
        "token_decimals": 6,
    }
    proposed = client.post(
        "/transactions/propose",
        headers={**AGENT_HEADERS, "Idempotency-Key": "demo-expiry-001"},
        json=proposal,
    )

    assert proposed.status_code == 201
    record = proposed.json()
    assert record["status"] == "awaiting_approval"
    approval_body = {
        "transaction_fingerprint": record["fingerprint"],
        "confirmation": "APPROVE",
    }
    tx_id = record["transaction_id"]

    approved = client.post(
        f"/transactions/{tx_id}/approve",
        headers=OWNER_HEADERS,
        json=approval_body,
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    now[0] += timedelta(seconds=301)
    expired_execution = client.post(
        f"/transactions/{tx_id}/execute",
        headers=OWNER_HEADERS,
    )
    assert expired_execution.status_code == 409
    assert expired_execution.json()["detail"] == "Human approval is required before execution"

    refreshed = client.get(f"/transactions/{tx_id}", headers=OWNER_HEADERS)
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "awaiting_approval"
    assert refreshed.json()["approval_fingerprint"] is None
    assert refreshed.json()["approved_at"] is None

    events = client.get(f"/transactions/{tx_id}/audit", headers=OWNER_HEADERS)
    assert events.status_code == 200
    expiry_events = [
        event for event in events.json() if event["event_type"] == "approval_expired"
    ]
    assert len(expiry_events) == 1
    assert expiry_events[0]["from_status"] == "approved"
    assert expiry_events[0]["to_status"] == "awaiting_approval"

    renewed = client.post(
        f"/transactions/{tx_id}/approve",
        headers=OWNER_HEADERS,
        json=approval_body,
    )
    assert renewed.status_code == 200
    assert renewed.json()["status"] == "approved"

    executed = client.post(
        f"/transactions/{tx_id}/execute",
        headers=OWNER_HEADERS,
    )
    assert executed.status_code == 200
    assert executed.json()["status"] == "executed_simulated"
    assert executed.json()["execution_reference"].startswith("simulated:")



def test_documented_idempotency_replay_returns_original_and_changed_payload_conflicts():
    proposal = {
        "agent_id": "devops-01",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": BASE_SEPOLIA_USDC_ADDRESS,
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 1_000_000,
        "token_decimals": 6,
    }
    headers = {**AGENT_HEADERS, "Idempotency-Key": "demo-allowed-001"}

    first = client.post("/transactions/propose", headers=headers, json=proposal)
    assert first.status_code == 201
    original = first.json()
    assert original["status"] == "ready"

    replay = client.post("/transactions/propose", headers=headers, json=proposal)
    assert replay.status_code == 200
    assert replay.json()["transaction_id"] == original["transaction_id"]
    assert replay.json()["fingerprint"] == original["fingerprint"]

    changed_proposal = {**proposal, "amount_base_units": 2_000_000}
    conflict = client.post(
        "/transactions/propose",
        headers=headers,
        json=changed_proposal,
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "Idempotency-Key was already used for a different proposal"

    events = client.get(
        f"/transactions/{original['transaction_id']}/audit",
        headers=OWNER_HEADERS,
    )
    assert events.status_code == 200
    event_types = [event["event_type"] for event in events.json()]
    assert event_types.count("proposal_created") == 1
    assert event_types.count("proposal_replayed") == 1
