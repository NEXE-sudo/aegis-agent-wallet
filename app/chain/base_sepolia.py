"""Read-only JSON-RPC helpers for Base Sepolia. No signing or transaction submission."""
from __future__ import annotations

import os
import re
from typing import Any, Self

import httpx

BASE_SEPOLIA_CHAIN_ID = 84532
# Circle's published USDC contract address on Base Sepolia (testnet only).
BASE_SEPOLIA_USDC_ADDRESS = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class RpcConfigurationError(RuntimeError):
    pass


class RpcRequestError(RuntimeError):
    pass


def validate_address(address: str) -> str:
    if not _ADDRESS_RE.fullmatch(address):
        raise ValueError("Address must be a 20-byte hexadecimal EVM address")
    return address.lower()


def _decode_abi_string(value: str) -> str:
    """Decode standard ABI string return data, with bytes32 compatibility."""
    if not isinstance(value, str) or not value.startswith("0x"):
        raise RpcRequestError("RPC returned malformed ABI data")
    raw = bytes.fromhex(value[2:])
    if len(raw) == 32:
        return raw.rstrip(b"\\x00").decode("utf-8", errors="replace")
    if len(raw) < 64:
        raise RpcRequestError("RPC returned truncated ABI string data")
    offset = int.from_bytes(raw[:32], "big")
    if offset + 32 > len(raw):
        raise RpcRequestError("RPC returned invalid ABI string offset")
    length = int.from_bytes(raw[offset:offset + 32], "big")
    start = offset + 32
    end = start + length
    if end > len(raw):
        raise RpcRequestError("RPC returned truncated ABI string")
    return raw[start:end].decode("utf-8", errors="replace")


class BaseSepoliaRpc:
    """Minimal JSON-RPC client limited to chain metadata and ERC-20 read calls."""

    def __init__(self, url: str | None = None, transport: httpx.BaseTransport | None = None):
        self.url = url or os.environ.get("BASE_SEPOLIA_RPC_URL", "")
        if not self.url:
            raise RpcConfigurationError("BASE_SEPOLIA_RPC_URL is not configured")
        if not self.url.startswith(("https://", "http://")):
            raise RpcConfigurationError("BASE_SEPOLIA_RPC_URL must be an HTTP(S) URL")
        self._client = httpx.Client(timeout=5.0, transport=transport)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _rpc(self, method: str, params: list[Any]) -> Any:
        try:
            response = self._client.post(
                self.url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RpcRequestError("Base Sepolia RPC request failed") from exc
        if not isinstance(payload, dict):
            raise RpcRequestError("RPC response must be a JSON object")
        if payload.get("error"):
            raise RpcRequestError(f"RPC method {method} returned an error")
        if "result" not in payload:
            raise RpcRequestError("RPC response did not include a result")
        return payload["result"]

    def chain_id(self) -> int:
        result = self._rpc("eth_chainId", [])
        try:
            return int(result, 16)
        except (TypeError, ValueError) as exc:
            raise RpcRequestError("RPC returned an invalid chain ID") from exc

    def get_code(self, address: str) -> str:
        return self._rpc("eth_getCode", [validate_address(address), "latest"])

    def _call(self, address: str, selector: str) -> str:
        return self._rpc("eth_call", [{"to": validate_address(address), "data": selector}, "latest"])

    def verify_token(self, address: str) -> dict[str, Any]:
        normalized = validate_address(address)
        chain_id = self.chain_id()
        if chain_id != BASE_SEPOLIA_CHAIN_ID:
            raise RpcRequestError(f"RPC endpoint is on chain {chain_id}, not Base Sepolia (84532)")
        code = self.get_code(normalized)
        if not isinstance(code, str) or code in {"", "0x", "0x0"}:
            return {
                "address": normalized,
                "chain_id": chain_id,
                "has_contract_code": False,
                "erc20_metadata_readable": False,
                "name": None,
                "symbol": None,
                "decimals": None,
                "verified": False,
                "warnings": ["No contract bytecode found at this address."],
            }

        warnings: list[str] = []
        try:
            symbol = _decode_abi_string(self._call(normalized, "0x95d89b41"))
            decimals_result = self._call(normalized, "0x313ce567")
            if not isinstance(decimals_result, str) or not decimals_result.startswith("0x"):
                raise RpcRequestError("RPC returned malformed token decimals data")
            decimals = int(decimals_result, 16)
            if not 0 <= decimals <= 36:
                raise RpcRequestError("Token decimals are outside the supported range")
        except (RpcRequestError, ValueError) as exc:
            raise RpcRequestError("Contract has bytecode but standard ERC-20 symbol/decimals calls failed") from exc

        name = None
        try:
            name = _decode_abi_string(self._call(normalized, "0x06fdde03"))
        except (RpcRequestError, ValueError):
            warnings.append("The optional ERC-20 name() call was unavailable.")

        return {
            "address": normalized,
            "chain_id": chain_id,
            "has_contract_code": True,
            "erc20_metadata_readable": True,
            "name": name,
            "symbol": symbol,
            "decimals": decimals,
            "verified": True,
            "warnings": warnings + [
                "This confirms bytecode and readable metadata only; it does not establish that the token is official, safe, or liquid."
            ],
        }
