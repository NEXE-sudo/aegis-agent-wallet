import json

import httpx
import pytest

from app.chain.base_sepolia import (
    BASE_SEPOLIA_CHAIN_ID,
    BaseSepoliaRpc,
    RpcConfigurationError,
    RpcRequestError,
    validate_address,
)

TOKEN = "0x1111111111111111111111111111111111111111"


def _abi_string(value: str) -> str:
    raw = value.encode()
    padded = raw + b"\x00" * ((32 - len(raw) % 32) % 32)
    return "0x" + (
        (32).to_bytes(32, "big") + len(raw).to_bytes(32, "big") + padded
    ).hex()


def _rpc_transport(methods):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        result = methods.get(body["method"])
        if isinstance(result, Exception):
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"message": str(result)}})
        if callable(result):
            result = result(body["params"])
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})
    return httpx.MockTransport(handler)


def test_rpc_requires_configured_http_url(monkeypatch):
    monkeypatch.delenv("BASE_SEPOLIA_RPC_URL", raising=False)
    with pytest.raises(RpcConfigurationError):
        BaseSepoliaRpc()


def test_address_validation_normalizes_and_rejects_invalid():
    assert validate_address(TOKEN.upper().replace("0X", "0x")) == TOKEN
    with pytest.raises(ValueError):
        validate_address("not-an-address")


def test_chain_id_reads_base_sepolia():
    rpc = BaseSepoliaRpc(
        "https://rpc.invalid",
        transport=_rpc_transport({"eth_chainId": hex(BASE_SEPOLIA_CHAIN_ID)}),
    )
    assert rpc.chain_id() == BASE_SEPOLIA_CHAIN_ID


def test_wrong_chain_rejected_before_token_inspection():
    rpc = BaseSepoliaRpc(
        "https://rpc.invalid",
        transport=_rpc_transport({"eth_chainId": hex(1)}),
    )
    with pytest.raises(RpcRequestError, match="not Base Sepolia"):
        rpc.verify_token(TOKEN)


def test_token_metadata_is_read_without_signing_or_sending_transactions():
    methods = {
        "eth_chainId": hex(BASE_SEPOLIA_CHAIN_ID),
        "eth_getCode": "0x60016000",
        "eth_call": lambda params: {
            "0x95d89b41": _abi_string("USDC"),
            "0x313ce567": "0x" + hex(6)[2:].zfill(64),
            "0x06fdde03": _abi_string("Example USD Coin"),
        }[params[0]["data"]],
    }
    rpc = BaseSepoliaRpc("https://rpc.invalid", transport=_rpc_transport(methods))
    result = rpc.verify_token(TOKEN)
    assert result["chain_id"] == BASE_SEPOLIA_CHAIN_ID
    assert result["has_contract_code"] is True
    assert result["erc20_metadata_readable"] is True
    assert result["symbol"] == "USDC"
    assert result["decimals"] == 6
    assert result["verified"] is True
    assert any("does not establish" in warning for warning in result["warnings"])


def test_address_without_code_is_not_marked_verified():
    rpc = BaseSepoliaRpc(
        "https://rpc.invalid",
        transport=_rpc_transport({
            "eth_chainId": hex(BASE_SEPOLIA_CHAIN_ID),
            "eth_getCode": "0x",
        }),
    )
    result = rpc.verify_token(TOKEN)
    assert result["has_contract_code"] is False
    assert result["verified"] is False


def test_rpc_error_is_not_treated_as_valid_result():
    rpc = BaseSepoliaRpc(
        "https://rpc.invalid",
        transport=_rpc_transport({"eth_chainId": RuntimeError("offline")}),
    )
    with pytest.raises(RpcRequestError):
        rpc.chain_id()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"jsonrpc": "1.0", "id": 1, "result": "0x14a34"},
        {"jsonrpc": "2.0", "id": "1", "result": "0x14a34"},
        {"jsonrpc": "2.0", "id": 2, "result": "0x14a34"},
        {"jsonrpc": "2.0", "id": 1},
    ],
    ids=[
        "non-object-envelope",
        "wrong-jsonrpc-version",
        "string-request-id",
        "mismatched-request-id",
        "missing-result",
    ],
)
def test_rpc_rejects_malformed_json_rpc_envelopes(payload):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)
    )
    rpc = BaseSepoliaRpc("https://rpc.invalid", transport=transport)

    with pytest.raises(RpcRequestError):
        rpc.chain_id()


@pytest.mark.parametrize("malformed_code", [[], "6000", "0x600", "0xgg"])
def test_get_code_rejects_malformed_bytecode_result(malformed_code):
    rpc = BaseSepoliaRpc(
        "https://rpc.invalid",
        transport=_rpc_transport({"eth_getCode": malformed_code}),
    )
    with pytest.raises(RpcRequestError, match="RPC returned malformed contract bytecode"):
        rpc.get_code(TOKEN)
