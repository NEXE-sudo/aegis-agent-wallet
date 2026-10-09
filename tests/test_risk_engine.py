import pytest

from app.policy.models import AgentPolicy, TransactionProposal
from app.risk.engine import assess_transaction_risk
from app.risk.models import RiskLevel


@pytest.fixture
def policy() -> AgentPolicy:
    return AgentPolicy(
        agent_id="agent-1",
        allowed_chain_ids={84532},
        allowed_token_addresses={"0x" + "1" * 40},
        allowed_token_symbols={"USDC"},
        max_transaction_base_units=100,
        daily_limit_base_units=300,
        approval_threshold_base_units=80,
        allowed_recipients={"0x" + "2" * 40},
    )


def proposal(amount: int = 10, recipient: str = "0x" + "2" * 40) -> TransactionProposal:
    return TransactionProposal(
        agent_id="agent-1",
        chain_id=84532,
        token_symbol="USDC",
        token_address="0x" + "1" * 40,
        recipient=recipient,
        amount_base_units=amount,
        token_decimals=6,
    )


def test_small_transaction_to_approved_recipient_is_low_risk(policy: AgentPolicy) -> None:
    result = assess_transaction_risk(proposal(), policy, daily_spent_base_units=0)
    assert result.score == 0
    assert result.level == RiskLevel.LOW
    assert result.reasons == ["No configured risk signals were triggered."]


def test_unknown_recipient_is_medium_risk(policy: AgentPolicy) -> None:
    result = assess_transaction_risk(
        proposal(recipient="0x" + "3" * 40), policy, daily_spent_base_units=0
    )
    assert result.score == 35
    assert result.level == RiskLevel.MEDIUM
    assert any("Recipient" in reason for reason in result.reasons)


def test_large_transaction_and_unknown_recipient_are_high_risk(policy: AgentPolicy) -> None:
    result = assess_transaction_risk(
        proposal(amount=90, recipient="0x" + "3" * 40),
        policy,
        daily_spent_base_units=180,
    )
    # 35 unknown recipient + 25 large amount + 25 projected daily spend + 15 threshold.
    assert result.score == 100
    assert result.level == RiskLevel.HIGH


def test_score_is_capped_at_100(policy: AgentPolicy) -> None:
    result = assess_transaction_risk(
        proposal(amount=100, recipient="0x" + "3" * 40),
        policy,
        daily_spent_base_units=300,
    )
    assert result.score == 100
