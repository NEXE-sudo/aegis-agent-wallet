from app.policy.models import AgentPolicy, TransactionProposal
from app.risk.models import RiskAssessment, RiskLevel


def assess_transaction_risk(
    proposal: TransactionProposal,
    policy: AgentPolicy,
    daily_spent_base_units: int = 0,
) -> RiskAssessment:
    """Return a transparent, deterministic risk score from 0 to 100.

    This is a heuristic, not a fraud detector. It never changes or overrides the
    separate policy engine's ALLOW / REQUIRE_APPROVAL / BLOCK decision.
    """
    score = 0
    reasons: list[str] = []

    allowed_recipients = {address.strip().lower() for address in policy.allowed_recipients}
    recipient = proposal.recipient.strip().lower()
    if recipient not in allowed_recipients:
        score += 35
        reasons.append("Recipient is not on the approved-recipient list (+35).")

    if proposal.amount_base_units > 0 and policy.max_transaction_base_units > 0:
        amount_ratio = proposal.amount_base_units / policy.max_transaction_base_units
        if amount_ratio >= 0.8:
            score += 25
            reasons.append("Amount is at least 80% of the per-transaction limit (+25).")
        elif amount_ratio >= 0.5:
            score += 10
            reasons.append("Amount is at least 50% of the per-transaction limit (+10).")

    if daily_spent_base_units >= 0 and policy.daily_limit_base_units > 0:
        projected_spend = daily_spent_base_units + max(proposal.amount_base_units, 0)
        daily_ratio = projected_spend / policy.daily_limit_base_units
        if daily_ratio >= 0.8:
            score += 25
            reasons.append("Projected daily spend is at least 80% of the daily limit (+25).")
        elif daily_ratio >= 0.5:
            score += 10
            reasons.append("Projected daily spend is at least 50% of the daily limit (+10).")

    if proposal.amount_base_units >= policy.approval_threshold_base_units:
        score += 15
        reasons.append("Amount meets or exceeds the approval threshold (+15).")

    score = min(score, 100)
    if score >= 50:
        level = RiskLevel.HIGH
    elif score >= 25:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW

    if not reasons:
        reasons.append("No configured risk signals were triggered.")

    return RiskAssessment(score=score, level=level, reasons=reasons)
