"""Regression tests for safe handling of malformed JSON-RPC responses."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import app.chain.router as chain_router
from app.chain.base_sepolia import BaseSepoliaRpc, RpcRequestError
from app.main import app

OWNER_TOKEN = "test-owner-token-for-rpc-error-tests"
TOKEN_ADDRESS = "0x2222222222222222222222222222222222222222"


def _rpc_with_payload(payload):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)
    )
    return BaseSepoliaRpc(
        url="https://provider.example/rpc?api_key=must-not-leak",
        transport=transport,
    )


def _abi_string(value):
    encoded = value.encode("utf-8")
    padded = encoded + b"\\x00" * ((32 - len(encoded) % 32) % 32)
    return "0x" + (32).to_bytes(32, "big").hex() + len(encoded).to_bytes(32, "big").hex() + padded.hex()


def _rpc_with_token_result(selector, malformed_result):
    def respond(request):
        body = json.loads(request.content)
        method = body["method"]
        if method == "eth_chainId":
            result = "0x14a34"
        elif method == "eth_getCode":
            result = "0x6000"
        elif method == "eth_call":
            call_selector = body["params"][0]["data"]
            if call_selector == selector:
                result = malformed_result
            elif call_selector == "0x95d89b41":
                result = _abi_string("USDC")
            elif call_selector == "0x313ce567":
                result = "0x6"
            else:
                result = _abi_string("USD Coin")
        else:
            raise AssertionError(f"Unexpected JSON-RPC method: {method}")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    transport = httpx.MockTransport(respond)
    return BaseSepoliaRpc(
        url="https://provider.example/rpc?api_key=must-not-leak",
        transport=transport,
    )


def test_rpc_rejects_non_object_json_response():
    with (
        _rpc_with_payload([]) as rpc,
        pytest.raises(RpcRequestError, match="RPC response must be a JSON object"),
    ):
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


@pytest.mark.parametrize(
    ("selector", "malformed_result"),
    [
        ("0x95d89b41", []),
        ("0x313ce567", {"unexpected": "object"}),
    ],
)
def test_token_inspection_maps_malformed_metadata_result_to_sanitized_502(
    monkeypatch, selector, malformed_result
):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_TOKEN)
    monkeypatch.setenv("AEGIS_AGENT_TOKEN", "different-agent-token")
    monkeypatch.setattr(
        chain_router,
        "BaseSepoliaRpc",
        lambda: _rpc_with_token_result(selector, malformed_result),
    )

    response = TestClient(app).get(
        f"/chain/base-sepolia/token/{TOKEN_ADDRESS}",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "Contract has bytecode but standard ERC-20 symbol/decimals calls failed"
    )
    assert "must-not-leak" not in response.text
