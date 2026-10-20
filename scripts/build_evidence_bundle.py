"""Build a portable evidence bundle from offline evaluation and optional testnet proof."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.eval import run_evaluation
from app.chain.base_sepolia import (
    BaseSepoliaRpc,
    RpcConfigurationError,
    RpcRequestError,
    validate_address,
)
from scripts.verify_testnet_evidence import verify_audit_record, verify_evidence

BUNDLE_SCHEMA = "aegis-evidence-bundle/v1"


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _render_markdown(bundle: dict[str, Any]) -> str:
    evaluation = bundle["offline_evaluation"]
    lines = [
        "# Aegis Evidence Bundle",
        "",
        f"- Schema: {bundle['schema']}",
        f"- Generated (UTC): {bundle['generated_at']}",
        f"- Overall result: **{'PASS' if bundle['passed'] else 'FAIL'}**",
        f"- Bundle SHA-256: {bundle['bundle_sha256']}",
        "",
        "## Deterministic offline evaluation",
        "",
        f"- Result: **{'PASS' if evaluation['passed'] else 'FAIL'}**",
        f"- Scenarios: {evaluation['scenario_count']}",
        "- Mode: deterministic fallback, simulated execution, isolated temporary SQLite databases.",
        "",
        "| Scenario | Result |",
        "| --- | --- |",
    ]
    for scenario in evaluation.get("scenarios", []):
        name = scenario["name"].replace("|", "\\|")
        lines.append(f"| {name} | {'PASS' if scenario['passed'] else 'FAIL'} |")

    onchain = bundle["testnet_evidence"]
    lines.extend(["", "## Base Sepolia transfer evidence", ""])
    if not onchain["requested"]:
        lines.extend([
            "**Not supplied.** This bundle does not demonstrate an on-chain transfer.",
            "",
        ])
    else:
        lines.append(f"- Result: **{'PASS' if onchain['passed'] else 'FAIL'}**")
        evidence = onchain.get("evidence")
        if evidence:
            lines.extend([
                f"- Chain ID: {evidence['chain_id']}",
                f"- Receipt status: {evidence['receipt_status']}",
                f"- Transaction: {evidence['transaction_hash']} ({evidence['explorer_url']})",
                f"- Block: {evidence['block_number']}",
                f"- Vault: {evidence.get('vault_address', 'not supplied')}",
                f"- Token: {evidence['token_contract']}",
                f"- Recipient: {evidence['recipient']}",
                f"- Amount (base units): {evidence['amount_base_units']}",
            ])
        lines.extend(["", "| Check | Result |", "| --- | --- |"])
        for name, result in onchain["checks"].items():
            status = "PASS" if result is True else "FAIL" if result is False else "NOT RUN"
            lines.append(f"| {name.replace('_', ' ')} | {status} |")
        if onchain.get("error"):
            safe_error = onchain["error"].replace("\n", " ").replace("\r", " ").replace("|", "\\|")
            lines.extend(["", f"Verification error: {safe_error}"])
        lines.append("")

    lines.extend([
        "## Interpretation",
        "",
        "This report records the checks performed by this local tool; it is not an independent audit.",
        "A successful receipt is an observed inclusion at the reported block, not a guarantee of finality.",
        "Offline evaluation is policy/workflow evidence only and does not prove LLM safety or real-world invoice verification.",
        "AegisVault is unaudited and testnet-only. Never use this workflow with mainnet funds.",
        "",
    ])
    return "\n".join(lines)


def _write_bundle(output_dir: Path, bundle: dict[str, Any]) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "aegis-evidence-bundle.json"
    markdown_path = output_dir / "aegis-evidence-bundle.md"
    json_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(_render_markdown(bundle), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


async def build_evidence_bundle(
    output_dir: Path,
    *,
    transaction_hash: str | None = None,
    expected_sender: str | None = None,
    audit_db: str | Path | None = None,
    vault: str | None = None,
) -> dict[str, Any]:
    """Generate a portable report; testnet verification is opt-in and read-only."""
    with tempfile.TemporaryDirectory(prefix="aegis-bundle-eval-") as temporary:
        offline = await run_evaluation(Path(temporary))

    testnet: dict[str, Any] = {
        "requested": transaction_hash is not None,
        "passed": None,
        "checks": {},
        "evidence": None,
        "audit_transaction_id": None,
        "error": None,
    }
    if transaction_hash is not None:
        testnet["checks"] = {
            "chain_and_transfer_evidence_verified": False,
            "expected_sender_matches": False,
            "audit_record_matches": False,
            "receipt_succeeded": False,
        }
        try:
            if expected_sender is None or audit_db is None or vault is None:
                raise ValueError(
                    "Testnet evidence requires --expected-sender, --audit-db, and --vault"
                )
            normalized_sender = validate_address(expected_sender)
            normalized_vault = validate_address(vault)
            with BaseSepoliaRpc() as rpc:
                evidence = verify_evidence(transaction_hash, rpc, normalized_vault)
            testnet["evidence"] = evidence
            testnet["checks"]["chain_and_transfer_evidence_verified"] = True
            testnet["checks"]["expected_sender_matches"] = (
                evidence["sender"] == normalized_sender
            )
            if not testnet["checks"]["expected_sender_matches"]:
                raise ValueError("Transaction sender does not match --expected-sender")
            testnet["audit_transaction_id"] = verify_audit_record(audit_db, evidence)
            testnet["checks"]["audit_record_matches"] = True
            testnet["checks"]["receipt_succeeded"] = evidence["receipt_status"] == "success"
            if not testnet["checks"]["receipt_succeeded"]:
                raise ValueError("Transaction receipt status is not success")
            testnet["passed"] = all(testnet["checks"].values())
        except (OSError, RpcConfigurationError, RpcRequestError, TypeError, ValueError) as exc:
            testnet["error"] = str(exc)
            testnet["passed"] = False

    payload: dict[str, Any] = {
        "schema": BUNDLE_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "passed": bool(offline["passed"] and (testnet["passed"] is not False)),
        "offline_evaluation": offline,
        "testnet_evidence": testnet,
        "limitations": [
            "Offline evaluation is not evidence of an on-chain transfer.",
            "A receipt at a block is not a guarantee of finality.",
            "The AegisVault is unaudited and testnet-only.",
        ],
    }
    payload["bundle_sha256"] = hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()
    _write_bundle(output_dir, payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a portable Aegis evaluation report and optional Base Sepolia evidence bundle."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/evidence-bundle"),
        help="Output directory (default: reports/evidence-bundle)",
    )
    parser.add_argument("--transaction-hash", help="optional Base Sepolia transaction hash to verify")
    parser.add_argument("--expected-sender", help="expected public address of the agent wallet")
    parser.add_argument("--audit-db", help="read-only Aegis SQLite database to cross-check")
    parser.add_argument("--vault", help="deployed AegisVault address")
    args = parser.parse_args(argv)

    supplied = (args.expected_sender is not None, args.audit_db is not None, args.vault is not None)
    if args.transaction_hash is None and any(supplied):
        parser.error("--expected-sender, --audit-db, and --vault require --transaction-hash")
    if args.transaction_hash is not None and not all(supplied):
        parser.error("--transaction-hash requires --expected-sender, --audit-db, and --vault")

    try:
        bundle = asyncio.run(
            build_evidence_bundle(
                args.output_dir,
                transaction_hash=args.transaction_hash,
                expected_sender=args.expected_sender,
                audit_db=args.audit_db,
                vault=args.vault,
            )
        )
    except (OSError, ValueError) as exc:
        print(f"Evidence bundle failed: {exc}", file=sys.stderr)
        return 1

    print(f"Bundle result: {'PASS' if bundle['passed'] else 'FAIL'}")
    print(f"Bundle SHA-256: {bundle['bundle_sha256']}")
    print(f"JSON report: {args.output_dir / 'aegis-evidence-bundle.json'}")
    print(f"Markdown report: {args.output_dir / 'aegis-evidence-bundle.md'}")
    return 0 if bundle["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
