"""Endpoint-wide authorization regression tests for the security review."""
import pytest
from fastapi.testclient import TestClient

from app import main
from app.chain import router as chain_router
from app.chain.base_sepolia import BASE_SEPOLIA_USDC_ADDRESS

AGENT_TOKEN = "security-review-agent-token"
OWNER_TOKEN = "security-review-owner-token"
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def configured_tokens(monkeypatch):
    monkeypatch.setenv("AEGIS_AGENT_TOKEN", AGENT_TOKEN)
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_TOKEN)


def _request(method, path, headers=None):
    kwargs = {"headers": headers} if headers is not None else {}
    if method == "post":
        kwargs["json"] = {
            "transaction_fingerprint": "a" * 64,
            "confirmation": "APPROVE",
        } if path.endswith("/approve") else {
            "agent_id": "devops-01",
            "chain_id": 84532,
            "token_symbol": "USDC",
            "token_address": BASE_SEPOLIA_USDC_ADDRESS,
            "recipient": "0x2222222222222222222222222222222222222222",
            "amount_base_units": 1_000_000,
            "token_decimals": 6,
        }
    return client.request(method.upper(), path, **kwargs)


@pytest.mark.parametrize(
    ("method", "path", "valid_token"),
    [
        ("post", "/transactions/propose", AGENT_TOKEN),
        ("get", "/transactions/security-review-id", OWNER_TOKEN),
        ("get", "/transactions/security-review-id/audit", OWNER_TOKEN),
        ("post", "/transactions/security-review-id/approve", OWNER_TOKEN),
        ("post", "/transactions/security-review-id/execute", OWNER_TOKEN),
        ("get", "/chain/base-sepolia/status", OWNER_TOKEN),
        (
            "get",
            "/chain/base-sepolia/token/0x1111111111111111111111111111111111111111",
            OWNER_TOKEN,
        ),
    ],
    ids=[
        "agent-proposal",
        "owner-transaction-read",
        "owner-audit-read",
        "owner-approval",
        "owner-simulated-execution",
        "owner-chain-status",
        "owner-token-inspection",
    ],
)
@pytest.mark.parametrize("credential_mode", ["missing", "wrong-role"])
def test_protected_endpoint_rejects_missing_or_wrong_role_token(
    monkeypatch, method, path, valid_token, credential_mode
):
    if path.startswith("/chain/"):
        def unexpected_rpc():
            raise AssertionError("RPC must not be initialized before authorization")

        monkeypatch.setattr(chain_router, "BaseSepoliaRpc", unexpected_rpc)

    headers = None
    if credential_mode == "wrong-role":
        wrong_token = OWNER_TOKEN if valid_token == AGENT_TOKEN else AGENT_TOKEN
        headers = {"Authorization": f"Bearer {wrong_token}"}

    response = _request(method, path, headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing or invalid bearer token"}


@pytest.mark.parametrize("path", ["/policy/evaluate", "/risk/assess"])
def test_documented_standalone_evaluation_endpoints_remain_public(path):
    payload = {
        "agent_id": "devops-01",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": main.DEMO_POLICY.allowed_token_addresses.copy().pop(),
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 1_000_000,
        "token_decimals": 6,
        "daily_spent_base_units": 0,
    }

    response = client.post(path, json=payload)

    assert response.status_code == 200
