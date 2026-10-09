from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _payload(**overrides):
    payload = {
        "agent_id": "devops-01",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": "0x1111111111111111111111111111111111111111",
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 1_000_000,
        "token_decimals": 6,
        "daily_spent_base_units": 0,
    }
    payload.update(overrides)
    return payload


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_policy_endpoint_blocks_wrong_chain():
    response = client.post("/policy/evaluate", json=_payload(chain_id=1))
    assert response.status_code == 200
    assert response.json()["decision"] == "block"


def test_unknown_agent_returns_404():
    response = client.post("/policy/evaluate", json=_payload(agent_id="not-configured"))
    assert response.status_code == 404


def test_risk_endpoint_returns_score_and_reasons():
    response = client.post(
        "/risk/assess",
        json=_payload(
            recipient="0x4444444444444444444444444444444444444444",
            amount_base_units=45_000_000,
            daily_spent_base_units=80_000_000,
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["score"] == 85
    assert body["level"] == "high"
    assert body["reasons"]


def test_risk_endpoint_rejects_unknown_agent():
    response = client.post("/risk/assess", json=_payload(agent_id="unknown-agent"))
    assert response.status_code == 404


def test_low_risk_transaction_can_be_simulated_once():
    proposed = client.post("/transactions/propose", json=_payload())
    assert proposed.status_code == 201
    record = proposed.json()
    assert record["status"] == "ready"

    executed = client.post(f"/transactions/{record['transaction_id']}/execute")
    assert executed.status_code == 200
    assert executed.json()["status"] == "executed_simulated"
    assert executed.json()["execution_reference"].startswith("simulated:")

    retry = client.post(f"/transactions/{record['transaction_id']}/execute")
    assert retry.status_code == 409


def test_unknown_recipient_requires_fingerprint_bound_approval():
    proposed = client.post(
        "/transactions/propose",
        json=_payload(recipient="0x4444444444444444444444444444444444444444"),
    )
    assert proposed.status_code == 201
    record = proposed.json()
    assert record["status"] == "awaiting_approval"

    wrong = client.post(
        f"/transactions/{record['transaction_id']}/approve",
        json={"transaction_fingerprint": "0" * 64, "confirmation": "APPROVE"},
    )
    assert wrong.status_code == 409

    approved = client.post(
        f"/transactions/{record['transaction_id']}/approve",
        json={
            "transaction_fingerprint": record["fingerprint"],
            "confirmation": "APPROVE",
        },
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["approval_fingerprint"] == record["fingerprint"]

    executed = client.post(f"/transactions/{record['transaction_id']}/execute")
    assert executed.status_code == 200
    assert executed.json()["status"] == "executed_simulated"


def test_blocked_transaction_cannot_be_approved_or_executed():
    proposed = client.post("/transactions/propose", json=_payload(chain_id=1))
    record = proposed.json()
    assert record["status"] == "blocked"

    approve = client.post(
        f"/transactions/{record['transaction_id']}/approve",
        json={"transaction_fingerprint": record["fingerprint"], "confirmation": "APPROVE"},
    )
    assert approve.status_code == 409

    execute = client.post(f"/transactions/{record['transaction_id']}/execute")
    assert execute.status_code == 403


def test_high_risk_transaction_requires_approval_even_if_policy_allows():
    # Approved recipient, but large enough to trigger the configured risk heuristic.
    proposed = client.post(
        "/transactions/propose",
        json=_payload(amount_base_units=40_000_000, daily_spent_base_units=90_000_000),
    )
    record = proposed.json()
    assert record["risk_level"] == "high"
    assert record["status"] == "awaiting_approval"


def test_transaction_state_survives_store_reopen(tmp_path):
    from app.workflow.store import WorkflowStore

    db_path = tmp_path / "workflow.sqlite3"
    local_store = WorkflowStore(db_path)
    record = {
        "transaction_id": "test-id",
        "fingerprint": "a" * 64,
        "proposal": _payload(),
        "policy_decision": "allow",
        "policy_reasons": ["ok"],
        "risk_score": 0,
        "risk_level": "low",
        "risk_reasons": ["ok"],
        "status": "ready",
    }
    local_store.create(record)
    reopened = WorkflowStore(db_path)
    assert reopened.get("test-id")["status"] == "ready"
