"""Coverage and invariants for the adversarial evaluation catalogue."""
from agent.scenarios import llm_injection_scenarios, scenarios


def test_deterministic_catalogue_has_unique_names_and_expected_outcomes():
    cases = scenarios()
    names = [case["name"] for case in cases]

    assert len(cases) == 18
    assert len(names) == len(set(names))
    assert {case["expected"] for case in cases} >= {
        "ready",
        "blocked",
        "awaiting_approval",
    }


def test_deterministic_injection_cases_keep_structured_recipient_authoritative():
    cases = [case for case in scenarios() if "expected_recipient" in case]

    assert len(cases) == 5
    for case in cases:
        assert case["expected"] == "ready"
        assert case["task"]["authorized_payment"]["recipient"] == case["expected_recipient"]
        assert case["must_not_propose_recipient"].lower() != case["expected_recipient"].lower()
        assert case["task"]["invoice_text"]


def test_llm_catalogue_covers_distinct_attack_families_with_stable_expected_payment():
    cases = llm_injection_scenarios()
    names = [case["name"] for case in cases]

    assert len(cases) == 10
    assert len(names) == len(set(names))
    for case in cases:
        assert case["expected_proposal"] == {
            "recipient": "0x2222222222222222222222222222222222222222",
            "amount_base_units": 1_000_000,
        }
        assert case["task"]["authorized_payment"]["recipient"] == (
            case["expected_proposal"]["recipient"]
        )
