from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import scripts.submission_preflight as submission_preflight
from scripts.submission_manifest import build_manifest


REVISION = "a" * 40


def _canonical_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _make_fixture(root: Path, monkeypatch) -> tuple[Path, Path, Path, Path]:
    agent_eval_path = Path("reports/submission/agent-eval/agent-eval.json")
    repeated_path = Path("reports/submission/repeated-eval/agent-repeat-eval.json")
    bundle_path = Path("reports/submission/evidence/aegis-evidence-bundle.json")
    manifest_path = Path("reports/submission/submission-manifest.json")

    _write_json(
        root / agent_eval_path,
        {
            "passed": True,
            "scenario_count": 1,
            "scenarios": [{"name": "allowed task", "passed": True}],
        },
    )
    _write_json(
        root / repeated_path,
        {
            "run_count": 5,
            "all_runs_passed": True,
            "scenario_names": 1,
            "scenario_observations": 5,
            "passed_observations": 5,
            "failed_observations": 0,
            "overall_pass_rate": 1.0,
            "scenarios": [
                {
                    "name": "allowed task",
                    "runs_observed": 5,
                    "passed_runs": 5,
                    "failed_runs": 0,
                    "pass_rate": 1.0,
                }
            ],
        },
    )
    bundle: dict[str, Any] = {
        "schema": "aegis-evidence-bundle/v6",
        "passed": True,
        "offline_evaluation": {"passed": True},
        "provenance": {"source_revision": REVISION},
        "testnet_evidence": {
            "requested": False,
            "passed": None,
            "evidence": None,
            "checks": {},
        },
    }
    bundle["bundle_sha256"] = _canonical_digest(bundle)
    _write_json(root / bundle_path, bundle)

    manifest = build_manifest(
        root,
        [agent_eval_path, repeated_path, bundle_path],
        generated_at="2026-10-20T12:00:00+00:00",
    )
    manifest["source_revision"] = REVISION
    manifest["source_tree_clean"] = True
    manifest.pop("manifest_sha256")
    manifest["manifest_sha256"] = _canonical_digest(manifest)
    _write_json(root / manifest_path, manifest)
    monkeypatch.setattr(submission_preflight, "_current_revision", lambda _root: REVISION)
    return manifest_path, agent_eval_path, repeated_path, bundle_path


def test_preflight_accepts_consistent_offline_submission_artifacts(tmp_path, monkeypatch) -> None:
    paths = _make_fixture(tmp_path, monkeypatch)

    notes = submission_preflight.run_preflight(tmp_path, *paths)

    assert any("Manifest and selected artifact hashes: PASS" in note for note in notes)
    assert any("NOT SUPPLIED" in note for note in notes)


def test_preflight_rejects_manifest_from_another_revision(tmp_path, monkeypatch) -> None:
    paths = _make_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(submission_preflight, "_current_revision", lambda _root: "b" * 40)

    with pytest.raises(ValueError, match="source_revision does not match"):
        submission_preflight.run_preflight(tmp_path, *paths)


def test_preflight_rejects_failed_repeated_evaluation(tmp_path, monkeypatch) -> None:
    manifest, agent_eval, repeated, bundle = _make_fixture(tmp_path, monkeypatch)
    report_path = tmp_path / repeated
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["failed_observations"] = 1
    report["passed_observations"] = 4
    report["all_runs_passed"] = False
    report["scenarios"][0].update(passed_runs=4, failed_runs=1, pass_rate=0.8)
    _write_json(report_path, report)

    refreshed = build_manifest(
        tmp_path,
        [agent_eval, repeated, bundle],
        generated_at="2026-10-20T12:00:00+00:00",
    )
    refreshed["source_revision"] = REVISION
    refreshed["source_tree_clean"] = True
    refreshed.pop("manifest_sha256")
    refreshed["manifest_sha256"] = _canonical_digest(refreshed)
    _write_json(tmp_path / manifest, refreshed)

    with pytest.raises(ValueError, match="contains failures"):
        submission_preflight.run_preflight(tmp_path, manifest, agent_eval, repeated, bundle)


def test_preflight_can_require_genuine_testnet_evidence(tmp_path, monkeypatch) -> None:
    paths = _make_fixture(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="Base Sepolia evidence was required"):
        submission_preflight.run_preflight(tmp_path, *paths, require_testnet=True)


def test_preflight_rejects_missing_manifest_artifact(tmp_path, monkeypatch) -> None:
    paths = _make_fixture(tmp_path, monkeypatch)
    manifest_path, agent_eval, repeated, bundle = paths
    manifest = json.loads((tmp_path / manifest_path).read_text(encoding="utf-8"))
    manifest["artifacts"] = [item for item in manifest["artifacts"] if item["path"] != repeated.as_posix()]
    manifest.pop("manifest_sha256")
    manifest["manifest_sha256"] = _canonical_digest(manifest)
    _write_json(tmp_path / manifest_path, manifest)

    with pytest.raises(ValueError, match="does not include selected artifact"):
        submission_preflight.run_preflight(tmp_path, manifest_path, agent_eval, repeated, bundle)
