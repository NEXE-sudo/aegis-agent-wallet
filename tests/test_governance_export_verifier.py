"""Tests for structural checks on exported governance files."""
from __future__ import annotations

from pathlib import Path

from scripts.verify_governance_export import verify_csv_export, verify_export, verify_json_export


def valid_json_export() -> dict:
    return {
        "schema_version": 1,
        "exported_at": "2026-10-20T12:00:00+00:00",
        "integrity": {"valid": True},
        "mandate": None,
        "payees": [],
        "tasks": [],
        "limitations": ["Hashes are not signatures."],
    }


def test_json_export_checks_schema_and_integrity_claim() -> None:
    assert verify_json_export(valid_json_export()) == []
    payload = valid_json_export()
    payload["integrity"] = {"valid": False}
    assert any("valid governance audit chain" in e for e in verify_json_export(payload))


def test_json_export_rejects_missing_fields_and_wrong_types() -> None:
    assert verify_json_export({"schema_version": 99})


def test_csv_export_checks_columns_and_each_integrity_flag() -> None:
    header = (
        "task_id,invoice_id,payee_id,amount_base_units,token_symbol,task_status,"
        "governance_integrity_valid"
    )
    assert verify_csv_export(header + "\nt1,inv1,p1,10,USDC,pending,true") == []
    assert verify_csv_export(header + "\nt1,inv1,p1,10,USDC,pending,false")


def test_export_reader_reports_bad_json_and_unknown_extension(tmp_path: Path) -> None:
    path = tmp_path / "export.json"
    path.write_text("{bad", encoding="utf-8")
    assert any("invalid JSON" in e for e in verify_export(path))
    unsupported = tmp_path / "export.txt"
    unsupported.write_text("anything", encoding="utf-8")
    assert verify_export(unsupported)
