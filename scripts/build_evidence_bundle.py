"""Build a portable evidence bundle from offline evaluation and optional testnet proof."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import re
import subprocess
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

BUNDLE_SCHEMA = "aegis-evidence-bundle/v4"


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _source_revision() -> str | None:
    """Return the source commit used to generate evidence, when available."""
    configured = os.environ.get("GITHUB_SHA", "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{40,64}", configured):
        return configured.lower()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    revision = result.stdout.strip().lower()
    if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", revision):
        return revision
    return None


def _render_markdown(bundle: dict[str, Any]) -> str:
    evaluation = bundle["offline_evaluation"]
    provenance = bundle["provenance"]
    lines = [
        "# Aegis Evidence Bundle",
        "",
        f"- Schema: {bundle['schema']}",
        f"- Generated (UTC): {bundle['generated_at']}",
        f"- Overall result: **{'PASS' if bundle['passed'] else 'FAIL'}**",
        f"- Bundle SHA-256: {bundle['bundle_sha256']}",
        "",
        "## Generation provenance",
        "",
        f"- Source revision: `{provenance['source_revision'] or 'unavailable'}`",
        f"- Python: `{provenance['python_version']}` ({provenance['python_implementation']})",
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
                f"- Chain ID: {evidence.get('chain_id', 'not supplied')}",
                f"- Receipt status: {evidence.get('receipt_status', 'not supplied')}",
                (
                    f"- Transaction: {evidence.get('transaction_hash', 'not supplied')} "
                    f"({evidence.get('explorer_url', 'no explorer link')})"
                ),
                f"- Block: {evidence.get('block_number', 'not supplied')}",
                f"- Receipt block hash: {evidence.get('receipt_block_hash', 'not supplied')}",
                f"- Canonical block hash at verification: {evidence.get('canonical_block_hash', 'not supplied')}",
                f"- Latest observed block: {evidence.get('latest_block_number', 'not supplied')}",
                (
                    "- Confirmations observed: "
                    f"{evidence.get('confirmations_observed', 'not supplied')} "
                    f"(minimum required: {onchain.get('minimum_confirmations_required', 1)})"
                ),
                f"- Vault: {evidence.get('vault_address', 'not supplied')}",
                f"- Token: {evidence.get('token_contract', 'not supplied')}",
                f"- Recipient: {evidence.get('recipient', 'not supplied')}",
                f"- Amount (base units): {evidence.get('amount_base_units', 'not supplied')}",
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
        (
            "This report records the checks performed by this local tool; "
            "it is not an independent audit."
        ),
        (
            "A successful receipt is an observed inclusion at the reported block, "
            "not a guarantee of finality."
        ),
        (
            "Offline evaluation is policy/workflow evidence only and does not prove "
            "LLM safety or real-world invoice verification."
        ),
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
    min_confirmations: int = 1,
) -> dict[str, Any]:
    """Generate a portable report; testnet verification is opt-in and read-only."""
    if type(min_confirmations) is not int or min_confirmations < 1:
        raise ValueError("Minimum confirmations must be at least 1")
    with tempfile.TemporaryDirectory(prefix="aegis-bundle-eval-") as temporary:
        offline = await run_evaluation(Path(temporary))

    testnet: dict[str, Any] = {
        "requested": transaction_hash is not None,
        "passed": None,
        "checks": {},
        "evidence": None,
        "audit_transaction_id": None,
        "minimum_confirmations_required": min_confirmations,
        "error": None,
    }
    if transaction_hash is not None:
        testnet["checks"] = {
            "chain_and_transfer_evidence_verified": False,
            "expected_sender_matches": False,
            "audit_record_matches": False,
            "receipt_succeeded": False,
            "minimum_confirmations_met": False,
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
            testnet["checks"]["minimum_confirmations_met"] = (
                type(evidence.get("confirmations_observed")) is int
                and evidence["confirmations_observed"] >= min_confirmations
            )
            if not testnet["checks"]["minimum_confirmations_met"]:
                observed = evidence.get("confirmations_observed", "unavailable")
                raise ValueError(
                    f"Observed {observed} confirmations, fewer than the "
                    f"{min_confirmations} required"
                )
            testnet["passed"] = all(testnet["checks"].values())
        except (OSError, RpcConfigurationError, RpcRequestError, TypeError, ValueError) as exc:
            testnet["error"] = str(exc)
            testnet["passed"] = False

    payload: dict[str, Any] = {
        "schema": BUNDLE_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "provenance": {
            "source_revision": _source_revision(),
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
        },
        "passed": bool(offline["passed"] and (testnet["passed"] is not False)),
        "offline_evaluation": offline,
        "testnet_evidence": testnet,
        "limitations": [
            "Offline evaluation is not evidence of an on-chain transfer.",
            "A receipt block hash is checked against the RPC's block at that height, but this is not a guarantee of finality.",
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
        description=(
            "Build a portable Aegis evaluation report and optional Base Sepolia "
            "evidence bundle."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/evidence-bundle"),
        help="Output directory (default: reports/evidence-bundle)",
    )
    parser.add_argument(
        "--transaction-hash",
        help="optional Base Sepolia transaction hash to verify",
    )
    parser.add_argument("--expected-sender", help="expected public address of the agent wallet")
    parser.add_argument("--audit-db", help="read-only Aegis SQLite database to cross-check")
    parser.add_argument("--vault", help="deployed AegisVault address")
    parser.add_argument(
        "--min-confirmations",
        type=int,
        default=1,
        help="minimum observed block confirmations required for success (default: 1)",
    )
    args = parser.parse_args(argv)
    if args.min_confirmations < 1:
        parser.error("--min-confirmations must be at least 1")

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
                min_confirmations=args.min_confirmations,
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
