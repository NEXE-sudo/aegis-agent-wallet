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

    def __post_init__(self) -> None:
        if self.approval_expires_seconds <= 0:
            raise ValueError("approval_expires_seconds must be positive")


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
