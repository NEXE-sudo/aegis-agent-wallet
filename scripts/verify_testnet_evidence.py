"""Read-only verification of an ERC-20 transfer on Base Sepolia."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from eth_utils import keccak

from app.chain.base_sepolia import (
    BASE_SEPOLIA_CHAIN_ID,
    BaseSepoliaRpc,
    RpcConfigurationError,
    RpcRequestError,
    validate_address,
)

TRANSFER_SELECTOR = "a9059cbb"
TRANSFER_CALLDATA_LENGTH = 10 + 64 + 64
VAULT_TRANSFER_SIGNATURE = "transferToken(address,address,uint256,bytes32)"
VAULT_TRANSFER_SELECTOR = "0x" + keccak(text=VAULT_TRANSFER_SIGNATURE)[:4].hex()
VAULT_TRANSFERRED_TOPIC = "0x" + keccak(
    text="TokenTransferred(address,address,uint256,bytes32,address)"
).hex()
ERC20_TRANSFER_TOPIC = "0x" + keccak(text="Transfer(address,address,uint256)").hex()
_BYTES32_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


class AuditVerificationError(ValueError):
    """Raised when persisted Aegis transaction or audit data cannot be verified."""


def verify_evidence(
    transaction_hash: str,
    rpc: BaseSepoliaRpc,
    vault_address: str | None = None,
) -> dict[str, Any]:
    chain_id = rpc.chain_id()
    if chain_id != BASE_SEPOLIA_CHAIN_ID:
        raise ValueError(
            f"RPC endpoint is on chain {chain_id}, not Base Sepolia "
            f"({BASE_SEPOLIA_CHAIN_ID})"
        )

    transaction = rpc.transaction_by_hash(transaction_hash)
    if transaction is None:
        raise ValueError("Transaction was not found on Base Sepolia")
    normalized_vault = validate_address(vault_address) if vault_address else None
    if normalized_vault is not None and validate_address(transaction["to"]) != normalized_vault:
        raise ValueError("Transaction destination does not match --vault")

    receipt = rpc.transaction_receipt(transaction_hash)
    if receipt["status"] == "pending":
        raise ValueError("Transaction receipt is not available yet")
    block_number = receipt["block_number"]
    if not isinstance(block_number, int):
        raise RpcRequestError("RPC returned a receipt without a block number")
    receipt_block_hash = receipt["block_hash"]
    if not isinstance(receipt_block_hash, str):
        raise RpcRequestError("RPC returned a receipt without a block hash")
    canonical_block_hash = rpc.block_hash_by_number(block_number)
    if receipt_block_hash.lower() != canonical_block_hash:
        raise ValueError(
            "Receipt block hash does not match the canonical block at the reported height"
        )
    latest_block_number = rpc.block_number()
    if latest_block_number < block_number:
        raise RpcRequestError("RPC latest block is behind the receipt block")
    confirmations_observed = latest_block_number - block_number + 1

    calldata = transaction["input"]
    if normalized_vault is None:
        if (
            len(calldata) != TRANSFER_CALLDATA_LENGTH
            or calldata[:10] != f"0x{TRANSFER_SELECTOR}"
        ):
            raise ValueError("Transaction calldata is not an ERC-20 transfer(address,uint256)")
        recipient_word = calldata[10:74]
        if recipient_word[:24] != "0" * 24:
            raise ValueError("ERC-20 transfer recipient is not a valid ABI-encoded address")
        token_contract = validate_address(transaction["to"])
        recipient = validate_address(f"0x{recipient_word[24:]}")
        amount_base_units = int(calldata[74:138], 16)
    else:
        if len(calldata) != 2 + 8 + 4 * 64 or calldata[:10] != VAULT_TRANSFER_SELECTOR:
            raise ValueError(
                "Transaction calldata is not AegisVault transferToken(address,address,uint256,bytes32)"
            )
        token_word, recipient_word = calldata[10:74], calldata[74:138]
        if token_word[:24] != "0" * 24 or recipient_word[:24] != "0" * 24:
            raise ValueError("Vault transfer contains an invalid ABI-encoded address")
        token_contract = validate_address("0x" + token_word[24:])
        recipient = validate_address("0x" + recipient_word[24:])
        amount_base_units = int(calldata[138:202], 16)
        reference = "0x" + calldata[202:266].lower()
        _verify_vault_receipt_logs(
            receipt["logs"],
            vault=normalized_vault,
            token=token_contract,
            recipient=recipient,
            amount=amount_base_units,
            reference=reference,
            agent=validate_address(transaction["from"]),
        )

    evidence: dict[str, Any] = {
        "chain_id": chain_id,
        "receipt_status": str(receipt["status"]),
        "block_number": block_number,
        "receipt_block_hash": receipt_block_hash.lower(),
        "canonical_block_hash": canonical_block_hash,
        "latest_block_number": latest_block_number,
        "confirmations_observed": confirmations_observed,
        "token_contract": token_contract,
        "sender": validate_address(transaction["from"]),
        "recipient": recipient,
        "amount_base_units": amount_base_units,
        "transaction_hash": transaction_hash.lower(),
        "explorer_url": f"https://sepolia.basescan.org/tx/{transaction_hash.lower()}",
    }
    if normalized_vault is not None:
        evidence["vault_address"] = normalized_vault
        evidence["proposal_reference"] = reference
    return evidence


def _decode_indexed_address(topic: str) -> str:
    if not _BYTES32_RE.fullmatch(topic) or topic[2:26] != "0" * 24:
        raise ValueError("Vault receipt contains a malformed indexed address")
    return validate_address("0x" + topic[-40:])


def _verify_vault_receipt_logs(
    logs: list[dict[str, Any]],
    *,
    vault: str,
    token: str,
    recipient: str,
    amount: int,
    reference: str,
    agent: str,
) -> None:
    vault_transfer_seen = False
    token_transfer_seen = False
    for log in logs:
        topics = log["topics"]
        if (
            log["address"] == vault
            and topics
            and topics[0] == VAULT_TRANSFERRED_TOPIC
        ):
            if len(topics) != 3 or len(log["data"]) != 2 + 3 * 64:
                raise ValueError("AegisVault TokenTransferred event is malformed")
            event_token = _decode_indexed_address(topics[1])
            event_recipient = _decode_indexed_address(topics[2])
            data = bytes.fromhex(log["data"][2:])
            event_amount = int.from_bytes(data[:32], "big")
            event_reference = "0x" + data[32:64].hex()
            if data[64:76] != b"\x00" * 12:
                raise ValueError("AegisVault TokenTransferred event has a malformed agent address")
            event_agent = validate_address("0x" + data[76:96].hex())
            if (
                event_token != token
                or event_recipient != recipient
                or event_amount != amount
                or event_reference != reference
                or event_agent != agent
            ):
                raise ValueError("AegisVault TokenTransferred event does not match transfer calldata")
            vault_transfer_seen = True
        if (
            log["address"] == token
            and topics
            and topics[0] == ERC20_TRANSFER_TOPIC
        ):
            if len(topics) != 3 or len(log["data"]) != 2 + 64:
                raise ValueError("ERC-20 Transfer event is malformed")
            event_sender = _decode_indexed_address(topics[1])
            event_recipient = _decode_indexed_address(topics[2])
            event_amount = int.from_bytes(bytes.fromhex(log["data"][2:]), "big")
            if (
                event_sender != vault
                or event_recipient != recipient
                or event_amount != amount
            ):
                raise ValueError("ERC-20 Transfer event does not match vault transfer calldata")
            token_transfer_seen = True
    if not vault_transfer_seen:
        raise ValueError("Receipt is missing the vault TokenTransferred event")
    if not token_transfer_seen:
        raise ValueError("Receipt is missing the matching token Transfer event")


def verify_audit_record(database_path: str | Path, evidence: dict[str, Any]) -> str:
    """Cross-check chain evidence against an Aegis database without opening it for writes."""
    database_uri = f"{Path(database_path).expanduser().resolve().as_uri()}?mode=ro"
    has_vault_reference = "proposal_reference" in evidence
    try:
        connection = sqlite3.connect(database_uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            fingerprint_column = ", fingerprint" if has_vault_reference else ""
            matching_transactions = connection.execute(
                f"""SELECT transaction_id, status, execution_reference, proposal_json,
                          chain_id, token_address, recipient_normalized, amount_base_units
                          {fingerprint_column}
                   FROM transactions
                   WHERE lower(execution_reference) = ?""",
                (evidence["transaction_hash"],),
            ).fetchall()
            if not matching_transactions:
                raise ValueError("No Aegis transaction record matches this transaction hash")
            if len(matching_transactions) != 1:
                raise AuditVerificationError(
                    "Multiple Aegis transaction records match this transaction hash"
                )
            transaction = matching_transactions[0]

            try:
                proposal = json.loads(transaction["proposal_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("Aegis transaction proposal is malformed") from exc
            if not isinstance(proposal, dict):
                raise AuditVerificationError("Aegis transaction proposal is malformed")
            if has_vault_reference:
                stored_fingerprint = transaction["fingerprint"]
                canonical_proposal = json.dumps(
                    proposal, sort_keys=True, separators=(",", ":")
                )
                independently_computed_fingerprint = hashlib.sha256(
                    canonical_proposal.encode("utf-8")
                ).hexdigest()
                reference = evidence["proposal_reference"]
                if (
                    not isinstance(stored_fingerprint, str)
                    or re.fullmatch(r"[0-9a-f]{64}", stored_fingerprint) is None
                    or stored_fingerprint != reference[2:].lower()
                    or stored_fingerprint != independently_computed_fingerprint
                ):
                    raise ValueError(
                        "Vault transfer ref does not match the stored proposal fingerprint"
                    )

            proposal_token = proposal.get("token_address")
            proposal_recipient = proposal.get("recipient")
            if (
                not isinstance(proposal_token, str)
                or not isinstance(proposal_recipient, str)
                or not isinstance(transaction["token_address"], str)
                or not isinstance(transaction["recipient_normalized"], str)
                or type(transaction["chain_id"]) is not int
                or type(transaction["amount_base_units"]) is not int
            ):
                raise AuditVerificationError("Aegis transaction record is malformed")
            if (
                type(proposal.get("chain_id")) is not int
                or proposal["chain_id"] != BASE_SEPOLIA_CHAIN_ID
                or type(proposal.get("amount_base_units")) is not int
            ):
                raise AuditVerificationError("Aegis transaction proposal is malformed")
            expected_fields = (
                (transaction["status"], "submitted_onchain"),
                (transaction["chain_id"], BASE_SEPOLIA_CHAIN_ID),
                (validate_address(transaction["token_address"]), evidence["token_contract"]),
                (validate_address(proposal_token), evidence["token_contract"]),
                (validate_address(proposal_recipient), evidence["recipient"]),
                (transaction["recipient_normalized"], evidence["recipient"]),
                (transaction["amount_base_units"], evidence["amount_base_units"]),
                (proposal.get("amount_base_units"), evidence["amount_base_units"]),
            )
            if any(actual != expected for actual, expected in expected_fields):
                raise ValueError("Aegis transaction record does not match the on-chain transfer")

            events = connection.execute(
                """SELECT event_id, event_type, actor_role, from_status, to_status,
                          details_json
                   FROM audit_events WHERE transaction_id = ? ORDER BY event_id""",
                (transaction["transaction_id"],),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ValueError(f"Could not read Aegis audit database: {exc}") from exc

    parsed_events: list[dict[str, Any]] = []
    try:
        for event in events:
            details = json.loads(event["details_json"])
            if not isinstance(details, dict):
                raise AuditVerificationError("Aegis audit event details are malformed")
            parsed_events.append({**dict(event), "details": details})
    except (TypeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("Aegis audit event details are malformed") from exc

    def find_event(event_type: str, after: int = -1) -> dict[str, Any] | None:
        return next(
            (
                event for event in parsed_events
                if event["event_id"] > after and event["event_type"] == event_type
            ),
            None,
        )

    created = find_event("proposal_created")
    signed = find_event("onchain_transaction_signed", created["event_id"] if created else -1)
    submitted = find_event(
        "onchain_transaction_submitted", signed["event_id"] if signed else -1
    )
    if created is None or signed is None or submitted is None:
        raise ValueError("Aegis audit trail is missing expected events in order")
    if (
        signed["actor_role"] != "owner"
        or signed["from_status"] not in {"ready", "approved"}
        or signed["to_status"] != "submitting_onchain"
        or submitted["actor_role"] != "owner"
        or submitted["from_status"] != "submitting_onchain"
        or submitted["to_status"] != "submitted_onchain"
        or not isinstance(signed["details"].get("transaction_hash"), str)
        or signed["details"]["transaction_hash"].lower() != evidence["transaction_hash"]
        or not isinstance(submitted["details"].get("transaction_hash"), str)
        or submitted["details"]["transaction_hash"].lower()
        != evidence["transaction_hash"]
    ):
        raise ValueError("Aegis audit events do not match the on-chain transaction")
    if signed["from_status"] == "approved":
        approval = find_event("approval_granted", created["event_id"])
        if (
            approval is None
            or approval["event_id"] >= signed["event_id"]
            or approval["actor_role"] != "owner"
            or approval["to_status"] != "approved"
        ):
            raise ValueError("Aegis audit trail is missing the owner approval event")

    return transaction["transaction_id"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a Base Sepolia ERC-20 transfer using read-only RPC calls."
    )
    parser.add_argument("transaction_hash", help="32-byte transaction hash")
    sender_group = parser.add_mutually_exclusive_group(required=True)
    sender_group.add_argument(
        "--expected-sender",
        help="expected public address of the wallet that sent the transfer",
    )
    sender_group.add_argument(
        "--skip-sender-check",
        action="store_true",
        help="skip sender matching (prints a warning and weakens the evidence)",
    )
    parser.add_argument("--audit-db", help="optional read-only Aegis SQLite database path")
    parser.add_argument(
        "--min-confirmations",
        type=int,
        default=1,
        help="minimum observed block confirmations required for success (default: 1)",
    )
    parser.add_argument(
        "--vault",
        help="verify an AegisVault transferToken call and its vault/token receipt events",
    )
    args = parser.parse_args(argv)

    if args.min_confirmations < 1:
        parser.error("--min-confirmations must be at least 1")
    if args.expected_sender is not None:
        try:
            expected_sender = validate_address(args.expected_sender)
        except ValueError as exc:
            parser.error(f"--expected-sender: {exc}")
    else:
        expected_sender = None
    if args.vault is not None:
        try:
            vault_address = validate_address(args.vault)
        except ValueError as exc:
            parser.error(f"--vault: {exc}")
    else:
        vault_address = None

    try:
        with BaseSepoliaRpc() as rpc:
            evidence = verify_evidence(args.transaction_hash, rpc, vault_address)
        if expected_sender is not None and evidence["sender"] != expected_sender:
            raise ValueError("Transaction sender does not match --expected-sender")
        transaction_id = (
            verify_audit_record(args.audit_db, evidence)
            if args.audit_db is not None
            else None
        )
    except (OSError, RpcConfigurationError, RpcRequestError, TypeError, ValueError) as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1

    print(f"Chain ID: {evidence['chain_id']} (Base Sepolia; expected 84532)")
    print(f"Transaction hash: {evidence['transaction_hash']}")
    print(f"Sender: {evidence['sender']}")
    if args.skip_sender_check:
        print(
            "WARNING: Sender check was explicitly skipped; this does not verify "
            "which wallet sent the transfer."
        )
    print(f"Receipt status: {evidence['receipt_status']}")
    print(f"Block number: {evidence['block_number']}")
    print(f"Receipt block hash: {evidence['receipt_block_hash']}")
    print(f"Canonical block hash at verification: {evidence['canonical_block_hash']}")
    print(f"Latest observed block: {evidence['latest_block_number']}")
    print(
        f"Confirmations observed: {evidence['confirmations_observed']} "
        f"(minimum required: {args.min_confirmations})"
    )
    print(f"Token contract: {evidence['token_contract']}")
    if vault_address is not None:
        print(f"AegisVault: {vault_address}")
        print(f"Proposal ref: {evidence['proposal_reference']}")
    print(f"Recipient: {evidence['recipient']}")
    print(f"Amount (base units): {evidence['amount_base_units']}")
    print(f"Base Sepolia explorer: {evidence['explorer_url']}")
    if transaction_id is None:
        print("Audit cross-check: not performed")
    else:
        print(f"Audit cross-check: OK (transaction {transaction_id})")
    if evidence["receipt_status"] != "success":
        print("Verification failed: the transaction receipt status is not success.", file=sys.stderr)
        return 1
    if evidence["confirmations_observed"] < args.min_confirmations:
        print(
            "Verification failed: observed "
            f"{evidence['confirmations_observed']} confirmations, fewer than "
            f"the {args.min_confirmations} required.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
