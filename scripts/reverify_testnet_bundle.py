"""Re-query Base Sepolia to reverify testnet evidence in a saved Aegis bundle."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.chain.base_sepolia import (
    BaseSepoliaRpc,
    RpcConfigurationError,
    RpcRequestError,
    validate_address,
)
from scripts.verify_evidence_bundle import validate_bundle
from scripts.verify_testnet_evidence import verify_audit_record, verify_evidence

_RECHECK_FIELDS = (
    "chain_id",
    "receipt_status",
    "block_number",
    "receipt_block_hash",
    "canonical_block_hash",
    "token_contract",
    "sender",
    "recipient",
    "amount_base_units",
    "transaction_hash",
    "vault_address",
    "proposal_reference",
)


def reverify_testnet_evidence(
    bundle: dict[str, Any],
    *,
    expected_sender: str,
    audit_db: str | Path | None = None,
    min_confirmations: int | None = None,
) -> dict[str, Any]:
    """Re-query the chain and compare a passing bundle's testnet evidence."""
    validate_bundle(bundle)
    if bundle["passed"] is not True:
        raise ValueError("Only an overall PASS bundle can be reverified")
    testnet = bundle["testnet_evidence"]
    if testnet.get("requested") is not True or testnet.get("passed") is not True:
        raise ValueError("Bundle does not contain passing testnet evidence")
    saved = testnet.get("evidence")
    if not isinstance(saved, dict):
        raise TypeError("Bundle transaction evidence must be a JSON object")

    mode = testnet.get("mode", "successful_transfer")
    if mode not in {"successful_transfer", "reverted_vault_call"}:
        raise ValueError(f"Unsupported testnet evidence mode: {mode!r}")
    expect_revert = mode == "reverted_vault_call"
    if expect_revert and audit_db is not None:
        raise ValueError("--audit-db cannot be used with reverted-call evidence")
    if not expect_revert and audit_db is None:
        raise ValueError("Successful-transfer evidence requires --audit-db")

    try:
        sender = validate_address(expected_sender)
        vault = validate_address(saved["vault_address"])
        transaction_hash = saved["transaction_hash"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Bundle is missing valid sender, vault, or transaction evidence") from exc

    required_depth = (
        testnet.get("minimum_confirmations_required", 1)
        if min_confirmations is None
        else min_confirmations
    )
    if type(required_depth) is not int or required_depth < 1:
        raise ValueError("Minimum confirmations must be at least 1")

    with BaseSepoliaRpc() as rpc:
        current = verify_evidence(
            transaction_hash,
            rpc,
            vault,
            expect_revert=expect_revert,
        )

    if current["sender"] != sender:
        raise ValueError("Current transaction sender does not match --expected-sender")
    for field in _RECHECK_FIELDS:
        if current.get(field) != saved.get(field):
            raise ValueError(f"Current chain evidence differs from bundle field: {field}")
    confirmations = current.get("confirmations_observed")
    if type(confirmations) is not int or confirmations < required_depth:
        raise ValueError(
            f"Observed {confirmations!r} confirmations, fewer than the "
            f"{required_depth} required"
        )

    audit_transaction_id = None
    if not expect_revert:
        audit_transaction_id = verify_audit_record(audit_db, current)

    return {
        "mode": mode,
        "transaction_hash": current["transaction_hash"],
        "receipt_status": current["receipt_status"],
        "confirmations_observed": confirmations,
        "minimum_confirmations_required": required_depth,
        "audit_transaction_id": audit_transaction_id,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Re-query Base Sepolia and reverify the on-chain evidence in a saved Aegis bundle."
        )
    )
    parser.add_argument("bundle_path", type=Path, help="path to an evidence-bundle JSON file")
    parser.add_argument(
        "--expected-sender",
        required=True,
        help="expected public address of the wallet that sent the transaction",
    )
    parser.add_argument(
        "--audit-db",
        help="read-only Aegis SQLite database path (required for successful transfers)",
    )
    parser.add_argument(
        "--min-confirmations",
        type=int,
        help="minimum observed depth (defaults to the bundle's recorded requirement)",
    )
    args = parser.parse_args(argv)

    try:
        with args.bundle_path.open("r", encoding="utf-8") as stream:
            bundle = json.load(stream)
        result = reverify_testnet_evidence(
            bundle,
            expected_sender=args.expected_sender,
            audit_db=args.audit_db,
            min_confirmations=args.min_confirmations,
        )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        RpcConfigurationError,
        RpcRequestError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"Testnet evidence re-verification failed: {exc}", file=sys.stderr)
        return 1

    print("Testnet evidence re-verification: PASS")
    print(f"Mode: {result['mode']}")
    print(f"Transaction hash: {result['transaction_hash']}")
    print(f"Receipt status: {result['receipt_status']}")
    print(
        "Confirmations observed: "
        f"{result['confirmations_observed']} "
        f"(minimum required: {result['minimum_confirmations_required']})"
    )
    if result["audit_transaction_id"] is not None:
        print(f"Aegis audit transaction ID: {result['audit_transaction_id']}")
    print("Reverification uses read-only RPC calls and does not sign or submit transactions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
