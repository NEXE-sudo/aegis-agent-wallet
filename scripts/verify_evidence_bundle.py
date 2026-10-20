"""Validate the integrity and internal consistency of a saved Aegis evidence bundle."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import sys
from pathlib import Path
from typing import Any

SUPPORTED_SCHEMAS = {"aegis-evidence-bundle/v4", "aegis-evidence-bundle/v5"}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate_bundle(bundle: object) -> dict[str, Any]:
    """Validate the digest and cross-field consistency without contacting an RPC."""
    if not isinstance(bundle, dict):
        raise ValueError("Evidence bundle must be a JSON object")

    digest = bundle.get("bundle_sha256")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise ValueError("Evidence bundle has a missing or malformed bundle_sha256")

    payload = dict(bundle)
    payload.pop("bundle_sha256")
    expected_digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    if not hmac.compare_digest(digest, expected_digest):
        raise ValueError("Evidence bundle SHA-256 digest does not match its payload")

    schema = bundle.get("schema")
    if not isinstance(schema, str) or schema not in SUPPORTED_SCHEMAS:
        raise ValueError(f"Unsupported evidence bundle schema: {schema!r}")
    if type(bundle.get("passed")) is not bool:
        raise ValueError("Evidence bundle passed field must be a boolean")

    offline = bundle.get("offline_evaluation")
    if not isinstance(offline, dict) or type(offline.get("passed")) is not bool:
        raise ValueError("Evidence bundle has malformed offline_evaluation results")

    testnet = bundle.get("testnet_evidence")
    if not isinstance(testnet, dict) or type(testnet.get("requested")) is not bool:
        raise ValueError("Evidence bundle has malformed testnet_evidence")
    requested = testnet["requested"]
    testnet_passed = testnet.get("passed")
    checks = testnet.get("checks")
    evidence = testnet.get("evidence")

    if not requested:
        if testnet_passed is not None or evidence is not None or checks != {}:
            raise ValueError("Unrequested testnet evidence must be marked not supplied")
        expected_overall = offline["passed"]
    else:
        if type(testnet_passed) is not bool or not isinstance(checks, dict):
            raise ValueError("Requested testnet evidence has malformed result checks")
        if any(type(value) is not bool for value in checks.values()):
            raise ValueError("Testnet evidence checks must contain boolean results")
        expected_overall = offline["passed"] and testnet_passed

        if testnet_passed:
            if not isinstance(evidence, dict):
                raise ValueError("Passing testnet evidence must include transaction evidence")
            if not checks or not all(checks.values()):
                raise ValueError("Passing testnet evidence must have all checks marked true")
            mode = testnet.get("mode", "successful_transfer")
            if mode == "successful_transfer":
                if evidence.get("receipt_status") != "success":
                    raise ValueError("Successful-transfer evidence must have a successful receipt")
                if checks.get("audit_record_matches") is not True:
                    raise ValueError("Successful-transfer evidence must pass its audit cross-check")
            elif mode == "reverted_vault_call":
                if schema != "aegis-evidence-bundle/v5":
                    raise ValueError("Reverted-call evidence requires schema v5")
                if evidence.get("receipt_status") != "failed":
                    raise ValueError("Reverted-call evidence must have a failed receipt")
                if "audit_record_matches" in checks:
                    raise ValueError("Reverted-call evidence must not claim a successful-transfer audit")
            else:
                raise ValueError(f"Unsupported testnet evidence mode: {mode!r}")

            if schema == "aegis-evidence-bundle/v5":
                if checks.get("receipt_status_matches_expectation") is not True:
                    raise ValueError("Schema v5 evidence must verify the expected receipt status")

    if bundle["passed"] is not expected_overall:
        raise ValueError("Overall passed result is inconsistent with the component results")
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a saved Aegis evidence bundle's digest and internal consistency."
    )
    parser.add_argument("bundle_path", type=Path, help="path to an evidence-bundle JSON file")
    args = parser.parse_args(argv)

    try:
        with args.bundle_path.open("r", encoding="utf-8") as stream:
            bundle = json.load(stream)
        validate_bundle(bundle)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"Evidence bundle verification failed: {exc}", file=sys.stderr)
        return 2

    print("Evidence bundle integrity and consistency: PASS")
    print(f"Schema: {bundle['schema']}")
    print(f"Bundle SHA-256: {bundle['bundle_sha256']}")
    print(f"Overall result: {'PASS' if bundle['passed'] else 'FAIL'}")
    return 0 if bundle["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
