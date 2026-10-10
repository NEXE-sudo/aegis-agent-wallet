import re

from app.policy.models import AgentPolicy, PolicyDecision, PolicyResult, TransactionProposal


def _normalise_address(address: str) -> str:
    return address.strip().lower()


def _is_valid_evm_address(address: str) -> bool:
    return re.fullmatch(r"0x[0-9a-fA-F]{40}", address.strip()) is not None


def evaluate_transaction(
    proposal: TransactionProposal,
    policy: AgentPolicy,
    daily_spent_base_units: int = 0,
) -> PolicyResult:
    """Evaluate a proposal against policy and trusted token metadata."""
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
    if not _is_valid_evm_address(proposal.recipient):
        hard_block = True
        reasons.append("Recipient must be a valid EVM address.")

    token_address = _normalise_address(proposal.token_address)
    trusted_tokens = {
        _normalise_address(address): metadata
        for address, metadata in policy.trusted_tokens.items()
    }
    token = trusted_tokens.get(token_address)
    if token is None:
        hard_block = True
        reasons.append("Token contract is not in the trusted token configuration.")
    if proposal.chain_id not in policy.allowed_chain_ids:
        hard_block = True
        reasons.append("Chain is not allowed by policy.")
    if proposal.token_symbol.upper() not in {s.upper() for s in policy.allowed_token_symbols}:
        hard_block = True
        reasons.append("Token symbol is not allowed by policy.")
    if token_address not in {_normalise_address(a) for a in policy.allowed_token_addresses}:
        hard_block = True
        reasons.append("Token contract address is not allow-listed.")

    if token is not None:
        if proposal.chain_id != token.chain_id:
            hard_block = True
            reasons.append("Chain ID does not match the trusted token configuration.")
        if proposal.token_symbol.casefold() != token.symbol.casefold():
            hard_block = True
            reasons.append("Token symbol does not match the trusted token configuration.")
        if proposal.token_decimals != token.decimals:
            hard_block = True
            reasons.append("Token decimals do not match the trusted token configuration.")

    recipient = _normalise_address(proposal.recipient)
    recipient_token_allowlist = {
        _normalise_address(allowed_recipient): {
            _normalise_address(address) for address in allowed_addresses
        }
        for allowed_recipient, allowed_addresses in policy.recipient_token_allowlist.items()
    }
    allowed_tokens_for_recipient = recipient_token_allowlist.get(recipient)
    if (
        allowed_tokens_for_recipient is not None
        and token_address not in allowed_tokens_for_recipient
    ):
        hard_block = True
        reasons.append("Token contract is not allowed for this recipient.")

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
