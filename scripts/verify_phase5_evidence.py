"""Cross-check deployment verification with a passing Base Sepolia evidence bundle."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import sys
from pathlib import Path
from typing import Any

from scripts.verify_evidence_bundle import validate_bundle

_CHAIN_ID = 84532
_HASH_RE = re.compile(r"0x[0-9a-fA-F]{64}")
_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _normal_address(value: object, field: str) -> str:
    if not isinstance(value, str) or _ADDRESS_RE.fullmatch(value) is None:
        raise ValueError(f"Deployment evidence has an invalid {field}")
    address = value.lower()
    if address == "0x" + "0" * 40:
        raise ValueError(f"Deployment evidence has a zero {field}")
    return address


def validate_phase5_evidence(
    deployment: object,
    bundle: object,
) -> dict[str, Any]:
    """Validate both artefacts independently and cross-check their shared identity."""
    if not isinstance(deployment, dict):
        raise TypeError("Deployment evidence must be a JSON object")

    digest = deployment.get("deployment_evidence_sha256")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise ValueError("Deployment evidence has a missing or malformed SHA-256 digest")
    payload = dict(deployment)
    payload.pop("deployment_evidence_sha256")
    expected_digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    if not hmac.compare_digest(digest, expected_digest):
        raise ValueError("Deployment evidence SHA-256 digest does not match its payload")

    if deployment.get("read_only") is not True:
        raise ValueError("Deployment evidence must explicitly identify a read-only verification")
    if deployment.get("chain_id") != _CHAIN_ID:
        raise ValueError("Deployment evidence must identify Base Sepolia (chain ID 84532)")
    if deployment.get("receipt_status") != "success":
        raise ValueError("Deployment evidence must have a successful deployment receipt")
    if deployment.get("expected_bytecode_hash_matched") is not True:
        raise ValueError("Deployment evidence does not confirm the expected bytecode fingerprint")

    deployment_tx = deployment.get("transaction_hash")
    if not isinstance(deployment_tx, str) or _HASH_RE.fullmatch(deployment_tx) is None:
        raise ValueError("Deployment evidence has an invalid deployment transaction hash")
    receipt_hash = deployment.get("receipt_block_hash")
    canonical_hash = deployment.get("canonical_block_hash")
    if (
        not isinstance(receipt_hash, str)
        or _HASH_RE.fullmatch(receipt_hash) is None
        or not isinstance(canonical_hash, str)
        or _HASH_RE.fullmatch(canonical_hash) is None
        or receipt_hash.lower() != canonical_hash.lower()
    ):
        raise ValueError("Deployment receipt block hash does not match its recorded canonical block hash")

    confirmations = deployment.get("confirmations_observed")
    minimum = deployment.get("minimum_confirmations_required")
    if type(confirmations) is not int or type(minimum) is not int or minimum < 1 or confirmations < minimum:
        raise ValueError("Deployment evidence does not meet its recorded confirmation threshold")

    vault = _normal_address(deployment.get("vault_address"), "vault address")
    owner = _normal_address(deployment.get("expected_owner"), "expected owner")
    if _normal_address(deployment.get("constructor_owner"), "constructor owner") != owner:
        raise ValueError("Deployment constructor owner does not match the expected owner")
    if _normal_address(deployment.get("current_onchain_owner"), "current on-chain owner") != owner:
        raise ValueError("Current on-chain owner does not match the expected owner")

    bytecode_hash = deployment.get("runtime_bytecode_sha256")
    if not isinstance(bytecode_hash, str) or _SHA256_RE.fullmatch(bytecode_hash) is None:
        raise ValueError("Deployment evidence has a malformed runtime bytecode SHA-256")

    validate_bundle(bundle)
    if not isinstance(bundle, dict):
        raise TypeError("Evidence bundle must be a JSON object")
    if bundle.get("schema") != "aegis-evidence-bundle/v6":
        raise ValueError("Phase 5 cross-check requires a schema-v6 evidence bundle")
    testnet = bundle.get("testnet_evidence")
    if not isinstance(testnet, dict) or testnet.get("requested") is not True or testnet.get("passed") is not True:
        raise ValueError("Evidence bundle must contain passing testnet transaction evidence")
    evidence = testnet.get("evidence")
    configuration = testnet.get("vault_configuration")
    if not isinstance(evidence, dict) or not isinstance(configuration, dict):
        raise TypeError("Evidence bundle transaction and vault configuration evidence must be JSON objects")
    if evidence.get("chain_id") != _CHAIN_ID or configuration.get("chain_id") != _CHAIN_ID:
        raise ValueError("Transfer bundle must identify Base Sepolia (chain ID 84532)")

    bundle_vault = _normal_address(evidence.get("vault_address"), "bundle vault address")
    config_vault = _normal_address(configuration.get("vault_address"), "bundle configuration vault address")
    if bundle_vault != vault or config_vault != vault:
        raise ValueError("Deployment and transfer bundle vault addresses do not match")
    bundle_owner = _normal_address(configuration.get("owner"), "bundle owner")
    agent = _normal_address(configuration.get("agent"), "bundle agent")
    if bundle_owner != owner:
        raise ValueError("Deployment owner does not match the bundle's on-chain owner snapshot")
    if agent == owner:
        raise ValueError("Bundle vault configuration merges owner and agent identities")
    if configuration.get("bytecode_sha256") != bytecode_hash:
        raise ValueError("Deployment bytecode fingerprint does not match the bundle snapshot")

    transfer_tx = evidence.get("transaction_hash")
    if not isinstance(transfer_tx, str) or _HASH_RE.fullmatch(transfer_tx) is None:
        raise ValueError("Transfer evidence has an invalid transaction hash")
    if transfer_tx.lower() == deployment_tx.lower():
        raise ValueError("Deployment and transfer transaction hashes must be distinct")

    checks = {
        "deployment_digest_valid": True,
        "deployment_receipt_and_confirmations_valid": True,
        "deployment_identity_valid": True,
        "bundle_digest_and_internal_consistency_valid": True,
        "same_base_sepolia_vault": True,
        "owner_identity_matches": True,
        "runtime_bytecode_fingerprint_matches": True,
        "deployment_and_transfer_transactions_distinct": True,
    }
    return {
        "result": "PASS",
        "chain_id": _CHAIN_ID,
        "vault_address": vault,
        "owner_address": owner,
        "agent_address": agent,
        "deployment_transaction_hash": deployment_tx.lower(),
        "transfer_transaction_hash": transfer_tx.lower(),
        "runtime_bytecode_sha256": bytecode_hash,
        "deployment_evidence_sha256": digest,
        "bundle_sha256": bundle["bundle_sha256"],
        "testnet_mode": testnet.get("mode", "successful_transfer"),
        "checks": checks,
        "limitations": [
            "SHA-256 digests detect changes but do not prove authorship or authenticity.",
            "RPC observations are from the configured provider and are not independent consensus proofs.",
            "Observed confirmations do not guarantee finality.",
            "Matching runtime bytecode does not prove source reproducibility or contract safety.",
            "AegisVault is unaudited and testnet-only; this report does not authorise mainnet or real-fund use.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cross-check a verified AegisVault deployment with a schema-v6 testnet evidence bundle."
    )
    parser.add_argument("deployment_evidence", type=Path, help="JSON output from verify_vault_deployment")
    parser.add_argument("evidence_bundle", type=Path, help="JSON output from build_evidence_bundle")
    parser.add_argument("--output", type=Path, help="optional path for the cross-check report")
    args = parser.parse_args(argv)

    try:
        deployment = json.loads(args.deployment_evidence.read_text(encoding="utf-8"))
        bundle = json.loads(args.evidence_bundle.read_text(encoding="utf-8"))
        report = validate_phase5_evidence(deployment, bundle)
        rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"Phase 5 evidence cross-check failed: {exc}", file=sys.stderr)
        return 1

    print("Phase 5 deployment-to-transfer evidence cross-check: PASS")
    print(rendered, end="")
    if args.output is not None:
        print(f"Cross-check report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
