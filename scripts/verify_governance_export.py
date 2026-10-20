"""Offline structural verifier for owner-generated governance JSON and CSV exports."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

CSV_REQUIRED_COLUMNS = {
    "task_id", "invoice_id", "payee_id", "amount_base_units", "token_symbol",
    "task_status", "governance_integrity_valid",
}


def verify_json_export(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["JSON export must be an object"]
    if payload.get("schema_version") != 1:
        errors.append("unsupported or missing schema_version (expected 1)")
    integrity = payload.get("integrity")
    if not isinstance(integrity, dict) or integrity.get("valid") is not True:
        errors.append("export does not report a valid governance audit chain")
    if payload.get("mandate") is not None and not isinstance(payload.get("mandate"), dict):
        errors.append("mandate must be an object or null")
    for field in ("payees", "tasks"):
        if not isinstance(payload.get(field), list):
            errors.append(f"{field} must be a list")
    if not isinstance(payload.get("limitations"), list) or not payload["limitations"]:
        errors.append("limitations must be a non-empty list")
    return errors


def verify_csv_export(content: str) -> list[str]:
    reader = csv.DictReader(content.splitlines())
    columns = set(reader.fieldnames or [])
    missing = sorted(CSV_REQUIRED_COLUMNS - columns)
    if missing:
        return [f"CSV missing required columns: {', '.join(missing)}"]
    errors = []
    for line_number, row in enumerate(reader, start=2):
        if row.get("governance_integrity_valid", "").lower() != "true":
            errors.append(f"row {line_number} does not report valid governance integrity")
    return errors


def verify_export(path: Path) -> list[str]:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        return [f"cannot read export: {exc}"]
    if path.suffix.lower() == ".json":
        try:
            return verify_json_export(json.loads(content))
        except json.JSONDecodeError as exc:
            return [f"invalid JSON: {exc}"]
    if path.suffix.lower() == ".csv":
        try:
            return verify_csv_export(content)
        except csv.Error as exc:
            return [f"invalid CSV: {exc}"]
    return ["export extension must be .json or .csv"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="owner-generated .json or .csv export")
    args = parser.parse_args()
    errors = verify_export(args.export)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("Export structure and declared governance-integrity status passed.")
    print("This is not a signature, provenance proof, or independent audit-chain verification.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
