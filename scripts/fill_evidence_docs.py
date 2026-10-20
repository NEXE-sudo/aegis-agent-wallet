"""Fill submission documentation only from internally verified reports/live evidence.

The tool is offline: it never calls an RPC and never invents chain data. It checks
the saved deployment digest, both schema-v6 bundles and their deployment cross-checks
before editing any documentation. Fixture bundles must never be placed in reports/live.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts.verify_evidence_bundle import validate_bundle
from scripts.verify_phase5_evidence import validate_phase5_evidence

START = "<!-- LIVE_EVIDENCE_START -->"
END = "<!-- LIVE_EVIDENCE_END -->"
PROVENANCE_START = "<!-- SUBMISSION_PROVENANCE_START -->"
PROVENANCE_END = "<!-- SUBMISSION_PROVENANCE_END -->"
REQUIRED_FILES = (
    "deployment-verification.json",
    "success/aegis-evidence-bundle.json",
    "revert/aegis-evidence-bundle.json",
    "phase5-success.json",
    "phase5-revert.json",
)
DOCS = (
    "docs/onchain-enforcement.md",
    "docs/technical-document.md",
    "docs/security-self-assessment.md",
)


class EvidenceDocsError(ValueError):
    """Raised when the live evidence is missing, invalid, or inconsistent."""


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceDocsError(f"Cannot read {label}; no documentation was changed.") from exc
    if not isinstance(value, dict):
        raise EvidenceDocsError(f"{label} must be a JSON object; no documentation was changed.")
    return value


def _validate_live_evidence(live_dir: Path) -> dict[str, Any]:
    missing = [name for name in REQUIRED_FILES if not (live_dir / name).is_file()]
    if missing:
        raise EvidenceDocsError(
            "Missing required operator-generated evidence under reports/live: "
            + ", ".join(missing)
            + ". Run the live verifier first; fixtures and offline-only bundles are not accepted."
        )
    deployment = _load_json(live_dir / "deployment-verification.json", "deployment verification")
    success = _load_json(live_dir / "success/aegis-evidence-bundle.json", "successful-transfer bundle")
    reverted = _load_json(live_dir / "revert/aegis-evidence-bundle.json", "reverted-call bundle")
    saved_success_check = _load_json(live_dir / "phase5-success.json", "successful-transfer cross-check")
    saved_revert_check = _load_json(live_dir / "phase5-revert.json", "reverted-call cross-check")

    validate_bundle(success)
    validate_bundle(reverted)
    if success.get("passed") is not True or reverted.get("passed") is not True:
        raise EvidenceDocsError("Both live evidence bundles must pass their own integrity checks.")
    if success.get("schema") != "aegis-evidence-bundle/v6" or reverted.get("schema") != "aegis-evidence-bundle/v6":
        raise EvidenceDocsError("Only schema-v6 evidence bundles are accepted.")
    if success.get("testnet_evidence", {}).get("mode") != "successful_transfer":
        raise EvidenceDocsError("The success bundle does not identify a successful transfer.")
    if reverted.get("testnet_evidence", {}).get("mode") != "reverted_vault_call":
        raise EvidenceDocsError("The revert bundle does not identify a reverted vault call.")
    success_check = validate_phase5_evidence(deployment, success)
    revert_check = validate_phase5_evidence(deployment, reverted)
    if success_check.get("result") != "PASS" or revert_check.get("result") != "PASS":
        raise EvidenceDocsError("Deployment-to-bundle cross-check failed.")
    for label, saved, computed in (
        ("success", saved_success_check, success_check),
        ("revert", saved_revert_check, revert_check),
    ):
        if saved != computed:
            raise EvidenceDocsError(f"Saved {label} cross-check does not match recomputed verified evidence.")
    success_tx = success["testnet_evidence"]["evidence"]["transaction_hash"]
    revert_tx = reverted["testnet_evidence"]["evidence"]["transaction_hash"]
    if success_tx.lower() == revert_tx.lower():
        raise EvidenceDocsError("Successful and reverted transaction hashes must be distinct.")
    if success_tx.lower() == deployment["transaction_hash"].lower() or revert_tx.lower() == deployment["transaction_hash"].lower():
        raise EvidenceDocsError("Deployment and transfer evidence must use distinct transaction hashes.")
    return {
        "vault_address": deployment["vault_address"],
        "deployment_transaction_hash": deployment["transaction_hash"],
        "successful_transfer_transaction_hash": success_tx,
        "reverted_bypass_transaction_hash": revert_tx,
        "chain_id": deployment["chain_id"],
        "confirmations_observed": deployment["confirmations_observed"],
        "minimum_confirmations_required": deployment["minimum_confirmations_required"],
        "deployment_evidence_sha256": deployment["deployment_evidence_sha256"],
        "success_bundle_sha256": success["bundle_sha256"],
        "revert_bundle_sha256": reverted["bundle_sha256"],
        "success_phase5_result": success_check["result"],
        "revert_phase5_result": revert_check["result"],
    }


def _render_block(evidence: dict[str, Any]) -> str:
    return "\n".join([
        START,
        "## Verified Base Sepolia execution evidence",
        "",
        f"- Source revision at fill time: `{evidence['source_revision']}`",
        f"- Chain ID: `{evidence['chain_id']}` (Base Sepolia only)",
        f"- Vault address: `{evidence['vault_address']}`",
        f"- Deployment transaction hash: `{evidence['deployment_transaction_hash']}`",
        f"- Successful transfer transaction hash: `{evidence['successful_transfer_transaction_hash']}`",
        f"- Reverted bypass transaction hash: `{evidence['reverted_bypass_transaction_hash']}`",
        f"- Deployment confirmations observed: {evidence['confirmations_observed']} (minimum required: {evidence['minimum_confirmations_required']})",
        f"- Deployment evidence SHA-256: `{evidence['deployment_evidence_sha256']}`",
        f"- Successful-transfer bundle SHA-256: `{evidence['success_bundle_sha256']}`",
        f"- Reverted-call bundle SHA-256: `{evidence['revert_bundle_sha256']}`",
        "- Cross-check: deployment-to-success and deployment-to-revert both PASS.",
        "- Interpretation: provider-observed testnet evidence only; confirmations do not guarantee finality, hashes do not prove authorship, and the vault remains unaudited and testnet-only.",
        END,
    ])


def _render_provenance(revision: str, python_ci_url: str | None, foundry_ci_url: str | None) -> str:
    def checked(value: str | None, label: str) -> str:
        if not value:
            return f"Pending: attach the {label} run URL for this exact source revision."
        if not value.startswith("https://github.com/") or "/actions/runs/" not in value:
            raise EvidenceDocsError(f"{label} URL must be a GitHub Actions run URL.")
        return f"[Observed {label} CI run]({value}) — operator must confirm it tested this exact source revision."
    return "\n".join([
        PROVENANCE_START,
        "## Final source and CI provenance",
        "",
        f"- Source commit at documentation fill time: **{revision}**",
        f"- Python CI: {checked(python_ci_url, 'Python')}",
        f"- Foundry CI: {checked(foundry_ci_url, 'Foundry')}",
        "- CI links are references, not cryptographic attestations; check each run's tested SHA before submission.",
        PROVENANCE_END,
    ])


def _replace_block(document: str, block: str, path: str) -> str:
    if document.count(START) != 1 or document.count(END) != 1:
        raise EvidenceDocsError(f"{path} must contain exactly one evidence marker pair.")
    start = document.index(START)
    end = document.index(END) + len(END)
    if end < start:
        raise EvidenceDocsError(f"{path} evidence markers are reversed.")
    # Idempotent: replacing the complete marker block with the same verified data is stable.
    return document[:start] + block + document[end:]


def _git_revision(root: Path) -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                text=True, check=False, timeout=3)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceDocsError("Cannot determine the source commit; documentation was not changed.") from exc
    revision = result.stdout.strip().lower()
    if result.returncode != 0 or len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise EvidenceDocsError("Git did not return a valid source commit; documentation was not changed.")
    return revision


def fill_documents(root: Path, live_dir: Path, *, python_ci_url: str | None = None,
                   foundry_ci_url: str | None = None) -> list[Path]:
    """Validate all evidence and all target files before writing any of them."""
    root = root.resolve()
    live = live_dir if live_dir.is_absolute() else root / live_dir
    evidence = _validate_live_evidence(live)
    revision = _git_revision(root)
    evidence["source_revision"] = revision
    block = _render_block(evidence)
    provenance_block = _render_provenance(revision, python_ci_url, foundry_ci_url)
    replacements: dict[Path, str] = {}
    for relative in DOCS:
        path = root / relative
        try:
            original = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise EvidenceDocsError(f"Cannot read {relative}; no documentation was changed.") from exc
        updated = _replace_block(original, block, relative)
        if updated.count(PROVENANCE_START) != 1 or updated.count(PROVENANCE_END) != 1:
            raise EvidenceDocsError(f"{relative} must contain exactly one provenance marker pair.")
        provenance_start = updated.index(PROVENANCE_START)
        provenance_end = updated.index(PROVENANCE_END) + len(PROVENANCE_END)
        updated = updated[:provenance_start] + provenance_block + updated[provenance_end:]
        replacements[path] = updated
    # Write temporary siblings first; validation above prevents partial writes from malformed evidence.
    temporaries: list[tuple[Path, Path]] = []
    try:
        for path, content in replacements.items():
            temp = path.with_suffix(path.suffix + ".evidence.tmp")
            temp.write_text(content, encoding="utf-8")
            temporaries.append((temp, path))
        for temp, path in temporaries:
            temp.replace(path)
    except OSError as exc:
        for temp, _ in temporaries:
            temp.unlink(missing_ok=True)
        raise EvidenceDocsError("Could not write the complete evidence documentation update.") from exc
    return list(replacements)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fill live-evidence fields only from verified reports/live bundles.")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--live-dir", type=Path, default=Path("reports/live"))
    parser.add_argument("--python-ci-url", help="observed GitHub Actions Python run URL for the source revision")
    parser.add_argument("--foundry-ci-url", help="observed GitHub Actions Foundry run URL for the source revision")
    args = parser.parse_args(argv)
    try:
        paths = fill_documents(args.root, args.live_dir, python_ci_url=args.python_ci_url,
                               foundry_ci_url=args.foundry_ci_url)
    except (EvidenceDocsError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Evidence documentation update refused: {exc}", file=sys.stderr)
        return 1
    print("Evidence documentation updated from verified Base Sepolia reports: PASS")
    for path in paths:
        print(path)
    print("This modifies tracked documentation; commit it and rebuild the submission manifest/preflight before packaging.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
