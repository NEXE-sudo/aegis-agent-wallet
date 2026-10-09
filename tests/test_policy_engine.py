import pytest

from app.chain.base_sepolia import BASE_SEPOLIA_USDC_ADDRESS
from app.policy.engine import evaluate_transaction
from app.policy.models import AgentPolicy, PolicyDecision, TransactionProposal, TrustedToken

TOKEN = BASE_SEPOLIA_USDC_ADDRESS
RECIPIENT_A = "0x2222222222222222222222222222222222222222"
UNKNOWN = "0x4444444444444444444444444444444444444444"


@pytest.fixture
def policy():
    return AgentPolicy(
        agent_id="devops-01",
        allowed_chain_ids={84532},
        allowed_token_addresses={TOKEN},
        allowed_token_symbols={"USDC"},
        trusted_tokens={TOKEN: TrustedToken(chain_id=84532, symbol="USDC", decimals=6)},
        max_transaction_base_units=50_000_000,
        daily_limit_base_units=150_000_000,
        approval_threshold_base_units=50_000_000,
        allowed_recipients={RECIPIENT_A},
        unknown_recipient_requires_approval=True,
    )


def proposal(**overrides):
    values = {
        "agent_id": "devops-01",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": TOKEN,
        "recipient": RECIPIENT_A,
        "amount_base_units": 20_000_000,
        "token_decimals": 6,
    }
    values.update(overrides)
    return TransactionProposal(**values)


def test_small_approved_payment_is_allowed(policy):
    result = evaluate_transaction(proposal(), policy)
    assert result.decision == PolicyDecision.ALLOW


@pytest.mark.parametrize(
    ("overrides", "daily_spent", "reason"),
    [
        ({"chain_id": 1}, 0, "Chain is not allowed"),
        ({"token_symbol": "DAI"}, 0, "Token symbol is not allowed"),
        ({"token_address": "0x9999999999999999999999999999999999999999"}, 0, "Token contract"),
        ({"amount_base_units": 50_000_001}, 0, "per-transaction limit"),
        ({"amount_base_units": 20_000_000}, 140_000_001, "daily spending limit"),
        ({"agent_id": "other-agent"}, 0, "Agent ID"),
        ({"amount_base_units": 0}, 0, "greater than zero"),
    ({"recipient": "not-an-address"}, 0, "valid EVM address"),
    ],
)
def test_hard_violations_are_blocked(policy, overrides, daily_spent, reason):
    result = evaluate_transaction(proposal(**overrides), policy, daily_spent)
    assert result.decision == PolicyDecision.BLOCK
    assert any(reason.lower() in item.lower() for item in result.reasons)


def test_unknown_recipient_requires_approval(policy):
    result = evaluate_transaction(proposal(recipient=UNKNOWN), policy)
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    assert any("Recipient is not" in item for item in result.reasons)


def test_threshold_amount_requires_approval(policy):
    result = evaluate_transaction(proposal(amount_base_units=50_000_000), policy)
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL


def test_hard_limit_cannot_be_overridden_by_approval(policy):
    result = evaluate_transaction(
        proposal(amount_base_units=50_000_001, recipient=UNKNOWN), policy
    )
    assert result.decision == PolicyDecision.BLOCK


def test_disabled_agent_is_blocked(policy):
    disabled = AgentPolicy(**{**policy.__dict__, "enabled": False})
    result = evaluate_transaction(proposal(), disabled)
    assert result.decision == PolicyDecision.BLOCK
    assert "Agent is disabled." in result.reasons


def test_negative_daily_spend_is_blocked(policy):
    result = evaluate_transaction(proposal(), policy, -1)
    assert result.decision == PolicyDecision.BLOCK


def test_trusted_token_rejects_tampered_decimals(policy):
    result = evaluate_transaction(proposal(token_decimals=18), policy)
    assert result.decision == PolicyDecision.BLOCK
    assert any("decimals do not match" in reason.lower() for reason in result.reasons)


def test_trusted_token_rejects_tampered_symbol(policy):
    result = evaluate_transaction(proposal(token_symbol="USDC.e"), policy)
    assert result.decision == PolicyDecision.BLOCK
    assert any("symbol does not match" in reason.lower() for reason in result.reasons)


def test_trusted_token_rejects_chain_mismatch(policy):
    result = evaluate_transaction(proposal(chain_id=1), policy)
    assert result.decision == PolicyDecision.BLOCK
    assert any("chain" in reason.lower() for reason in result.reasons)



def test_recipient_address_validation_allows_mixed_case_evm_address(policy):
    result = evaluate_transaction(
        proposal(recipient="0x222222222222222222222222222222222222222A"), policy
    )
    assert result.decision != PolicyDecision.BLOCK or not any(
        "valid EVM address" in reason for reason in result.reasons
    )


def test_recipient_address_validation_rejects_wrong_length(policy):
    result = evaluate_transaction(proposal(recipient="0x2222"), policy)
    assert result.decision == PolicyDecision.BLOCK
    assert any("valid EVM address" in reason for reason in result.reasons)
