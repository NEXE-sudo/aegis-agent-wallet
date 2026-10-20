"""Verify a mined AegisVault deployment using read-only Base Sepolia RPC calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from app.chain.base_sepolia import (
    BASE_SEPOLIA_CHAIN_ID,
    BaseSepoliaRpc,
    RpcConfigurationError,
    RpcRequestError,
    validate_address,
)

_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_DATA_RE = re.compile(r"^0x(?:[0-9a-fA-F]{2})*$")
_QUANTITY_RE = re.compile(r"^0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)$")
_OWNER_SELECTOR = "0x8da5cb5b"


def _quantity(value: Any, name: str) -> int:
    if not isinstance(value, str) or not _QUANTITY_RE.fullmatch(value):
        raise RpcRequestError(f"RPC returned an invalid deployment {name}")
    return int(value, 16)


def _deployment_transaction(rpc: BaseSepoliaRpc, transaction_hash: str) -> dict[str, str]:
    result = rpc._rpc("eth_getTransactionByHash", [transaction_hash])
    if result is None:
        raise ValueError("Deployment transaction was not found")
    if not isinstance(result, dict):
        raise RpcRequestError("RPC returned a malformed deployment transaction")
    returned_hash = result.get("hash")
    sender = result.get("from")
    recipient = result.get("to")
    data = result.get("input", result.get("data"))
    if (
        "to" not in result
        or not isinstance(returned_hash, str)
        or not _HASH_RE.fullmatch(returned_hash)
        or returned_hash.lower() != transaction_hash
        or not isinstance(sender, str)
        or not re.fullmatch(r"0x[0-9a-fA-F]{40}", sender)
        or recipient is not None
        or not isinstance(data, str)
        or not _DATA_RE.fullmatch(data)
        or len(data) < 2 + 64
    ):
        raise ValueError("Transaction is not a valid contract-creation transaction")
    return {"transaction_hash": returned_hash.lower(), "sender": sender.lower(), "input": data.lower()}


def verify_deployment(
    transaction_hash: str,
    rpc: BaseSepoliaRpc,
    *,
    expected_vault: str,
    expected_owner: str,
    min_confirmations: int = 1,
    expected_bytecode_sha256: str | None = None,
) -> dict[str, Any]:
    """Check deployment receipt, created address, constructor owner and current runtime code."""
    if not isinstance(transaction_hash, str) or not _HASH_RE.fullmatch(transaction_hash):
        raise ValueError("Deployment transaction hash must be 32-byte hexadecimal data")
    transaction_hash = transaction_hash.lower()
    vault = validate_address(expected_vault)
    owner = validate_address(expected_owner)
    if vault == "0x" + "0" * 40 or owner == "0x" + "0" * 40:
        raise ValueError("Expected vault and owner must be non-zero addresses")
    if type(min_confirmations) is not int or min_confirmations < 1:
        raise ValueError("Minimum confirmations must be at least 1")
    if expected_bytecode_sha256 is not None and not re.fullmatch(
        r"(?:0x)?[0-9a-fA-F]{64}", expected_bytecode_sha256
    ):
        raise ValueError("Expected bytecode SHA-256 must contain exactly 32 bytes")

    chain_id = rpc.chain_id()
    if chain_id != BASE_SEPOLIA_CHAIN_ID:
        raise ValueError(f"RPC endpoint is on chain {chain_id}, not Base Sepolia (84532)")

    transaction = _deployment_transaction(rpc, transaction_hash)
    constructor_owner_word = transaction["input"][-64:]
    if constructor_owner_word[:24] != "0" * 24:
        raise ValueError("Deployment constructor owner argument is malformed")
    constructor_owner = validate_address("0x" + constructor_owner_word[24:])
    if constructor_owner != owner:
        raise ValueError("Deployment constructor owner does not match --expected-owner")

    receipt = rpc._rpc("eth_getTransactionReceipt", [transaction_hash])
    if receipt is None:
        raise ValueError("Deployment transaction receipt is not available yet")
    if not isinstance(receipt, dict):
        raise RpcRequestError("RPC returned a malformed deployment receipt")
    receipt_hash = receipt.get("transactionHash")
    if (
        not isinstance(receipt_hash, str)
        or not _HASH_RE.fullmatch(receipt_hash)
        or receipt_hash.lower() != transaction_hash
    ):
        raise RpcRequestError("Deployment receipt transaction hash does not match")
    if receipt.get("status") != "0x1":
        raise ValueError("Deployment transaction receipt status is not success")
    contract_address = receipt.get("contractAddress")
    if not isinstance(contract_address, str) or not re.fullmatch(
        r"0x[0-9a-fA-F]{40}", contract_address
    ):
        raise RpcRequestError("Deployment receipt has no valid contract address")
    contract_address = contract_address.lower()
    if contract_address != vault:
        raise ValueError("Created contract address does not match --expected-vault")

    block_number = _quantity(receipt.get("blockNumber"), "receipt block number")
    block_hash = receipt.get("blockHash")
    if not isinstance(block_hash, str) or not _HASH_RE.fullmatch(block_hash):
        raise RpcRequestError("Deployment receipt has an invalid block hash")
    canonical_hash = rpc.block_hash_by_number(block_number)
    if block_hash.lower() != canonical_hash:
        raise ValueError("Deployment receipt block hash does not match the RPC's block at that height")
    latest_block = rpc.block_number()
    if latest_block < block_number:
        raise RpcRequestError("RPC latest block is behind the deployment receipt")
    confirmations = latest_block - block_number + 1
    if confirmations < min_confirmations:
        raise ValueError(
            f"Observed {confirmations} confirmations, fewer than the {min_confirmations} required"
        )

    code = rpc.get_code(vault)
    if not isinstance(code, str) or not _DATA_RE.fullmatch(code) or code.lower() in {"0x", "0x0"}:
        raise ValueError("No deployed runtime bytecode found at the expected vault address")
    bytecode_hash = hashlib.sha256(bytes.fromhex(code[2:])).hexdigest()
    if expected_bytecode_sha256 is not None:
        expected_hash = expected_bytecode_sha256.removeprefix("0x").lower()
        if bytecode_hash != expected_hash:
            raise ValueError("Runtime bytecode SHA-256 does not match --expected-bytecode-sha256")

    owner_result = rpc._rpc(
        "eth_call",
        [{"to": vault, "data": _OWNER_SELECTOR}, "latest"],
    )
    if not isinstance(owner_result, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", owner_result):
        raise RpcRequestError("RPC returned malformed AegisVault owner() data")
    owner_word = owner_result[2:]
    if owner_word[:24] != "0" * 24:
        raise RpcRequestError("RPC returned malformed AegisVault owner address")
    current_owner = validate_address("0x" + owner_word[24:])
    if current_owner != owner:
        raise ValueError("Current on-chain vault owner does not match --expected-owner")

    return {
        "chain_id": chain_id,
        "transaction_hash": transaction_hash,
        "deployer": transaction["sender"],
        "vault_address": contract_address,
        "expected_owner": owner,
        "constructor_owner": constructor_owner,
        "current_onchain_owner": current_owner,
        "receipt_status": "success",
        "block_number": block_number,
        "receipt_block_hash": block_hash.lower(),
        "canonical_block_hash": canonical_hash,
        "latest_block_number": latest_block,
        "confirmations_observed": confirmations,
        "minimum_confirmations_required": min_confirmations,
        "runtime_bytecode_sha256": bytecode_hash,
        "expected_bytecode_hash_matched": expected_bytecode_sha256 is None or (
            bytecode_hash == expected_bytecode_sha256.removeprefix("0x").lower()
        ),
        "explorer_url": f"https://sepolia.basescan.org/tx/{transaction_hash}",
        "read_only": True,
        "limitations": [
            "RPC observations are from the configured provider and are not an independent consensus proof.",
            "Observed confirmations are not a guarantee of finality.",
            "Matching bytecode and owner do not constitute a contract audit.",
            "The AegisVault is unaudited and testnet-only.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify an already-mined AegisVault deployment with read-only Base Sepolia RPC calls."
    )
    parser.add_argument("transaction_hash", help="Base Sepolia contract-creation transaction hash")
    parser.add_argument("--expected-vault", required=True, help="contract address reported by the deployment receipt")
    parser.add_argument("--expected-owner", required=True, help="expected initial and current vault owner")
    parser.add_argument("--min-confirmations", type=int, default=12)
    parser.add_argument("--expected-bytecode-sha256", help="optional expected runtime bytecode SHA-256 (hex)")
    parser.add_argument("--output", type=Path, help="optional path to write the machine-readable JSON evidence")
    args = parser.parse_args(argv)

    try:
        with BaseSepoliaRpc() as rpc:
            result = verify_deployment(
                args.transaction_hash,
                rpc,
                expected_vault=args.expected_vault,
                expected_owner=args.expected_owner,
                min_confirmations=args.min_confirmations,
                expected_bytecode_sha256=args.expected_bytecode_sha256,
            )
    except (OSError, RpcConfigurationError, RpcRequestError, TypeError, ValueError) as exc:
        print(f"Deployment verification failed: {exc}", file=sys.stderr)
        return 1

    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    result["deployment_evidence_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\\n"
    if args.output is not None:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        except OSError as exc:
            print(f"Could not write deployment evidence: {exc}", file=sys.stderr)
            return 1
    print("AegisVault deployment verification: PASS")
    print(rendered, end="")
    if args.output is not None:
        print(f"JSON evidence: {args.output}")
    print("Read-only verification; no transaction was signed or broadcast.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
