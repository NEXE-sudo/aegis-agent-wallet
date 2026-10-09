from fastapi.testclient import TestClient

import app.chain.router as chain_router
import app.main as main

client = TestClient(main.app)
OWNER_HEADERS = {"Authorization": "Bearer test-owner-token-with-enough-length"}


def test_chain_status_requires_owner_authentication(monkeypatch):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1])
    response = client.get("/chain/base-sepolia/status")
    assert response.status_code == 401


def test_chain_status_fails_closed_without_rpc_url(monkeypatch):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1])
    monkeypatch.delenv("BASE_SEPOLIA_RPC_URL", raising=False)
    response = client.get("/chain/base-sepolia/status", headers=OWNER_HEADERS)
    assert response.status_code == 503
    assert "BASE_SEPOLIA_RPC_URL" in response.json()["detail"]


def test_chain_status_reports_read_only_mode(monkeypatch):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1])

    class FakeRpc:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def chain_id(self):
            return 84532

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", FakeRpc)
    response = client.get("/chain/base-sepolia/status", headers=OWNER_HEADERS)
    assert response.status_code == 200
    assert response.json() == {
        "connected": True,
        "chain_id": 84532,
        "is_base_sepolia": True,
        "expected_chain_id": 84532,
        "mode": "read-only",
    }
