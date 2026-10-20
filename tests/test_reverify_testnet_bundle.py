"""Tests for independently re-verifying saved testnet evidence."""

import hashlib
import json

import pytest

from scripts import reverify_testnet_bundle as reverify


SENDER = "0x1111111111111111111111111111111111111111"
VAULT = "0x4444444444444444444444444444444444444444"
TX_HASH = "0x" + "a" * 64


def _seal(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {**payload, "bundle_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def _bundle(mode="successful_transfer"):
    reverted = mode == "reverted_vault_call"
    evidence = {
        "chain_id": 84532,
        "receipt_status": "failed" if reverted else "success",
        "block_number": 123,
        "receipt_block_hash": "0x" + "c" * 64,
        "canonical_block_hash": "0x" + "c" * 64,
        "latest_block_number": 136,
        "confirmations_observed": 14,
        "token_contract": "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
        "sender": SENDER,
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 2500000,
        "transaction_hash": TX_HASH,
        "explorer_url": f"https://sepolia.basescan.org/tx/{TX_HASH}",
        "vault_address": VAULT,
        "proposal_reference": "0x" + "b" * 64,
    }
    checks = {
        "chain_and_transfer_evidence_verified": True,
        "expected_sender_matches": True,
        "receipt_status_matches_expectation": True,
        "minimum_confirmations_met": True,
    }
    if not reverted:
        checks["audit_record_matches"] = True
    return _seal({
        "schema": "aegis-evidence-bundle/v5",
        "generated_at": "2026-10-20T00:00:00+00:00",
        "provenance": {
            "source_revision": "a" * 40,
            "python_version": "3.14.0",
            "python_implementation": "CPython",
        },
        "passed": True,
        "offline_evaluation": {"passed": True, "scenario_count": 1, "scenarios": []},
        "testnet_evidence": {
            "requested": True,
            "mode": mode,
            "passed": True,
            "checks": checks,
            "evidence": evidence,
            "audit_transaction_id": None if reverted else "txn-123",
            "minimum_confirmations_required": 12,
        },
        "limitations": ["Test fixture only."],
    })


class _FakeRpc:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def _install_chain(monkeypatch, evidence):
    monkeypatch.setattr(reverify, "BaseSepoliaRpc", _FakeRpc)
    monkeypatch.setattr(reverify, "verify_evidence", lambda *_args, **_kwargs: evidence)


def test_reverifies_successful_transfer_and_audit_record(monkeypatch):
    bundle = _bundle()
    evidence = bundle["testnet_evidence"]["evidence"]
    _install_chain(monkeypatch, evidence)
    monkeypatch.setattr(
        reverify, "verify_audit_record", lambda *_args: "txn-123"
    )

    result = reverify.reverify_testnet_evidence(
        bundle, expected_sender=SENDER, audit_db="aegis.sqlite3"
    )

    assert result["mode"] == "successful_transfer"
    assert result["confirmations_observed"] == 14
    assert result["audit_transaction_id"] == "txn-123"


def test_reverification_fails_if_current_chain_evidence_differs(monkeypatch):
    bundle = _bundle()
    evidence = dict(bundle["testnet_evidence"]["evidence"])
    evidence["recipient"] = "0x3333333333333333333333333333333333333333"
    _install_chain(monkeypatch, evidence)

    with pytest.raises(ValueError, match="differs from bundle field: recipient"):
        reverify.reverify_testnet_evidence(
            bundle, expected_sender=SENDER, audit_db="aegis.sqlite3"
        )


def test_reverifies_reverted_call_without_audit_database(monkeypatch):
    bundle = _bundle("reverted_vault_call")
    evidence = bundle["testnet_evidence"]["evidence"]
    _install_chain(monkeypatch, evidence)
    monkeypatch.setattr(
        reverify,
        "verify_audit_record",
        lambda *_args: pytest.fail("Reverted calls must not require a successful-transfer audit"),
    )

    result = reverify.reverify_testnet_evidence(bundle, expected_sender=SENDER)

    assert result["mode"] == "reverted_vault_call"
    assert result["receipt_status"] == "failed"
    assert result["audit_transaction_id"] is None


def test_successful_transfer_requires_audit_database(monkeypatch):
    bundle = _bundle()
    _install_chain(monkeypatch, bundle["testnet_evidence"]["evidence"])

    with pytest.raises(ValueError, match="requires --audit-db"):
        reverify.reverify_testnet_evidence(bundle, expected_sender=SENDER)


def test_reverification_rejects_sender_mismatch(monkeypatch):
    bundle = _bundle()
    _install_chain(monkeypatch, bundle["testnet_evidence"]["evidence"])
    monkeypatch.setattr(reverify, "verify_audit_record", lambda *_args: "txn-123")

    with pytest.raises(ValueError, match="does not match --expected-sender"):
        reverify.reverify_testnet_evidence(
            bundle,
            expected_sender="0x5555555555555555555555555555555555555555",
            audit_db="aegis.sqlite3",
        )


def test_reverification_enforces_confirmation_depth(monkeypatch):
    bundle = _bundle()
    evidence = dict(bundle["testnet_evidence"]["evidence"])
    evidence["confirmations_observed"] = 2
    _install_chain(monkeypatch, evidence)
    monkeypatch.setattr(reverify, "verify_audit_record", lambda *_args: "txn-123")

    with pytest.raises(ValueError, match="fewer than the 12 required"):
        reverify.reverify_testnet_evidence(
            bundle, expected_sender=SENDER, audit_db="aegis.sqlite3"
        )
