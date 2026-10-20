"""Tests for repeated deterministic agent evaluation aggregation."""

import pytest

from agent.repeated_eval import summarise_runs


def test_summarise_runs_counts_each_scenario_across_runs():
    reports = [
        {
            "passed": True,
            "scenario_count": 2,
            "summary": {"failed_names": []},
            "scenarios": [
                {"name": "valid payment", "passed": True},
                {"name": "injection blocked", "passed": True},
            ],
        },
        {
            "passed": False,
            "scenario_count": 2,
            "summary": {"failed_names": ["injection blocked"]},
            "scenarios": [
                {"name": "valid payment", "passed": True},
                {"name": "injection blocked", "passed": False},
            ],
        },
    ]

    summary = summarise_runs(reports)

    assert summary["run_count"] == 2
    assert summary["scenario_names"] == 2
    assert summary["scenario_observations"] == 4
    assert summary["passed_observations"] == 3
    assert summary["failed_observations"] == 1
    assert summary["overall_pass_rate"] == 0.75
    assert summary["all_runs_passed"] is False
    assert summary["scenarios"] == [
        {
            "name": "injection blocked",
            "runs_observed": 2,
            "passed_runs": 1,
            "failed_runs": 1,
            "pass_rate": 0.5,
        },
        {
            "name": "valid payment",
            "runs_observed": 2,
            "passed_runs": 2,
            "failed_runs": 0,
            "pass_rate": 1.0,
        },
    ]


def test_summarise_runs_rejects_empty_input():
    with pytest.raises(ValueError, match="At least one"):
        summarise_runs([])



@pytest.mark.parametrize(
    ("reports", "message"),
    [
        (
            [
                {
                    "passed": True,
                    "scenario_count": 1,
                    "scenarios": [{"name": "valid", "passed": True}],
                },
                {
                    "passed": True,
                    "scenario_count": 0,
                    "scenarios": [],
                },
            ],
            "at least one scenario",
        ),
        (
            [
                {
                    "passed": True,
                    "scenario_count": 1,
                    "scenarios": [{"name": "valid", "passed": True}],
                },
                {
                    "passed": True,
                    "scenario_count": 1,
                    "scenarios": [{"name": "missing-case", "passed": True}],
                },
            ],
            "inventory differs",
        ),
        (
            [
                {
                    "passed": True,
                    "scenario_count": 2,
                    "scenarios": [
                        {"name": "valid", "passed": True},
                        {"name": "valid", "passed": False},
                    ],
                }
            ],
            "duplicate scenario",
        ),
        (
            [
                {
                    "passed": True,
                    "scenario_count": 1,
                    "scenarios": [{"name": "valid", "passed": "yes"}],
                }
            ],
            "boolean passed field",
        ),
        (
            [
                {
                    "passed": True,
                    "scenario_count": 2,
                    "scenarios": [{"name": "valid", "passed": True}],
                }
            ],
            "scenario_count does not match",
        ),
    ],
)
def test_summarise_runs_rejects_incomplete_or_inconsistent_evidence(reports, message):
    with pytest.raises(ValueError, match=message):
        summarise_runs(reports)
