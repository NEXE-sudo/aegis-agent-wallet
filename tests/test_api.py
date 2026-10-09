import os

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.workflow.controller import TransactionController
from app.workflow.store import WorkflowStore

AGENT_TOKEN = "test-agent-token-with-enough-length"
OWNER_TOKEN = "test-owner-token-with-enough-length"
AGENT_HEADERS = {"Authorization": f"Bearer {AGENT_TOKEN}"}
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def isolated_workflow(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_AGENT_TOKEN", AGENT_TOKEN)
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_TOKEN)
    local_store = WorkflowStore(tmp_path / "workflow.sqlite3")
    monkeypatch.setattr(main, "store", local_store)
    monkeypatch.setattr(main, "controller", TransactionController(main.DEMO_POLICY, local_store))


def _payload(**overrides):
    payload = {
        "agent_id": "devops-01",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": "0x1111111111111111111111111111111111111111",
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 1_000_000,
        "token_decimals": 6,
    }
    payload.update(overrides)
    return payload


def propose(payload=None, **overrides):
    return client.post("/transactions/propose", headers=AGENT_HEADERS, json=payload or _payload(**overrides))


def owner_post(path, **kwargs):
    return client.post(path, headers=OWNER_HEADERS, **kwargs)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_policy_endpoint_blocks_wrong_chain():
    response = client.post("/policy/evaluate", json={**_payload(), "chain_id": 1, "daily_spent_base_units": 0})
    assert response.status_code == 200
    assert response.json()["decision"] == "block"


def test_unknown_agent_returns_404():
    response = client.post("/policy/evaluate", json={**_payload(), "agent_id": "not-configured"})
    assert response.status_code == 404


def test_risk_endpoint_returns_score_and_reasons():
    response = client.post("/risk/assess", json={
        **_payload(recipient="0x4444444444444444444444444444444444444444", amount_base_units=45_000_000),
        "daily_spent_base_units": 80_000_000,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["score"] == 85
    assert body["level"] == "high"
    assert body["reasons"]


def test_risk_endpoint_rejects_unknown_agent():
    response = client.post("/risk/assess", json={**_payload(), "agent_id": "unknown-agent"})
    assert response.status_code == 404


def test_workflow_requires_distinct_authentication():
    assert client.post("/transactions/propose", json=_payload()).status_code == 401
    assert client.post("/transactions/propose", headers=OWNER_HEADERS, json=_payload()).status_code == 401
    record = propose().json()
    assert client.get(f"/transactions/{record['transaction_id']}").status_code == 401
    assert owner_post(f"/transactions/{record['transaction_id']}/execute").status_code == 200


def test_low_risk_transaction_can_be_simulated_once():
    proposed = propose()
    assert proposed.status_code == 201
    record = proposed.json()
    assert record["status"] == "ready"
    executed = owner_post(f"/transactions/{record['transaction_id']}/execute")
    assert executed.status_code == 200
    assert executed.json()["status"] == "executed_simulated"
    assert executed.json()["execution_reference"].startswith("simulated:")
    assert owner_post(f"/transactions/{record['transaction_id']}/execute").status_code == 409


def test_unknown_recipient_requires_fingerprint_bound_approval():
    record = propose(_payload(recipient="0x4444444444444444444444444444444444444444")).json()
    assert record["status"] == "awaiting_approval"
    wrong = owner_post(f"/transactions/{record['transaction_id']}/approve", json={
        "transaction_fingerprint": "0" * 64, "confirmation": "APPROVE",
    })
    assert wrong.status_code == 409
    approved = owner_post(f"/transactions/{record['transaction_id']}/approve", json={
        "transaction_fingerprint": record["fingerprint"], "confirmation": "APPROVE",
    })
    assert approved.status_code == 200
    assert approved.json()["approval_fingerprint"] == record["fingerprint"]
    executed = owner_post(f"/transactions/{record['transaction_id']}/execute")
    assert executed.status_code == 200
    assert executed.json()["status"] == "executed_simulated"


def test_blocked_transaction_cannot_be_approved_or_executed():
    record = propose(_payload(chain_id=1)).json()
    assert record["status"] == "blocked"
    approve = owner_post(f"/transactions/{record['transaction_id']}/approve", json={
        "transaction_fingerprint": record["fingerprint"], "confirmation": "APPROVE",
    })
    assert approve.status_code == 409
    assert owner_post(f"/transactions/{record['transaction_id']}/execute").status_code == 403


def test_transaction_workflow_ignores_caller_supplied_daily_spend():
    # ProposalRequest does not accept caller-supplied accounting data. Persisted spend is authoritative.
    body = {**_payload(amount_base_units=40_000_000), "daily_spent_base_units": 149_000_000}
    record = propose(body).json()
    assert record["status"] == "ready"  # caller's fabricated spend is ignored


def test_daily_spend_reservations_block_overspend():
    first = propose(_payload(amount_base_units=45_000_000)).json()
    assert first["status"] != "blocked"
    second = propose(_payload(amount_base_units=45_000_000)).json()
    assert second["status"] != "blocked"
    third = propose(_payload(amount_base_units=45_000_000)).json()
    assert third["status"] != "blocked"
    fourth = propose(_payload(amount_base_units=20_000_000)).json()
    assert fourth["status"] == "blocked"
    assert "daily spending limit" in " ".join(fourth["policy_reasons"]).lower()


def test_idempotency_replay_returns_same_transaction_and_conflict_for_changed_payload():
    headers = {**AGENT_HEADERS, "Idempotency-Key": "retry-key-0001"}
    first = client.post("/transactions/propose", headers=headers, json=_payload())
    second = client.post("/transactions/propose", headers=headers, json=_payload())
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["transaction_id"] == second.json()["transaction_id"]
    conflict = client.post("/transactions/propose", headers=headers, json=_payload(amount_base_units=2_000_000))
    assert conflict.status_code == 409


def test_execution_rechecks_spending_and_blocks_stale_approval():
    # First reserve most of the daily budget; second proposal is initially evaluated against it.
    first = propose(_payload(amount_base_units=45_000_000)).json()
    second = propose(_payload(amount_base_units=45_000_000)).json()
    assert second["status"] in {"ready", "awaiting_approval"}
    # Simulate an externally inserted reservation to test execution-time revalidation.
    from app.workflow.controller import fingerprint_proposal
    from app.policy.models import TransactionProposal
    proposal = TransactionProposal(**_payload(amount_base_units=65_000_000))
    main.store.create({
        "transaction_id": "external-reservation", "fingerprint": fingerprint_proposal(proposal),
        "proposal": _payload(amount_base_units=65_000_000), "policy_decision": "allow",
        "policy_reasons": ["test reservation"], "risk_score": 0, "risk_level": "low",
        "risk_reasons": [], "status": "ready",
    })
    result = owner_post(f"/transactions/{second['transaction_id']}/execute")
    assert result.status_code == 403
    assert result.json()["detail"] == "Policy-blocked transactions cannot execute"


def test_missing_auth_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("AEGIS_AGENT_TOKEN", raising=False)
    response = client.post("/transactions/propose", json=_payload())
    assert response.status_code == 503


def test_transaction_state_survives_store_reopen(tmp_path):
    db_path = tmp_path / "workflow.sqlite3"
    local_store = WorkflowStore(db_path)
    record = {
        "transaction_id": "test-id", "fingerprint": "a" * 64, "proposal": _payload(),
        "policy_decision": "allow", "policy_reasons": ["ok"], "risk_score": 0,
        "risk_level": "low", "risk_reasons": ["ok"], "status": "ready",
    }
    local_store.create(record)
    reopened = WorkflowStore(db_path)
    assert reopened.get("test-id")["status"] == "ready"
    assert reopened.daily_spend(84532, _payload()["token_address"]) == 1_000_000
