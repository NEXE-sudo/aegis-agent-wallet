"""Base Sepolia JSON-RPC helpers for metadata, execution, and receipts."""
from __future__ import annotations

import os
import re
from typing import Any, Self

import httpx

BASE_SEPOLIA_CHAIN_ID = 84532
# Circle's published USDC contract address on Base Sepolia (testnet only).
BASE_SEPOLIA_USDC_ADDRESS = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_QUANTITY_RE = re.compile(r"^0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)$")
_DATA_RE = re.compile(r"^0x(?:[0-9a-fA-F]{2})*$")
_TRANSACTION_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_MAX_TOKEN_METADATA_BYTES = 256


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
        if len(raw.rstrip(b"\x00")) > _MAX_TOKEN_METADATA_BYTES:
            raise RpcRequestError("RPC returned oversized token metadata")
        return raw.rstrip(b"\x00").decode("utf-8", errors="replace")
    if len(raw) < 64:
        raise RpcRequestError("RPC returned truncated ABI string data")
    offset = int.from_bytes(raw[:32], "big")
    if offset < 32 or offset % 32 != 0 or offset + 32 > len(raw):
        raise RpcRequestError("RPC returned invalid ABI string offset")
    length = int.from_bytes(raw[offset:offset + 32], "big")
    start = offset + 32
    end = start + length
    if length > _MAX_TOKEN_METADATA_BYTES:
        raise RpcRequestError("RPC returned oversized token metadata")
    if end > len(raw):
        raise RpcRequestError("RPC returned truncated ABI string")
    return raw[start:end].decode("utf-8", errors="replace")


class BaseSepoliaRpc:
    """Minimal JSON-RPC client for Base Sepolia reads and signed transaction submission."""

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
        if (
            payload.get("jsonrpc") != "2.0"
            or type(payload.get("id")) is not int
            or payload["id"] != 1
        ):
            raise RpcRequestError("RPC response has an invalid JSON-RPC envelope")
        if "error" in payload:
            raise RpcRequestError(f"RPC method {method} returned an error")
        if "result" not in payload:
            raise RpcRequestError("RPC response did not include a result")
        return payload["result"]

    def chain_id(self) -> int:
        result = self._rpc("eth_chainId", [])
        if not isinstance(result, str) or not _QUANTITY_RE.fullmatch(result):
            raise RpcRequestError("RPC returned an invalid chain ID")
        try:
            return int(result, 16)
        except ValueError as exc:
            raise RpcRequestError("RPC returned an invalid chain ID") from exc

    def transaction_count(self, address: str) -> int:
        result = self._rpc(
            "eth_getTransactionCount", [validate_address(address), "pending"]
        )
        return self._parse_quantity(result, "transaction count")

    def gas_price(self) -> int:
        return self._parse_quantity(self._rpc("eth_gasPrice", []), "gas price")

    def estimate_gas(self, transaction: dict[str, str]) -> int:
        return self._parse_quantity(
            self._rpc("eth_estimateGas", [transaction]), "gas estimate"
        )

    def send_raw_transaction(self, raw_transaction: str) -> str:
        if not isinstance(raw_transaction, str) or not _DATA_RE.fullmatch(raw_transaction):
            raise RpcRequestError("Signed transaction data is malformed")
        result = self._rpc("eth_sendRawTransaction", [raw_transaction])
        if not isinstance(result, str) or not _TRANSACTION_HASH_RE.fullmatch(result):
            raise RpcRequestError("RPC returned an invalid transaction hash")
        return result.lower()

    def transaction_by_hash(self, transaction_hash: str) -> dict[str, str] | None:
        if not isinstance(transaction_hash, str) or not _TRANSACTION_HASH_RE.fullmatch(
            transaction_hash
        ):
            raise ValueError("Transaction hash must be 32-byte hexadecimal data")
        result = self._rpc("eth_getTransactionByHash", [transaction_hash])
        if result is None:
            return None
        if not isinstance(result, dict):
            raise RpcRequestError("RPC returned a malformed transaction")
        returned_hash = result.get("hash")
        sender = result.get("from")
        recipient = result.get("to")
        calldata = result.get("input")
        if (
            not isinstance(returned_hash, str)
            or not _TRANSACTION_HASH_RE.fullmatch(returned_hash)
            or returned_hash.lower() != transaction_hash.lower()
            or not isinstance(sender, str)
            or not _ADDRESS_RE.fullmatch(sender)
            or not isinstance(recipient, str)
            or not _ADDRESS_RE.fullmatch(recipient)
            or not isinstance(calldata, str)
            or not _DATA_RE.fullmatch(calldata)
        ):
            raise RpcRequestError("RPC returned a malformed transaction")
        return {
            "transaction_hash": returned_hash.lower(),
            "from": sender.lower(),
            "to": recipient.lower(),
            "input": calldata.lower(),
        }

    def transaction_receipt(self, transaction_hash: str) -> dict[str, int | str | None]:
        if not isinstance(transaction_hash, str) or not _TRANSACTION_HASH_RE.fullmatch(
            transaction_hash
        ):
            raise ValueError("Transaction hash must be 32-byte hexadecimal data")
        result = self._rpc("eth_getTransactionReceipt", [transaction_hash])
        if result is None:
            return {
                "transaction_hash": transaction_hash.lower(),
                "status": "pending",
                "block_number": None,
            }
        if not isinstance(result, dict):
            raise RpcRequestError("RPC returned a malformed transaction receipt")
        returned_hash = result.get("transactionHash")
        status = result.get("status")
        if (
            not isinstance(returned_hash, str)
            or not _TRANSACTION_HASH_RE.fullmatch(returned_hash)
            or returned_hash.lower() != transaction_hash.lower()
            or not isinstance(status, str)
            or status not in {"0x0", "0x1"}
        ):
            raise RpcRequestError("RPC returned a malformed transaction receipt")
        block_number = self._parse_quantity(result.get("blockNumber"), "receipt block number")
        return {
            "transaction_hash": returned_hash.lower(),
            "status": "success" if status == "0x1" else "failed",
            "block_number": block_number,
        }

    @staticmethod
    def _parse_quantity(value: Any, field_name: str) -> int:
        if not isinstance(value, str) or not _QUANTITY_RE.fullmatch(value):
            raise RpcRequestError(f"RPC returned an invalid {field_name}")
        try:
            return int(value, 16)
        except ValueError as exc:
            raise RpcRequestError(f"RPC returned an invalid {field_name}") from exc

    def get_code(self, address: str) -> str:
        result = self._rpc("eth_getCode", [validate_address(address), "latest"])
        if not isinstance(result, str) or not _DATA_RE.fullmatch(result):
            raise RpcRequestError("RPC returned malformed contract bytecode")
        return result

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
                "warnings": ["No contract bytecode found at this address."],
            }

        warnings: list[str] = []
        try:
            symbol = _decode_abi_string(self._call(normalized, "0x95d89b41"))
            decimals_result = self._call(normalized, "0x313ce567")
            if not isinstance(decimals_result, str) or not re.fullmatch(
                r"0x[0-9a-fA-F]{64}", decimals_result
            ):
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
            "warnings": warnings + [
                "This confirms bytecode and readable metadata only; it does not establish that the token is official, safe, or liquid."
            ],
        }
