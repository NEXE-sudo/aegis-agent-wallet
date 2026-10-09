from app.policy.models import AgentPolicy, PolicyDecision, PolicyResult, TransactionProposal


def _normalise_address(address: str) -> str:
    return address.strip().lower()


def evaluate_transaction(
    proposal: TransactionProposal,
    policy: AgentPolicy,
    daily_spent_base_units: int = 0,
) -> PolicyResult:
    """Deterministically evaluate a proposal using integer token base units."""
    reasons: list[str] = []
    hard_block = False
    needs_approval = False

    if not policy.enabled:
        return PolicyResult(PolicyDecision.BLOCK, ["Agent is disabled."])

    if proposal.agent_id != policy.agent_id:
        hard_block = True
        reasons.append("Agent ID does not match this policy.")
    if proposal.amount_base_units <= 0:
        hard_block = True
        reasons.append("Transaction amount must be greater than zero.")
    if not 0 <= proposal.token_decimals <= 36:
        hard_block = True
        reasons.append("Token decimals are outside the supported range.")
    if proposal.chain_id not in policy.allowed_chain_ids:
        hard_block = True
        reasons.append("Chain is not allowed by policy.")
    if proposal.token_symbol.upper() not in {s.upper() for s in policy.allowed_token_symbols}:
        hard_block = True
        reasons.append("Token symbol is not allowed by policy.")
    if _normalise_address(proposal.token_address) not in {
        _normalise_address(a) for a in policy.allowed_token_addresses
    }:
        hard_block = True
        reasons.append("Token contract address is not allow-listed.")
    if proposal.amount_base_units > policy.max_transaction_base_units:
        hard_block = True
        reasons.append("Transaction exceeds the per-transaction limit.")
    if daily_spent_base_units < 0:
        hard_block = True
        reasons.append("Recorded daily spend cannot be negative.")
    elif daily_spent_base_units + proposal.amount_base_units > policy.daily_limit_base_units:
        hard_block = True
        reasons.append("Transaction would exceed the daily spending limit.")

    if hard_block:
        return PolicyResult(PolicyDecision.BLOCK, reasons)

    approved = {_normalise_address(a) for a in policy.allowed_recipients}
    if (
        _normalise_address(proposal.recipient) not in approved
        and policy.unknown_recipient_requires_approval
    ):
        needs_approval = True
        reasons.append("Recipient is not on the approved-recipient list.")
    if proposal.amount_base_units >= policy.approval_threshold_base_units:
        needs_approval = True
        reasons.append("Transaction meets or exceeds the approval threshold.")

    if needs_approval:
        return PolicyResult(PolicyDecision.REQUIRE_APPROVAL, reasons)
    return PolicyResult(PolicyDecision.ALLOW, ["All configured policy checks passed."])
