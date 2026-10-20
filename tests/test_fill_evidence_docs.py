"""Fixture-only tests for evidence documentation filling; no live-chain claims are made."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.fill_evidence_docs as filler

FIXTURE_SHA = "a" * 40
FIXTURE_VAULT = "0x" + "12" * 20
FIXTURE_DEPLOYMENT_TX = "0x" + "01" * 32
FIXTURE_SUCCESS_TX = "0x" + "02" * 32
FIXTURE_REVERT_TX = "0x" + "03" * 32


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture_tree(root: Path) -> Path:
    """Create synthetic files with explicit fixture labels, never real operator evidence."""
    live = root / "reports/live"
    deployment = {
        "_fixture_only": True,
        "chain_id": 84532,
        "vault_address": FIXTURE_VAULT,
        "transaction_hash": FIXTURE_DEPLOYMENT_TX,
        "deployment_evidence_sha256": "fixture-digest-not-real",
        "confirmations_observed": 12,
        "minimum_confirmations_required": 12,
    }
    success = {
        "_fixture_only": True,
        "schema": "aegis-evidence-bundle/v6",
        "passed": True,
        "bundle_sha256": "fixture-success-digest",
        "testnet_evidence": {"mode": "successful_transfer", "evidence": {"transaction_hash": FIXTURE_SUCCESS_TX}},
    }
    reverted = {
        "_fixture_only": True,
        "schema": "aegis-evidence-bundle/v6",
        "passed": True,
        "bundle_sha256": "fixture-revert-digest",
        "testnet_evidence": {"mode": "reverted_vault_call", "evidence": {"transaction_hash": FIXTURE_REVERT_TX}},
    }
    write_json(live / "deployment-verification.json", deployment)
    write_json(live / "success/aegis-evidence-bundle.json", success)
    write_json(live / "revert/aegis-evidence-bundle.json", reverted)
    write_json(live / "phase5-success.json", {"result": "PASS", "transfer_transaction_hash": FIXTURE_SUCCESS_TX})
    write_json(live / "phase5-revert.json", {"result": "PASS", "transfer_transaction_hash": FIXTURE_REVERT_TX})
    for path in filler.DOCS:
        doc = root / path
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            "# Fixture document\n\n"
            + filler.START + "\nPlaceholder evidence\n" + filler.END + "\n\n"
            + filler.PROVENANCE_START + "\nPlaceholder provenance\n" + filler.PROVENANCE_END + "\n",
            encoding="utf-8",
        )
    return live


def install_fixture_verifiers(monkeypatch: pytest.MonkeyPatch) -> None:
    def validate_bundle(bundle: object) -> object:
        assert isinstance(bundle, dict) and bundle.get("_fixture_only") is True
        return bundle

    def validate_phase5(deployment: object, bundle: object) -> dict[str, str]:
        assert isinstance(deployment, dict) and deployment.get("_fixture_only") is True
        assert isinstance(bundle, dict) and bundle.get("_fixture_only") is True
        evidence = bundle["testnet_evidence"]["evidence"]
        return {"result": "PASS", "transfer_transaction_hash": evidence["transaction_hash"]}

    monkeypatch.setattr(filler, "validate_bundle", validate_bundle)
    monkeypatch.setattr(filler, "validate_phase5_evidence", validate_phase5)
    monkeypatch.setattr(filler, "_git_revision", lambda _root: FIXTURE_SHA)


def test_missing_live_evidence_is_refused_without_editing_docs(tmp_path: Path) -> None:
    for path in filler.DOCS:
        doc = tmp_path / path
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text("unchanged", encoding="utf-8")
    with pytest.raises(filler.EvidenceDocsError, match="Missing required operator-generated evidence"):
        filler.fill_documents(tmp_path, tmp_path / "reports/live")
    assert all((tmp_path / path).read_text(encoding="utf-8") == "unchanged" for path in filler.DOCS)


def test_fixture_evidence_fills_docs_idempotently(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install_fixture_verifiers(monkeypatch)
    live = fixture_tree(tmp_path)
    first_paths = filler.fill_documents(tmp_path, live)
    first = {str(path): path.read_text(encoding="utf-8") for path in first_paths}
    second_paths = filler.fill_documents(tmp_path, live)
    second = {str(path): path.read_text(encoding="utf-8") for path in second_paths}
    assert first == second
    assert all(FIXTURE_SHA in text for text in second.values())
    assert all(FIXTURE_SUCCESS_TX in text and FIXTURE_REVERT_TX in text for text in second.values())
    assert all("_fixture_only" not in text for text in second.values())


def test_mismatched_phase5_report_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install_fixture_verifiers(monkeypatch)
    live = fixture_tree(tmp_path)
    write_json(live / "phase5-success.json", {"result": "PASS", "transfer_transaction_hash": FIXTURE_REVERT_TX})
    with pytest.raises(filler.EvidenceDocsError, match="does not match recomputed verified evidence"):
        filler.fill_documents(tmp_path, live)
    assert all("Placeholder evidence" in (tmp_path / path).read_text(encoding="utf-8") for path in filler.DOCS)
