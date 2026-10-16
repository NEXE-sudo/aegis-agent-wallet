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
        if path.endswith("/approve"):
            kwargs["json"] = {
                "transaction_fingerprint": "a" * 64,
                "confirmation": "APPROVE",
            }
        elif path == "/transactions/propose":
            kwargs["json"] = {
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
        ("get", "/agent/transactions/security-review-id", AGENT_TOKEN),
        ("get", "/agent/market-data/crypto/BTC", AGENT_TOKEN),
        ("get", "/transactions/security-review-id", OWNER_TOKEN),
        ("get", "/transactions/security-review-id/audit", OWNER_TOKEN),
        ("get", "/transactions/security-review-id/receipt", OWNER_TOKEN),
        ("post", "/transactions/security-review-id/approve", OWNER_TOKEN),
        ("post", "/transactions/security-review-id/execute", OWNER_TOKEN),
        ("post", "/transactions/security-review-id/reject", OWNER_TOKEN),
        ("get", "/chain/base-sepolia/status", OWNER_TOKEN),
        (
            "get",
            "/chain/base-sepolia/token/0x1111111111111111111111111111111111111111",
            OWNER_TOKEN,
        ),
    ],
    ids=[
        "agent-proposal",
        "agent-transaction-status",
        "agent-market-data",
        "owner-transaction-read",
        "owner-audit-read",
        "owner-receipt-read",
        "owner-approval",
        "owner-simulated-execution",
        "owner-rejection",
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

@pytest.mark.parametrize(
    ("method", "path", "valid_token"),
    [
        ("post", "/transactions/propose", AGENT_TOKEN),
        ("get", "/agent/transactions/security-review-id", AGENT_TOKEN),
        ("get", "/agent/market-data/crypto/BTC", AGENT_TOKEN),
        ("get", "/transactions/security-review-id", OWNER_TOKEN),
        ("get", "/transactions/security-review-id/audit", OWNER_TOKEN),
        ("get", "/transactions/security-review-id/receipt", OWNER_TOKEN),
        ("post", "/transactions/security-review-id/approve", OWNER_TOKEN),
        ("post", "/transactions/security-review-id/execute", OWNER_TOKEN),
        ("post", "/transactions/security-review-id/reject", OWNER_TOKEN),
        ("get", "/chain/base-sepolia/status", OWNER_TOKEN),
        (
            "get",
            "/chain/base-sepolia/token/0x1111111111111111111111111111111111111111",
            OWNER_TOKEN,
        ),
    ],
    ids=[
        "agent-proposal",
        "agent-transaction-status",
        "agent-market-data",
        "owner-transaction-read",
        "owner-audit-read",
        "owner-receipt-read",
        "owner-approval",
        "owner-simulated-execution",
        "owner-rejection",
        "owner-chain-status",
        "owner-token-inspection",
    ],
)
def test_protected_endpoint_fails_closed_when_tokens_are_identical(
    monkeypatch, method, path, valid_token
):
    monkeypatch.setenv("AEGIS_AGENT_TOKEN", "shared-security-review-token")
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", "shared-security-review-token")

    if path.startswith("/chain/"):
        def unexpected_rpc():
            raise AssertionError("RPC must not be initialized when authentication configuration is invalid")

        monkeypatch.setattr(chain_router, "BaseSepoliaRpc", unexpected_rpc)

    response = _request(
        method,
        path,
        {"Authorization": "Bearer shared-security-review-token"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "AEGIS_AGENT_TOKEN and AEGIS_APPROVAL_TOKEN must be different; endpoint fails closed"
    }


@pytest.mark.parametrize(
    ("method", "path", "configured_env", "configured_token"),
    [
        ("post", "/transactions/propose", "AEGIS_AGENT_TOKEN", AGENT_TOKEN),
        (
            "get",
            "/agent/transactions/security-review-id",
            "AEGIS_AGENT_TOKEN",
            AGENT_TOKEN,
        ),
        (
            "get",
            "/agent/market-data/crypto/BTC",
            "AEGIS_AGENT_TOKEN",
            AGENT_TOKEN,
        ),
        ("get", "/transactions/security-review-id", "AEGIS_APPROVAL_TOKEN", OWNER_TOKEN),
        ("get", "/transactions/security-review-id/audit", "AEGIS_APPROVAL_TOKEN", OWNER_TOKEN),
        ("get", "/transactions/security-review-id/receipt", "AEGIS_APPROVAL_TOKEN", OWNER_TOKEN),
        ("post", "/transactions/security-review-id/approve", "AEGIS_APPROVAL_TOKEN", OWNER_TOKEN),
        ("post", "/transactions/security-review-id/execute", "AEGIS_APPROVAL_TOKEN", OWNER_TOKEN),
        ("post", "/transactions/security-review-id/reject", "AEGIS_APPROVAL_TOKEN", OWNER_TOKEN),
    ],
    ids=[
        "agent-proposal-missing-owner-token",
        "agent-transaction-status-missing-owner-token",
        "agent-market-data-missing-owner-token",
        "owner-transaction-read-missing-agent-token",
        "owner-audit-read-missing-agent-token",
        "owner-receipt-read-missing-agent-token",
        "owner-approval-missing-agent-token",
        "owner-execution-missing-agent-token",
        "owner-rejection-missing-agent-token",
    ],
)
def test_workflow_endpoint_fails_closed_when_other_token_is_missing(
    monkeypatch, method, path, configured_env, configured_token
):
    monkeypatch.setenv(configured_env, configured_token)
    missing_env = (
        "AEGIS_APPROVAL_TOKEN"
        if configured_env == "AEGIS_AGENT_TOKEN"
        else "AEGIS_AGENT_TOKEN"
    )
    monkeypatch.delenv(missing_env, raising=False)

    response = _request(
        method,
        path,
        {"Authorization": f"Bearer {configured_token}"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Both AEGIS_AGENT_TOKEN and AEGIS_APPROVAL_TOKEN must be configured; endpoint fails closed"
    }



@pytest.mark.parametrize(
    "path",
    [
        "/chain/base-sepolia/status",
        "/chain/base-sepolia/token/0x1111111111111111111111111111111111111111",
    ],
    ids=["chain-status", "token-inspection"],
)
def test_read_only_chain_endpoints_accept_owner_token_without_agent_token(
    monkeypatch, path
):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_TOKEN)
    monkeypatch.delenv("AEGIS_AGENT_TOKEN", raising=False)

    class FakeRpc:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def chain_id(self):
            return 84532

        def verify_token(self, address):
            return {"address": address, "mode": "read-only"}

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", FakeRpc)
    response = client.get(path, headers={"Authorization": f"Bearer {OWNER_TOKEN}"})

    assert response.status_code == 200
    if path.endswith("/status"):
        assert response.json()["mode"] == "read-only"
        assert response.json()["is_base_sepolia"] is True
    else:
        assert response.json() == {
            "address": "0x1111111111111111111111111111111111111111",
            "mode": "read-only",
        }
