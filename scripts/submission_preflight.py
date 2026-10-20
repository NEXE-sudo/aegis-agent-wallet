"""Run a fail-closed, offline preflight over the selected submission artifacts."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent.repeated_eval import summarise_runs
from scripts.submission_manifest import verify_manifest
from scripts.verify_evidence_bundle import validate_bundle

_REVISION_RE = re.compile(r"[0-9a-f]{40,64}")


def _current_revision(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    revision = result.stdout.strip().lower()
    return revision if result.returncode == 0 and _REVISION_RE.fullmatch(revision) else None


def _read_json(path: Path, label: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {label} JSON at {path}: {exc}") from exc


def _relative_artifact(root: Path, supplied: Path) -> str:
    candidate = supplied if supplied.is_absolute() else root / supplied
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Submission artifact must be inside root: {supplied}") from exc
    if not resolved.is_file():
        raise ValueError(f"Submission artifact does not exist: {supplied}")
    return relative.as_posix()


def _validate_agent_evaluation(report: object) -> dict[str, Any]:
    if not isinstance(report, dict) or type(report.get("passed")) is not bool:
        raise ValueError("Agent evaluation must be a JSON object with boolean passed")
    scenarios = report.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("Agent evaluation must contain at least one scenario")
    names: set[str] = set()
    for item in scenarios:
        if not isinstance(item, dict):
            raise ValueError("Agent evaluation contains an invalid scenario record")
        name, passed = item.get("name"), item.get("passed")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("Agent evaluation scenario names must be non-empty and unique")
        if type(passed) is not bool:
            raise ValueError(f"Agent evaluation scenario {name!r} must have boolean passed")
        names.add(name)
    if report.get("scenario_count") != len(scenarios):
        raise ValueError("Agent evaluation scenario_count does not match its records")
    if report["passed"] != all(item["passed"] for item in scenarios):
        raise ValueError("Agent evaluation overall result disagrees with scenario results")
    if not report["passed"]:
        raise ValueError("Agent evaluation contains failed scenarios")
    return report


def _validate_repeated_evaluation(report: object, minimum_runs: int) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise ValueError("Repeated evaluation must be a JSON object")
    runs = report.get("run_count")
    if type(runs) is not int or runs < minimum_runs:
        raise ValueError(f"Repeated evaluation must contain at least {minimum_runs} runs")
    scenarios = report.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("Repeated evaluation must contain per-scenario results")
    names: set[str] = set()
    for item in scenarios:
        if not isinstance(item, dict):
            raise ValueError("Repeated evaluation contains an invalid scenario record")
        name = item.get("name")
        observed = item.get("runs_observed")
        passed = item.get("passed_runs")
        failed = item.get("failed_runs")
        rate = item.get("pass_rate")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("Repeated evaluation scenario names must be non-empty and unique")
        names.add(name)
        if any(type(value) is not int for value in (observed, passed, failed)):
            raise ValueError(f"Repeated evaluation counts are invalid for {name!r}")
        if observed != runs or passed < 0 or failed < 0 or passed + failed != observed:
            raise ValueError(f"Repeated evaluation counts are inconsistent for {name!r}")
        if not isinstance(rate, (int, float)) or isinstance(rate, bool):
            raise ValueError(f"Repeated evaluation pass_rate is invalid for {name!r}")
        if rate != passed / observed:
            raise ValueError(f"Repeated evaluation pass_rate does not match counts for {name!r}")
        if failed:
            raise ValueError(f"Repeated evaluation contains failures for {name!r}")
    observations = runs * len(scenarios)
    if report.get("scenario_names") != len(scenarios):
        raise ValueError("Repeated evaluation scenario_names does not match its records")
    if report.get("scenario_observations") != observations:
        raise ValueError("Repeated evaluation observation total is inconsistent")
    if report.get("passed_observations") != observations or report.get("failed_observations") != 0:
        raise ValueError("Repeated evaluation totals do not show all observations passing")
    if report.get("all_runs_passed") is not True or report.get("overall_pass_rate") != 1.0:
        raise ValueError("Repeated evaluation overall result is not a full pass")
    return report


def run_preflight(
    root: Path,
    manifest_path: Path,
    agent_eval_path: Path,
    repeated_eval_path: Path,
    evidence_bundle_path: Path,
    *,
    minimum_runs: int = 5,
    require_testnet: bool = False,
) -> list[str]:
    """Validate source provenance and selected offline reports without network access."""
    root = root.resolve()
    if minimum_runs < 1:
        raise ValueError("minimum_runs must be at least 1")
    manifest_file = manifest_path if manifest_path.is_absolute() else root / manifest_path
    manifest = _read_json(manifest_file, "submission manifest")
    verify_manifest(manifest, root)

    current_revision = _current_revision(root)
    if current_revision is None:
        raise ValueError("Cannot determine current Git revision; run from a Git checkout")
    if manifest.get("source_revision") != current_revision:
        raise ValueError("Manifest source_revision does not match the current Git HEAD")
    if manifest.get("source_tree_clean") is not True:
        raise ValueError("Manifest does not record a clean tracked source tree")

    selected = [
        _relative_artifact(root, agent_eval_path),
        _relative_artifact(root, repeated_eval_path),
        _relative_artifact(root, evidence_bundle_path),
    ]
    manifested = {item["path"] for item in manifest["artifacts"]}
    missing = sorted(set(selected) - manifested)
    if missing:
        raise ValueError(f"Manifest does not include selected artifact(s): {', '.join(missing)}")

    agent_eval = _validate_agent_evaluation(
        _read_json(root / agent_eval_path if not agent_eval_path.is_absolute() else agent_eval_path, "agent evaluation")
    )
    repeated_eval = _validate_repeated_evaluation(
        _read_json(root / repeated_eval_path if not repeated_eval_path.is_absolute() else repeated_eval_path, "repeated evaluation"),
        minimum_runs,
    )
    bundle_path = root / evidence_bundle_path if not evidence_bundle_path.is_absolute() else evidence_bundle_path
    bundle = validate_bundle(_read_json(bundle_path, "evidence bundle"))
    if bundle["passed"] is not True or bundle["offline_evaluation"].get("passed") is not True:
        raise ValueError("Evidence bundle or its offline evaluation did not pass")
    provenance = bundle.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("source_revision") != current_revision:
        raise ValueError("Evidence bundle source_revision does not match the current Git HEAD")

    testnet = bundle["testnet_evidence"]
    if require_testnet and not (testnet.get("requested") is True and testnet.get("passed") is True):
        raise ValueError("Live Base Sepolia evidence was required but is not verified as passing")

    notes = [
        f"Source revision: {current_revision}",
        f"Deterministic scenarios passed: {len(agent_eval['scenarios'])}",
        f"Repeated evaluation: {repeated_eval['run_count']} runs, {repeated_eval['scenario_observations']} observations passed",
        "Manifest and selected artifact hashes: PASS",
        "Evidence bundle integrity and offline evaluation: PASS",
    ]
    if testnet.get("requested") is True and testnet.get("passed") is True:
        notes.append("Base Sepolia evidence bundle: PASS (operator evidence supplied and verifier passed)")
    else:
        notes.append("Base Sepolia evidence: NOT SUPPLIED; this preflight does not establish live deployment or transfer")
    return notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check submission report consistency and provenance offline; never creates chain evidence."
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--manifest", type=Path, default=Path("reports/submission/submission-manifest.json"))
    parser.add_argument("--agent-eval", type=Path, default=Path("reports/submission/agent-eval/agent-eval.json"))
    parser.add_argument("--repeated-eval", type=Path, default=Path("reports/submission/repeated-eval/agent-repeat-eval.json"))
    parser.add_argument("--evidence-bundle", type=Path, default=Path("reports/submission/evidence/aegis-evidence-bundle.json"))
    parser.add_argument("--minimum-runs", type=int, default=5)
    parser.add_argument("--require-testnet", action="store_true", help="fail unless verified passing Base Sepolia evidence is included")
    args = parser.parse_args(argv)

    try:
        notes = run_preflight(
            args.root,
            args.manifest,
            args.agent_eval,
            args.repeated_eval,
            args.evidence_bundle,
            minimum_runs=args.minimum_runs,
            require_testnet=args.require_testnet,
        )
    except (OSError, TypeError, ValueError) as exc:
        print(f"Submission preflight: FAIL — {exc}", file=sys.stderr)
        return 2
    print("Submission preflight: PASS")
    for note in notes:
        print(f"- {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
