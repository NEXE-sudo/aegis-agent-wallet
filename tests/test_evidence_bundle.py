"""Tests for the portable evaluation and testnet evidence bundle."""
import asyncio
import hashlib
import json

import pytest

from scripts import build_evidence_bundle as bundle_script


def _offline_report(passed=True):
    return {
        "generated_at": "2026-10-20T00:00:00+00:00",
        "agent_mode": "deterministic_offline",
        "database_mode": "temporary_sqlite",
        "passed": passed,
        "scenario_count": 2,
        "scenarios": [
            {"name": "allowed proposal", "passed": True},
            {"name": "unsafe chain", "passed": passed},
        ],
    }


def _install_offline_eval(monkeypatch, report=None):
    async def fake_run_evaluation(_output_dir):
        return report or _offline_report()

    monkeypatch.setattr(bundle_script, "run_evaluation", fake_run_evaluation)


def test_offline_bundle_is_portable_and_explicitly_not_onchain(monkeypatch, tmp_path):
    _install_offline_eval(monkeypatch)
    revision = "a" * 40
    monkeypatch.setattr(bundle_script, "_source_revision", lambda: revision)

    bundle = asyncio.run(bundle_script.build_evidence_bundle(tmp_path / "bundle"))

    assert bundle["schema"] == "aegis-evidence-bundle/v5"
    assert bundle["provenance"] == {
        "source_revision": revision,
        "python_version": bundle_script.platform.python_version(),
        "python_implementation": bundle_script.platform.python_implementation(),
    }
    assert bundle["passed"] is True
    assert bundle["testnet_evidence"]["requested"] is False
    assert bundle["testnet_evidence"]["passed"] is None
    markdown = (tmp_path / "bundle" / "aegis-evidence-bundle.md").read_text()
    assert "does not demonstrate an on-chain transfer" in markdown
    assert f"Source revision: `{revision}`" in markdown
    assert f"Python: `{bundle['provenance']['python_version']}`" in markdown
    saved = json.loads((tmp_path / "bundle" / "aegis-evidence-bundle.json").read_text())
    digest = saved.pop("bundle_sha256")
    canonical = json.dumps(saved, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(canonical.encode()).hexdigest() == digest


def test_testnet_bundle_requires_all_verification_inputs(monkeypatch, tmp_path):
    _install_offline_eval(monkeypatch)

    bundle = asyncio.run(
        bundle_script.build_evidence_bundle(
            tmp_path / "bundle",
            transaction_hash="0x" + "a" * 64,
        )
    )

    assert bundle["passed"] is False
    assert bundle["testnet_evidence"]["checks"]["chain_and_transfer_evidence_verified"] is False
    assert (
        "requires --expected-sender, --audit-db, and --vault"
        in bundle["testnet_evidence"]["error"]
    )


def test_successful_testnet_bundle_links_chain_and_audit(monkeypatch, tmp_path):
    _install_offline_eval(monkeypatch)

    class FakeRpc:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    evidence = {
        "chain_id": 84532,
        "receipt_status": "success",
        "block_number": 123,
        "receipt_block_hash": "0x" + "c" * 64,
        "canonical_block_hash": "0x" + "c" * 64,
        "latest_block_number": 136,
        "confirmations_observed": 14,
        "token_contract": "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
        "sender": "0x1111111111111111111111111111111111111111",
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 2_500_000,
        "transaction_hash": "0x" + "a" * 64,
        "explorer_url": "https://sepolia.basescan.org/tx/" + "0x" + "a" * 64,
        "vault_address": "0x4444444444444444444444444444444444444444",
        "proposal_reference": "0x" + "b" * 64,
    }
    monkeypatch.setattr(bundle_script, "BaseSepoliaRpc", FakeRpc)
    monkeypatch.setattr(bundle_script, "verify_evidence", lambda *_args: evidence)
    monkeypatch.setattr(bundle_script, "verify_audit_record", lambda *_args: "txn-123")

    bundle = asyncio.run(
        bundle_script.build_evidence_bundle(
            tmp_path / "bundle",
            transaction_hash=evidence["transaction_hash"],
            expected_sender=evidence["sender"],
            audit_db=tmp_path / "workflow.sqlite3",
            vault=evidence["vault_address"],
        )
    )

    assert bundle["passed"] is True
    assert bundle["testnet_evidence"]["checks"] == {
        "chain_and_transfer_evidence_verified": True,
        "expected_sender_matches": True,
        "audit_record_matches": True,
        "receipt_status_matches_expectation": True,
        "minimum_confirmations_met": True,
    }
    assert bundle["testnet_evidence"]["audit_transaction_id"] == "txn-123"


def test_testnet_bundle_fails_when_confirmation_depth_is_too_low(monkeypatch, tmp_path):
    _install_offline_eval(monkeypatch)

    class FakeRpc:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    evidence = {
        "chain_id": 84532,
        "receipt_status": "success",
        "block_number": 123,
        "receipt_block_hash": "0x" + "c" * 64,
        "canonical_block_hash": "0x" + "c" * 64,
        "latest_block_number": 124,
        "confirmations_observed": 2,
        "token_contract": "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
        "sender": "0x1111111111111111111111111111111111111111",
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 2_500_000,
        "transaction_hash": "0x" + "a" * 64,
        "explorer_url": "https://sepolia.basescan.org/tx/0x" + "a" * 64,
        "vault_address": "0x4444444444444444444444444444444444444444",
        "proposal_reference": "0x" + "b" * 64,
    }
    monkeypatch.setattr(bundle_script, "BaseSepoliaRpc", FakeRpc)
    monkeypatch.setattr(bundle_script, "verify_evidence", lambda *_args: evidence)
    monkeypatch.setattr(bundle_script, "verify_audit_record", lambda *_args: "txn-123")

    bundle = asyncio.run(
        bundle_script.build_evidence_bundle(
            tmp_path / "bundle",
            transaction_hash=evidence["transaction_hash"],
            expected_sender=evidence["sender"],
            audit_db=tmp_path / "workflow.sqlite3",
            vault=evidence["vault_address"],
            min_confirmations=3,
        )
    )

    assert bundle["passed"] is False
    assert bundle["testnet_evidence"]["checks"]["minimum_confirmations_met"] is False
    assert "Observed 2 confirmations, fewer than the 3 required" in (
        bundle["testnet_evidence"]["error"]
    )
    report = (tmp_path / "bundle" / "aegis-evidence-bundle.md").read_text()
    assert "Receipt block hash: 0x" + "c" * 64 in report
    assert "Canonical block hash at verification: 0x" + "c" * 64 in report
    assert "Confirmations observed: 2 (minimum required: 3)" in report


def test_testnet_bundle_fails_closed_on_wrong_sender(monkeypatch, tmp_path):
    _install_offline_eval(monkeypatch)

    class FakeRpc:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    evidence = {
        "chain_id": 84532,
        "receipt_status": "success",
        "sender": "0x1111111111111111111111111111111111111111",
    }
    monkeypatch.setattr(bundle_script, "BaseSepoliaRpc", FakeRpc)
    monkeypatch.setattr(bundle_script, "verify_evidence", lambda *_args: evidence)
    monkeypatch.setattr(
        bundle_script,
        "verify_audit_record",
        lambda *_args: pytest.fail("Audit must not be checked after sender mismatch"),
    )

    bundle = asyncio.run(
        bundle_script.build_evidence_bundle(
            tmp_path / "bundle",
            transaction_hash="0x" + "a" * 64,
            expected_sender="0x2222222222222222222222222222222222222222",
            audit_db=tmp_path / "workflow.sqlite3",
            vault="0x4444444444444444444444444444444444444444",
        )
    )

    assert bundle["passed"] is False
    assert bundle["testnet_evidence"]["checks"]["expected_sender_matches"] is False
    assert bundle["testnet_evidence"]["checks"]["audit_record_matches"] is False
    report = (tmp_path / "bundle" / "aegis-evidence-bundle.md").read_text()
    assert "Verification error: Transaction sender does not match --expected-sender" in report
    assert "Transaction: not supplied" in report


def test_failed_receipt_does_not_pass_testnet_bundle(monkeypatch, tmp_path):
    _install_offline_eval(monkeypatch)

    class FakeRpc:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    evidence = {
        "chain_id": 84532,
        "receipt_status": "failed",
        "sender": "0x1111111111111111111111111111111111111111",
    }
    monkeypatch.setattr(bundle_script, "BaseSepoliaRpc", FakeRpc)
    monkeypatch.setattr(bundle_script, "verify_evidence", lambda *_args: evidence)
    monkeypatch.setattr(bundle_script, "verify_audit_record", lambda *_args: "txn-123")

    bundle = asyncio.run(
        bundle_script.build_evidence_bundle(
            tmp_path / "bundle",
            transaction_hash="0x" + "a" * 64,
            expected_sender=evidence["sender"],
            audit_db=tmp_path / "workflow.sqlite3",
            vault="0x4444444444444444444444444444444444444444",
        )
    )

    assert bundle["passed"] is False
    assert bundle["testnet_evidence"]["checks"]["receipt_status_matches_expectation"] is False
    assert "receipt status is not success" in bundle["testnet_evidence"]["error"]
    report = (tmp_path / "bundle" / "aegis-evidence-bundle.md").read_text()
    assert "Verification error: Transaction receipt status is not success" in report
    assert "Transaction: not supplied" in report


def test_reverted_testnet_bundle_verifies_failed_call_without_audit_db(monkeypatch, tmp_path):
    _install_offline_eval(monkeypatch)

    class FakeRpc:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    evidence = {
        "chain_id": 84532,
        "receipt_status": "failed",
        "block_number": 123,
        "receipt_block_hash": "0x" + "c" * 64,
        "canonical_block_hash": "0x" + "c" * 64,
        "latest_block_number": 136,
        "confirmations_observed": 14,
        "token_contract": "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
        "sender": "0x1111111111111111111111111111111111111111",
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 2500000,
        "transaction_hash": "0x" + "a" * 64,
        "explorer_url": "https://sepolia.basescan.org/tx/0x" + "a" * 64,
        "vault_address": "0x4444444444444444444444444444444444444444",
        "proposal_reference": "0x" + "b" * 64,
    }
    monkeypatch.setattr(bundle_script, "BaseSepoliaRpc", FakeRpc)
    monkeypatch.setattr(
        bundle_script,
        "verify_evidence",
        lambda *args, **kwargs: evidence if kwargs.get("expect_revert") else pytest.fail("Expected revert mode"),
    )
    monkeypatch.setattr(
        bundle_script,
        "verify_audit_record",
        lambda *_args: pytest.fail("Revert mode must not require a successful transfer audit record"),
    )

    bundle = asyncio.run(
        bundle_script.build_evidence_bundle(
            tmp_path / "bundle",
            transaction_hash=evidence["transaction_hash"],
            expected_sender=evidence["sender"],
            vault=evidence["vault_address"],
            expect_revert=True,
            min_confirmations=12,
        )
    )

    assert bundle["passed"] is True
    assert bundle["testnet_evidence"]["mode"] == "reverted_vault_call"
    assert bundle["testnet_evidence"]["checks"]["receipt_status_matches_expectation"] is True
    assert "audit_record_matches" not in bundle["testnet_evidence"]["checks"]
    assert bundle["testnet_evidence"]["audit_transaction_id"] is None


def test_cli_requires_complete_testnet_arguments():
    with pytest.raises(SystemExit) as exc:
        bundle_script.main(["--transaction-hash", "0x" + "a" * 64])
    assert exc.value.code == 2
