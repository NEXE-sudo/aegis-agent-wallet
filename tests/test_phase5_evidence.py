"""Tests for the Phase 5 deployment-to-transfer evidence cross-check."""
from __future__ import annotations

import hashlib
import json

import pytest

from scripts.verify_phase5_evidence import validate_phase5_evidence

OWNER = "0x2222222222222222222222222222222222222222"
AGENT = "0x1111111111111111111111111111111111111111"
VAULT = "0x3333333333333333333333333333333333333333"
TOKEN = "0x4444444444444444444444444444444444444444"
DEPLOYMENT_TX = "0x" + "a" * 64
TRANSFER_TX = "0x" + "b" * 64
BLOCK_HASH = "0x" + "c" * 64
BYTECODE_HASH = "d" * 64


def _seal(value: dict) -> dict:
    payload = dict(value)
    payload.pop("bundle_sha256", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload["bundle_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def _deployment() -> dict:
    payload = {
        "chain_id": 84532,
        "transaction_hash": DEPLOYMENT_TX,
        "vault_address": VAULT,
        "expected_owner": OWNER,
        "constructor_owner": OWNER,
        "current_onchain_owner": OWNER,
        "receipt_status": "success",
        "receipt_block_hash": BLOCK_HASH,
        "canonical_block_hash": BLOCK_HASH,
        "confirmations_observed": 20,
        "minimum_confirmations_required": 12,
        "runtime_bytecode_sha256": BYTECODE_HASH,
        "expected_bytecode_hash_matched": True,
        "read_only": True,
    }
    payload["deployment_evidence_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return payload


def _bundle() -> dict:
    return _seal({
        "schema": "aegis-evidence-bundle/v6",
        "generated_at": "2026-10-20T00:00:00+00:00",
        "provenance": {
            "source_revision": "e" * 40,
            "python_version": "3.14.0",
            "python_implementation": "CPython",
        },
        "passed": True,
        "offline_evaluation": {
            "passed": True,
            "scenario_count": 1,
            "scenarios": [],
        },
        "testnet_evidence": {
            "requested": True,
            "mode": "successful_transfer",
            "passed": True,
            "checks": {
                "chain_and_transfer_evidence_verified": True,
                "expected_sender_matches": True,
                "audit_record_matches": True,
                "receipt_status_matches_expectation": True,
                "minimum_confirmations_met": True,
                "vault_configuration_valid": True,
            },
            "evidence": {
                "chain_id": 84532,
                "receipt_status": "success",
                "confirmations_observed": 15,
                "transaction_hash": TRANSFER_TX,
                "vault_address": VAULT,
                "token_contract": TOKEN,
            },
            "vault_configuration": {
                "chain_id": 84532,
                "vault_address": VAULT,
                "bytecode_sha256": BYTECODE_HASH,
                "owner": OWNER,
                "agent": AGENT,
                "paused": False,
                "token_address": TOKEN,
                "token_policy": {
                    "allowed": True,
                    "per_transaction_cap_base_units": 100,
                    "daily_cap_base_units": 200,
                    "approval_threshold_base_units": 80,
                },
                "configuration_structurally_valid": True,
                "issues": [],
            },
        },
        "limitations": ["Test fixture only."],
    })


def test_cross_check_accepts_matching_deployment_and_transfer_evidence():
    report = validate_phase5_evidence(_deployment(), _bundle())

    assert report["result"] == "PASS"
    assert report["chain_id"] == 84532
    assert report["vault_address"] == VAULT
    assert report["deployment_transaction_hash"] == DEPLOYMENT_TX
    assert report["transfer_transaction_hash"] == TRANSFER_TX
    assert all(report["checks"].values())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("vault_address", "0x5555555555555555555555555555555555555555", "vault addresses do not match"),
        ("expected_owner", "0x5555555555555555555555555555555555555555", "constructor owner does not match"),
        ("runtime_bytecode_sha256", "f" * 64, "bytecode fingerprint does not match"),
        ("chain_id", 1, "Base Sepolia"),
        ("receipt_status", "failed", "successful deployment receipt"),
        ("expected_bytecode_hash_matched", False, "bytecode fingerprint"),
    ],
)
def test_cross_check_rejects_deployment_mismatch(field, value, message):
    deployment = _deployment()
    deployment[field] = value
    payload = dict(deployment)
    payload.pop("deployment_evidence_sha256")
    deployment["deployment_evidence_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    with pytest.raises(ValueError, match=message):
        validate_phase5_evidence(deployment, _bundle())


def test_cross_check_rejects_deployment_digest_tampering():
    deployment = _deployment()
    deployment["vault_address"] = "0x5555555555555555555555555555555555555555"

    with pytest.raises(ValueError, match="digest does not match"):
        validate_phase5_evidence(deployment, _bundle())


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("vault", "vault addresses do not match"),
        ("owner", "owner does not match"),
        ("bytecode", "bytecode fingerprint does not match"),
        ("chain", "Base Sepolia"),
        ("same_transaction", "must be distinct"),
    ],
)
def test_cross_check_rejects_bundle_identity_mismatches(change, message):
    deployment = _deployment()
    bundle = _bundle()
    if change == "vault":
        bundle["testnet_evidence"]["evidence"]["vault_address"] = (
            "0x5555555555555555555555555555555555555555"
        )
    elif change == "owner":
        bundle["testnet_evidence"]["vault_configuration"]["owner"] = (
            "0x5555555555555555555555555555555555555555"
        )
    elif change == "bytecode":
        bundle["testnet_evidence"]["vault_configuration"]["bytecode_sha256"] = "f" * 64
    elif change == "chain":
        bundle["testnet_evidence"]["evidence"]["chain_id"] = 1
    else:
        bundle["testnet_evidence"]["evidence"]["transaction_hash"] = DEPLOYMENT_TX
    bundle = _seal(bundle)

    with pytest.raises(ValueError, match=message):
        validate_phase5_evidence(deployment, bundle)


def test_cross_check_rejects_legacy_bundle_schema():
    bundle = _bundle()
    bundle["schema"] = "aegis-evidence-bundle/v5"
    bundle = _seal(bundle)

    with pytest.raises(ValueError, match="schema-v6"):
        validate_phase5_evidence(_deployment(), bundle)


def test_cross_check_rejects_bundle_digest_tampering():
    bundle = _bundle()
    bundle["testnet_evidence"]["evidence"]["transaction_hash"] = "0x" + "9" * 64

    with pytest.raises(ValueError, match="SHA-256 digest does not match"):
        validate_phase5_evidence(_deployment(), bundle)


def test_cross_check_rejects_insufficient_deployment_confirmations():
    deployment = _deployment()
    deployment["confirmations_observed"] = 3
    payload = dict(deployment)
    payload.pop("deployment_evidence_sha256")
    deployment["deployment_evidence_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    with pytest.raises(ValueError, match="confirmation threshold"):
        validate_phase5_evidence(deployment, _bundle())
