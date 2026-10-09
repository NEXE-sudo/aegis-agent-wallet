from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_policy_endpoint_blocks_wrong_chain():
    response = client.post("/policy/evaluate", json={
        "agent_id": "devops-01",
        "chain_id": 1,
        "token_symbol": "USDC",
        "token_address": "0x1111111111111111111111111111111111111111",
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 1_000_000,
        "token_decimals": 6,
        "daily_spent_base_units": 0,
    })
    assert response.status_code == 200
    assert response.json()["decision"] == "block"


def test_unknown_agent_returns_404():
    response = client.post("/policy/evaluate", json={
        "agent_id": "not-configured",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": "0x1111111111111111111111111111111111111111",
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 1_000_000,
        "token_decimals": 6,
        "daily_spent_base_units": 0,
    })
    assert response.status_code == 404
