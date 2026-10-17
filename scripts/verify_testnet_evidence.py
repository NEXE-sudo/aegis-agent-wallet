"""Read-only verification of an ERC-20 transfer on Base Sepolia."""
from __future__ import annotations

import argparse
import sys

from app.chain.base_sepolia import (
    BASE_SEPOLIA_CHAIN_ID,
    BaseSepoliaRpc,
    RpcConfigurationError,
    RpcRequestError,
    validate_address,
)

TRANSFER_SELECTOR = "a9059cbb"
TRANSFER_CALLDATA_LENGTH = 10 + 64 + 64


def verify_evidence(transaction_hash: str, rpc: BaseSepoliaRpc) -> dict[str, int | str]:
    chain_id = rpc.chain_id()
    if chain_id != BASE_SEPOLIA_CHAIN_ID:
        raise ValueError(
            f"RPC endpoint is on chain {chain_id}, not Base Sepolia "
            f"({BASE_SEPOLIA_CHAIN_ID})"
        )

    transaction = rpc.transaction_by_hash(transaction_hash)
    if transaction is None:
        raise ValueError("Transaction was not found on Base Sepolia")

    receipt = rpc.transaction_receipt(transaction_hash)
    if receipt["status"] == "pending":
        raise ValueError("Transaction receipt is not available yet")
    block_number = receipt["block_number"]
    if not isinstance(block_number, int):
        raise RpcRequestError("RPC returned a receipt without a block number")

    calldata = transaction["input"]
    if (
        len(calldata) != TRANSFER_CALLDATA_LENGTH
        or calldata[:10] != f"0x{TRANSFER_SELECTOR}"
    ):
        raise ValueError("Transaction calldata is not an ERC-20 transfer(address,uint256)")

    recipient_word = calldata[10:74]
    if recipient_word[:24] != "0" * 24:
        raise ValueError("ERC-20 transfer recipient is not a valid ABI-encoded address")
    recipient = validate_address(f"0x{recipient_word[24:]}")
    amount_base_units = int(calldata[74:138], 16)

    return {
        "chain_id": chain_id,
        "receipt_status": str(receipt["status"]),
        "block_number": block_number,
        "token_contract": validate_address(transaction["to"]),
        "recipient": recipient,
        "amount_base_units": amount_base_units,
        "transaction_hash": transaction_hash.lower(),
        "explorer_url": f"https://sepolia.basescan.org/tx/{transaction_hash.lower()}",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a Base Sepolia ERC-20 transfer using read-only RPC calls."
    )
    parser.add_argument("transaction_hash", help="32-byte transaction hash")
    args = parser.parse_args(argv)

    try:
        with BaseSepoliaRpc() as rpc:
            evidence = verify_evidence(args.transaction_hash, rpc)
    except (RpcConfigurationError, RpcRequestError, ValueError) as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1

    print(f"Chain ID: {evidence['chain_id']} (Base Sepolia; expected 84532)")
    print(f"Transaction hash: {evidence['transaction_hash']}")
    print(f"Receipt status: {evidence['receipt_status']}")
    print(f"Block number: {evidence['block_number']}")
    print(f"Token contract: {evidence['token_contract']}")
    print(f"Recipient: {evidence['recipient']}")
    print(f"Amount (base units): {evidence['amount_base_units']}")
    print(f"Base Sepolia explorer: {evidence['explorer_url']}")
    if evidence["receipt_status"] != "success":
        print("Verification failed: the transaction receipt status is not success.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
