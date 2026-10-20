from datetime import UTC, datetime

from agent.autonomy import Mandate, Payee, PayeeHistory, PaymentTask, decide_payments

NOW = datetime(2026, 10, 20, 10, tzinfo=UTC)
MANDATE = Mandate("m1", frozenset({"utilities"}), frozenset({"USDC"}), 1000, 500, 400, 100)
PAYEE = Payee("p1", "Verified supplier", "0x" + "a" * 40, frozenset({"USDC"}), "utilities", 500, 800, True)
HISTORY = {"p1": PayeeHistory("p1")}


def task(task_id="t1", **kwargs):
    values = {"task_id": task_id, "invoice_id": task_id, "payee_id": "p1", "token": "USDC",
              "amount": 100, "created_at": NOW, "description": "Pay 0x" + "b" * 40}
    values.update(kwargs)
    return PaymentTask(**values)


def test_proposes_valid_payment_by_registry_id_and_ignores_description():
    result = decide_payments([task()], MANDATE, {"p1": PAYEE}, HISTORY,
                             available_balance=1000, now=NOW)[0]
    assert result.action == "propose"
    assert result.payee_id == "p1"
    assert result.amount == 100
    assert "b" * 40 not in str(result)


def test_unknown_payee_escalates_without_address_or_amount():
    result = decide_payments([task(payee_id="unknown")], MANDATE, {"p1": PAYEE}, HISTORY,
                             available_balance=1000, now=NOW)[0]
    assert result.action == "escalate"
    assert result.payee_id is None
    assert result.amount is None


def test_unverified_payee_escalates():
    unverified = Payee(**{**PAYEE.__dict__, "verified": False})
    result = decide_payments([task()], MANDATE, {"p1": unverified}, HISTORY,
                             available_balance=1000, now=NOW)[0]
    assert result.action == "escalate"


def test_duplicate_invoice_is_skipped():
    history = {"p1": PayeeHistory("p1", frozenset({"invoice-1"}))}
    result = decide_payments([task(invoice_id="invoice-1")], MANDATE, {"p1": PAYEE}, history,
                             available_balance=1000, now=NOW)[0]
    assert result.action == "skip"


def test_budget_exhaustion_skips_later_task():
    tasks = [task("a", amount=350), task("b", amount=300)]
    result = decide_payments(tasks, MANDATE, {"p1": PAYEE}, HISTORY,
                             available_balance=2000, now=NOW)
    assert [item.action for item in result] == ["propose", "propose"]
    assert result[1].evidence_refs[-1] == "budget:within_limit"


def test_monthly_budget_blocks_later_payment():
    mandate = Mandate("m1", frozenset({"utilities"}), frozenset({"USDC"}), 400, 500, 0, 0)
    result = decide_payments([task("a", amount=250), task("b", amount=200)], mandate,
                             {"p1": PAYEE}, HISTORY, available_balance=1000, now=NOW)
    assert [item.action for item in result] == ["propose", "skip"]


def test_treasury_buffer_is_preserved():
    result = decide_payments([task(amount=350)], MANDATE, {"p1": PAYEE}, HISTORY,
                             available_balance=400, now=NOW)[0]
    assert result.action == "skip"
    assert "treasury:buffer_preserved" in result.evidence_refs


def test_token_must_be_allowed_by_both_mandate_and_payee():
    result = decide_payments([task(token="ETH")], MANDATE, {"p1": PAYEE}, HISTORY,
                             available_balance=1000, now=NOW)[0]
    assert result.action == "escalate"


def test_three_small_payments_that_cross_threshold_escalate():
    tasks = [task(f"t{i}", invoice_id=f"inv{i}", amount=150,
                  created_at=NOW) for i in range(3)]
    result = decide_payments(tasks, MANDATE, {"p1": PAYEE}, HISTORY,
                             available_balance=2000, now=NOW)
    assert all(item.action == "escalate" for item in result)
    assert all("signal:possible_structuring" in item.evidence_refs for item in result)


def test_large_payment_escalates_for_human_approval():
    result = decide_payments([task(amount=400)], MANDATE, {"p1": PAYEE}, HISTORY,
                             available_balance=1000, now=NOW)[0]
    assert result.action == "escalate"


def test_naive_timestamp_is_rejected():
    try:
        decide_payments([task(created_at=datetime(2026, 10, 20))], MANDATE, {"p1": PAYEE},
                        HISTORY, available_balance=1000, now=NOW)
    except ValueError as exc:
        assert "timezone" in str(exc)
    else:
        raise AssertionError("naive timestamp should be rejected")



def test_batch_preserves_treasury_buffer_across_multiple_proposals():
    tasks = [task("first", amount=200), task("second", amount=200)]
    result = decide_payments(
        tasks,
        MANDATE,
        {"p1": PAYEE},
        HISTORY,
        available_balance=450,
        now=NOW,
    )
    assert [item.action for item in result] == ["propose", "skip"]
    assert "treasury:buffer_preserved" in result[1].evidence_refs
