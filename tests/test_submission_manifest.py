from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.submission_manifest import build_manifest, main, verify_manifest


def test_submission_manifest_is_stable_except_for_runtime_metadata(tmp_path: Path) -> None:
    artifact = tmp_path / "reports" / "evaluation.json"
    artifact.parent.mkdir()
    artifact.write_text('{"passed": true}\n', encoding="utf-8")

    manifest = build_manifest(
        tmp_path,
        ["reports/evaluation.json"],
        generated_at="2026-10-20T12:00:00+00:00",
    )

    assert manifest["schema"] == "aegis-submission-manifest/v1"
    assert manifest["artifacts"] == [
        {
            "path": "reports/evaluation.json",
            "size_bytes": len(artifact.read_bytes()),
            "sha256": manifest["artifacts"][0]["sha256"],
        }
    ]
    assert verify_manifest(manifest, tmp_path) is manifest


def test_submission_manifest_rejects_modified_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "evaluation.json"
    artifact.write_text("original evidence", encoding="utf-8")
    manifest = build_manifest(tmp_path, ["evaluation.json"])
    artifact.write_text("modified evidence", encoding="utf-8")

    with pytest.raises(ValueError, match="size mismatch|SHA-256 mismatch"):
        verify_manifest(manifest, tmp_path)


def test_submission_manifest_rejects_manifest_digest_tampering(tmp_path: Path) -> None:
    artifact = tmp_path / "evaluation.json"
    artifact.write_text("evidence", encoding="utf-8")
    manifest = build_manifest(tmp_path, ["evaluation.json"])
    manifest["python_version"] = "tampered"

    with pytest.raises(ValueError, match="manifest SHA-256"):
        verify_manifest(manifest, tmp_path)


def test_submission_manifest_rejects_artifacts_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("not part of the root", encoding="utf-8")

    with pytest.raises(ValueError, match="inside the root"):
        build_manifest(root, [outside])


def test_submission_manifest_cli_build_and_verify(tmp_path: Path) -> None:
    artifact = tmp_path / "reports" / "evaluation.json"
    artifact.parent.mkdir()
    artifact.write_text(json.dumps({"passed": True}), encoding="utf-8")
    manifest_path = tmp_path / "reports" / "submission-manifest.json"

    assert main(
        [
            "build",
            "--root",
            str(tmp_path),
            "--output",
            str(manifest_path),
            "--artifact",
            "reports/evaluation.json",
        ]
    ) == 0
    assert manifest_path.is_file()
    assert manifest_path.with_suffix(".md").is_file()
    assert main(
        [
            "verify",
            str(manifest_path),
            "--root",
            str(tmp_path),
        ]
    ) == 0
