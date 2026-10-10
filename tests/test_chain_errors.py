"""Regression tests for safe handling of malformed JSON-RPC responses."""
import httpx
import pytest
from fastapi.testclient import TestClient

import app.chain.router as chain_router
from app.chain.base_sepolia import BaseSepoliaRpc, RpcRequestError
from app.main import app

OWNER_TOKEN = "test-owner-token-for-rpc-error-tests"


def _rpc_with_payload(payload):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)
    )
    return BaseSepoliaRpc(
        url="https://provider.example/rpc?api_key=must-not-leak",
        transport=transport,
    )


def test_rpc_rejects_non_object_json_response():
    with _rpc_with_payload([]) as rpc:
        with pytest.raises(RpcRequestError, match="RPC response must be a JSON object"):
            rpc.chain_id()


def test_rpc_api_maps_malformed_response_to_sanitized_502(monkeypatch):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_TOKEN)
    monkeypatch.setenv("AEGIS_AGENT_TOKEN", "different-agent-token")
    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", lambda: _rpc_with_payload("invalid"))

    response = TestClient(app).get(
        "/chain/base-sepolia/status",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "RPC response must be a JSON object"
    assert "must-not-leak" not in response.text
