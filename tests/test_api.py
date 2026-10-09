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


def test_risk_endpoint_returns_score_and_reasons() -> None:
    response = client.post(
        "/risk/assess",
        json={
            "agent_id": "devops-01",
            "chain_id": 84532,
            "token_symbol": "USDC",
            "token_address": "0x1111111111111111111111111111111111111111",
            "recipient": "0x4444444444444444444444444444444444444444",
            "amount_base_units": 45000000,
            "token_decimals": 6,
            "daily_spent_base_units": 80000000,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["score"] == 85
    assert body["level"] == "high"
    assert body["reasons"]


def test_risk_endpoint_rejects_unknown_agent() -> None:
    response = client.post(
        "/risk/assess",
        json={
            "agent_id": "unknown-agent",
            "chain_id": 84532,
            "token_symbol": "USDC",
            "token_address": "0x1111111111111111111111111111111111111111",
            "recipient": "0x2222222222222222222222222222222222222222",
            "amount_base_units": 100,
            "token_decimals": 6,
        },
    )
    assert response.status_code == 404
