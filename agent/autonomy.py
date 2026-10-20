"""Deterministic, mandate-bounded payment selection.

Free-text task content is deliberately excluded from all payment parameters. The
agent selects a payee by registry ID; only owner-supplied structured records can
supply token and amount values. Decisions are proposals, never approvals.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

Action = Literal["propose", "skip", "escalate"]


@dataclass(frozen=True)
class Payee:
    payee_id: str
    name: str
    address: str
    allowed_tokens: frozenset[str]
    category: str
    per_tx_limit: int
    daily_limit: int
    verified: bool = False


@dataclass(frozen=True)
class Mandate:
    mandate_id: str
    allowed_categories: frozenset[str]
    allowed_tokens: frozenset[str]
    monthly_budget: int
    per_payment_limit: int
    approval_threshold: int
    treasury_buffer: int = 0


@dataclass(frozen=True)
class PaymentTask:
    task_id: str
    invoice_id: str
    payee_id: str
    token: str
    amount: int
    created_at: datetime
    due_at: datetime | None = None
    description: str = ""  # Untrusted display text; never parsed for payment fields.


@dataclass(frozen=True)
class PayeeHistory:
    payee_id: str
    invoice_ids: frozenset[str] = frozenset()
    paid_today: int = 0
    paid_this_month: int = 0
    recent_amounts: tuple[int, ...] = ()


@dataclass(frozen=True)
class Decision:
    action: Action
    task_id: str
    payee_id: str | None
    amount: int | None
    rationale: str
    confidence: float
    evidence_refs: tuple[str, ...]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Task timestamps must include a timezone")
    return value.astimezone(UTC)


def decide_payments(
    tasks: list[PaymentTask],
    mandate: Mandate,
    payees: dict[str, Payee],
    history: dict[str, PayeeHistory],
    *,
    available_balance: int,
    reserved_monthly_spend: int = 0,
    now: datetime | None = None,
) -> list[Decision]:
    """Rank and decide on tasks under a fixed mandate and verified registry.

    The function has no signing, approval, policy mutation, or execution ability.
    """
    current = _utc(now or datetime.now(UTC))
    if available_balance < 0 or reserved_monthly_spend < 0:
        raise ValueError("Balances and reservations cannot be negative")
    if mandate.monthly_budget <= 0 or mandate.per_payment_limit <= 0:
        raise ValueError("Mandate budget and payment limit must be positive")
    if mandate.approval_threshold < 0 or mandate.treasury_buffer < 0:
        raise ValueError("Approval threshold and treasury buffer cannot be negative")

    ordered = sorted(
        tasks,
        key=lambda task: (
            _utc(task.due_at) if task.due_at else datetime.max.replace(tzinfo=UTC),
            _utc(task.created_at),
            task.task_id,
        ),
    )
    seen_invoice_ids: set[tuple[str, str]] = set()
    projected_monthly = reserved_monthly_spend
    projected_daily: dict[str, int] = {}
    decisions: list[Decision] = []

    # Detect likely threshold evasion before selecting individual tasks.
    by_payee: dict[str, list[PaymentTask]] = {}
    for task in ordered:
        if isinstance(task.payee_id, str):
            by_payee.setdefault(task.payee_id, []).append(task)
    structured_ids: set[str] = set()
    for payee_id, grouped in by_payee.items():
        if payee_id not in payees or mandate.approval_threshold <= 0:
            continue
        windowed = [
            task for task in grouped
            if type(task.amount) is int
            and 0 < task.amount < mandate.approval_threshold
            and current - timedelta(hours=24) <= _utc(task.created_at) <= current
        ]
        if len(windowed) >= 3 and sum(task.amount for task in windowed) >= mandate.approval_threshold:
            structured_ids.update(task.task_id for task in windowed)

    for task in ordered:
        evidence = [f"task:{task.task_id}"]
        payee = payees.get(task.payee_id)
        past = history.get(task.payee_id, PayeeHistory(task.payee_id))
        if task.task_id in structured_ids:
            decisions.append(Decision(
                "escalate", task.task_id, task.payee_id, None,
                "Multiple small payments to the same payee within 24 hours may evade approval thresholds.",
                0.99, tuple(evidence + ["signal:possible_structuring"]),
            ))
            continue
        if payee is None:
            decisions.append(Decision(
                "escalate", task.task_id, None, None,
                "Payee ID is not in the owner-managed registry; request owner verification.",
                0.99, tuple(evidence + ["registry:unknown_payee"]),
            ))
            continue
        evidence.append(f"payee:{payee.payee_id}")
        if not payee.verified:
            decisions.append(Decision(
                "escalate", task.task_id, payee.payee_id, None,
                "Payee has not been verified by the owner.", 0.99,
                tuple(evidence + ["registry:unverified"]),
            ))
            continue
        if (
            not task.invoice_id.strip()
            or task.invoice_id in past.invoice_ids
            or (payee.payee_id, task.invoice_id) in seen_invoice_ids
        ):
            decisions.append(Decision(
                "skip", task.task_id, payee.payee_id, None,
                "Invoice is missing an ID or appears to be a duplicate.", 0.99,
                tuple(evidence + ["history:duplicate_invoice"]),
            ))
            continue
        seen_invoice_ids.add((payee.payee_id, task.invoice_id))
        if type(task.amount) is not int or task.amount <= 0:
            decisions.append(Decision(
                "escalate", task.task_id, payee.payee_id, None,
                "Amount must be a positive integer in token base units.", 0.99,
                tuple(evidence + ["validation:invalid_amount"]),
            ))
            continue
        if task.token not in mandate.allowed_tokens or task.token not in payee.allowed_tokens:
            decisions.append(Decision(
                "escalate", task.task_id, payee.payee_id, None,
                "Token is not allowed by both the mandate and the payee registry.",
                0.99, tuple(evidence + ["policy:token_not_allowed"]),
            ))
            continue
        if payee.category not in mandate.allowed_categories:
            decisions.append(Decision(
                "escalate", task.task_id, payee.payee_id, None,
                "Payee category is outside the active mandate.", 0.98,
                tuple(evidence + ["policy:category_not_allowed"]),
            ))
            continue
        if task.amount > min(payee.per_tx_limit, mandate.per_payment_limit):
            decisions.append(Decision(
                "escalate", task.task_id, payee.payee_id, None,
                "Amount exceeds the payee or mandate per-payment limit.", 0.99,
                tuple(evidence + ["policy:per_payment_limit"]),
            ))
            continue
        if task.amount >= mandate.approval_threshold > 0:
            decisions.append(Decision(
                "escalate", task.task_id, payee.payee_id, None,
                "Amount reaches the human-approval threshold.", 0.98,
                tuple(evidence + ["policy:approval_threshold"]),
            ))
            continue
        if past.paid_today + projected_daily.get(payee.payee_id, 0) + task.amount > payee.daily_limit:
            decisions.append(Decision(
                "escalate", task.task_id, payee.payee_id, None,
                "Payment would exceed the payee daily limit.", 0.99,
                tuple(evidence + ["policy:payee_daily_limit"]),
            ))
            continue
        if projected_monthly + task.amount > mandate.monthly_budget:
            decisions.append(Decision(
                "skip", task.task_id, payee.payee_id, None,
                "Mandate monthly budget is exhausted.", 0.99,
                tuple(evidence + ["budget:monthly_exhausted"]),
            ))
            continue
        if available_balance - task.amount < mandate.treasury_buffer:
            decisions.append(Decision(
                "skip", task.task_id, payee.payee_id, None,
                "Payment would breach the required treasury buffer.", 0.98,
                tuple(evidence + ["treasury:buffer_preserved"]),
            ))
            continue
        if task.amount in past.recent_amounts and len(past.recent_amounts) >= 3:
            decisions.append(Decision(
                "escalate", task.task_id, payee.payee_id, None,
                "Repeated amount pattern differs from ordinary invoice variation; review required.",
                0.75, tuple(evidence + ["risk:repeated_amount_pattern"]),
            ))
            continue

        projected_monthly += task.amount
        projected_daily[payee.payee_id] = projected_daily.get(payee.payee_id, 0) + task.amount
        decisions.append(Decision(
            "propose", task.task_id, payee.payee_id, task.amount,
            "Verified payee and token match the mandate; limits, budget, duplicate and treasury checks passed.",
            0.9, tuple(evidence + ["policy:mandate_match", "budget:within_limit"]),
        ))
    return decisions
