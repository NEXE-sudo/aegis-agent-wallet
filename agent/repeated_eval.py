"""Repeat the offline evaluation and report per-scenario pass rates."""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from agent.eval import run_evaluation


def summarise_runs(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate runs while rejecting incomplete or structurally inconsistent evidence."""
    if not reports:
        raise ValueError("At least one evaluation report is required")

    scenario_results: dict[str, list[bool]] = defaultdict(list)
    expected_names: set[str] | None = None
    for report_index, report in enumerate(reports, start=1):
        if not isinstance(report, dict):
            raise ValueError(f"Run {report_index} must be an evaluation report object")
        if not isinstance(report.get("passed"), bool):
            raise ValueError(f"Run {report_index} must contain a boolean passed field")
        scenarios = report.get("scenarios")
        if not isinstance(scenarios, list) or not scenarios:
            raise ValueError(f"Run {report_index} must contain at least one scenario")
        names: set[str] = set()
        for scenario in scenarios:
            if not isinstance(scenario, dict):
                raise ValueError(f"Run {report_index} contains an invalid scenario record")
            name = scenario.get("name")
            passed = scenario.get("passed")
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"Run {report_index} contains a scenario without a name")
            if name in names:
                raise ValueError(f"Run {report_index} contains duplicate scenario {name!r}")
            if not isinstance(passed, bool):
                raise ValueError(
                    f"Run {report_index} scenario {name!r} must have a boolean passed field"
                )
            names.add(name)
            scenario_results[name].append(passed)
        declared_count = report.get("scenario_count")
        if declared_count is not None and declared_count != len(scenarios):
            raise ValueError(f"Run {report_index} scenario_count does not match its records")
        if expected_names is None:
            expected_names = names
        elif names != expected_names:
            missing = sorted(expected_names - names)
            unexpected = sorted(names - expected_names)
            raise ValueError(
                f"Run {report_index} scenario inventory differs "
                f"(missing={missing}, unexpected={unexpected})"
            )

    scenario_summary = []
    for name in sorted(scenario_results):
        outcomes = scenario_results[name]
        scenario_summary.append(
            {
                "name": name,
                "runs_observed": len(outcomes),
                "passed_runs": sum(outcomes),
                "failed_runs": len(outcomes) - sum(outcomes),
                "pass_rate": sum(outcomes) / len(outcomes),
            }
        )

    total_passed = sum(item["passed_runs"] for item in scenario_summary)
    total_observed = sum(item["runs_observed"] for item in scenario_summary)
    return {
        "run_count": len(reports),
        "all_runs_passed": all(report["passed"] for report in reports),
        "scenario_names": len(scenario_summary),
        "scenario_observations": total_observed,
        "passed_observations": total_passed,
        "failed_observations": total_observed - total_passed,
        "overall_pass_rate": total_passed / total_observed if total_observed else 1.0,
        "scenarios": scenario_summary,
        "runs": [
            {
                "run": index + 1,
                "passed": report["passed"],
                "scenario_count": report.get("scenario_count", len(report["scenarios"])),
                "failed_names": report.get("summary", {}).get("failed_names", []),
            }
            for index, report in enumerate(reports)
        ],
    }


async def run_repeated_evaluation(run_count: int, output_dir: Path) -> dict[str, Any]:
    """Run isolated deterministic evaluations and persist aggregate evidence."""
    if not 1 <= run_count <= 50:
        raise ValueError("run_count must be between 1 and 50")

    reports: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="aegis-repeat-eval-") as temporary_root:
        root = Path(temporary_root)
        for index in range(run_count):
            reports.append(await run_evaluation(root / f"run-{index + 1}"))

    aggregate = summarise_runs(reports)
    aggregate["agent_mode"] = "deterministic_offline"
    aggregate["database_mode"] = "isolated_temporary_sqlite_per_scenario"
    aggregate["network_dependencies"] = "none"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "agent-repeat-eval.json").write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Repeated Aegis Agent Evaluation",
        "",
        "- Mode: deterministic offline fallback",
        "- State: isolated temporary SQLite databases for each run",
        "- Network dependencies: none (no live RPC, market data, or LLM calls)",
        f"- Runs: {aggregate['run_count']}",
        f"- Unique scenarios: {aggregate['scenario_names']}",
        f"- Scenario observations: {aggregate['scenario_observations']}",
        f"- Passed observations: {aggregate['passed_observations']}",
        f"- Failed observations: {aggregate['failed_observations']}",
        f"- Overall pass rate: {aggregate['overall_pass_rate']:.1%}",
        f"- Overall result: {'PASS' if aggregate['all_runs_passed'] else 'FAIL'}",
        "",
        "| Scenario | Passed runs | Failed runs | Pass rate |",
        "| --- | ---: | ---: | ---: |",
    ]
    for scenario in aggregate["scenarios"]:
        lines.append(
            f"| {scenario['name']} | {scenario['passed_runs']} / "
            f"{scenario['runs_observed']} | {scenario['failed_runs']} | "
            f"{scenario['pass_rate']:.1%} |"
        )
    lines.extend(
        [
            "",
            "## Run-by-run results",
            "",
            "| Run | Result | Failed scenarios |",
            "| ---: | --- | --- |",
        ]
    )
    for run in aggregate["runs"]:
        failed = ", ".join(run["failed_names"]) or "—"
        lines.append(f"| {run['run']} | {'PASS' if run['passed'] else 'FAIL'} | {failed} |")
    lines.extend(
        [
            "",
            (
                "This report measures repeatability of the deterministic fallback. "
                "It does not measure live LLM quality, chain execution, or real payment outcomes."
            ),
            "",
        ]
    )
    (output_dir / "agent-repeat-eval.md").write_text("\n".join(lines), encoding="utf-8")
    return aggregate


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repeat deterministic offline agent scenarios and aggregate pass rates."
    )
    parser.add_argument("--runs", type=int, default=5, help="number of isolated runs (1–50)")
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()
    if not 1 <= args.runs <= 50:
        parser.error("--runs must be between 1 and 50")
    report = asyncio.run(run_repeated_evaluation(args.runs, args.output_dir))
    print(
        f"Agent evaluation: {'PASS' if report['all_runs_passed'] else 'FAIL'}; "
        f"{report['passed_observations']}/{report['scenario_observations']} "
        f"scenario observations passed across {report['run_count']} runs."
    )
    return 0 if report["all_runs_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
