from dataclasses import dataclass, field
from enum import StrEnum


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"


@dataclass(frozen=True)
class TrustedToken:
    chain_id: int
    symbol: str
    decimals: int

    def __post_init__(self) -> None:
        if self.chain_id <= 0:
            raise ValueError("trusted token chain_id must be positive")
        if not self.symbol.strip():
            raise ValueError("trusted token symbol cannot be empty")
        if not 0 <= self.decimals <= 36:
            raise ValueError("trusted token decimals must be between 0 and 36")


@dataclass(frozen=True)
class AgentPolicy:
    agent_id: str
    allowed_chain_ids: set[int]
    allowed_token_addresses: set[str]
    allowed_token_symbols: set[str]
    max_transaction_base_units: int
    daily_limit_base_units: int
    approval_threshold_base_units: int
    allowed_recipients: set[str] = field(default_factory=set)
    unknown_recipient_requires_approval: bool = True
    enabled: bool = True
    trusted_tokens: dict[str, TrustedToken] = field(default_factory=dict)
    approval_expires_seconds: int = 300
    recipient_token_allowlist: dict[str, set[str]] = field(default_factory=dict)
    recipient_daily_limits_base_units: dict[str, int] = field(default_factory=dict)
    recipient_transaction_limits_base_units: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.agent_id.strip():
            raise ValueError("agent_id cannot be empty")
        if self.max_transaction_base_units <= 0:
            raise ValueError("max_transaction_base_units must be positive")
        if self.daily_limit_base_units <= 0:
            raise ValueError("daily_limit_base_units must be positive")
        if self.approval_threshold_base_units < 0:
            raise ValueError("approval_threshold_base_units cannot be negative")
        if not self.allowed_chain_ids or any(chain_id <= 0 for chain_id in self.allowed_chain_ids):
            raise ValueError("allowed_chain_ids must contain positive chain IDs")
        if any(not symbol.strip() for symbol in self.allowed_token_symbols):
            raise ValueError("allowed token symbols cannot be empty")
        if self.approval_expires_seconds <= 0:
            raise ValueError("approval_expires_seconds must be positive")
        if any(limit < 0 for limit in self.recipient_daily_limits_base_units.values()):
            raise ValueError("recipient daily limits cannot be negative")
        if any(limit < 0 for limit in self.recipient_transaction_limits_base_units.values()):
            raise ValueError("recipient transaction limits cannot be negative")


@dataclass(frozen=True)
class TransactionProposal:
    agent_id: str
    chain_id: int
    token_symbol: str
    token_address: str
    recipient: str
    amount_base_units: int
    token_decimals: int


@dataclass(frozen=True)
class PolicyResult:
    decision: PolicyDecision
    reasons: list[str]
