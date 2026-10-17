"""Canonical policy serialization and hashing for cross-system policy comparison."""
from __future__ import annotations

import hashlib
import json

from app.policy.models import AgentPolicy

POLICY_HASH_FORMAT_VERSION = "aegis-policy-v1"


def canonical_policy(policy: AgentPolicy) -> str:
    """Serialize policy v1 as compact sorted-key JSON with integer values as decimal strings.

    Address values and address-keyed mappings are lower-cased. Set-valued allowlists
    are sorted before serialization. The format includes every AgentPolicy field
    and trusted-token metadata; JSON uses UTF-8, ASCII escaping, sorted object keys,
    and no insignificant whitespace.
    """
    payload = {
        "agent_id": policy.agent_id,
        "allowed_chain_ids": sorted(str(value) for value in policy.allowed_chain_ids),
        "allowed_recipients": sorted(value.lower() for value in policy.allowed_recipients),
        "allowed_token_addresses": sorted(
            value.lower() for value in policy.allowed_token_addresses
        ),
        "allowed_token_symbols": sorted(policy.allowed_token_symbols),
        "approval_expires_seconds": str(policy.approval_expires_seconds),
        "approval_threshold_base_units": str(policy.approval_threshold_base_units),
        "daily_limit_base_units": str(policy.daily_limit_base_units),
        "enabled": policy.enabled,
        "max_transaction_base_units": str(policy.max_transaction_base_units),
        "recipient_daily_limits_base_units": {
            address.lower(): str(limit)
            for address, limit in sorted(
                policy.recipient_daily_limits_base_units.items(),
                key=lambda item: item[0].lower(),
            )
        },
        "recipient_token_allowlist": {
            recipient.lower(): sorted(address.lower() for address in addresses)
            for recipient, addresses in sorted(
                policy.recipient_token_allowlist.items(),
                key=lambda item: item[0].lower(),
            )
        },
        "recipient_transaction_limits_base_units": {
            address.lower(): str(limit)
            for address, limit in sorted(
                policy.recipient_transaction_limits_base_units.items(),
                key=lambda item: item[0].lower(),
            )
        },
        "trusted_tokens": {
            address.lower(): {
                "chain_id": str(metadata.chain_id),
                "decimals": str(metadata.decimals),
                "symbol": metadata.symbol,
            }
            for address, metadata in sorted(
                policy.trusted_tokens.items(),
                key=lambda item: item[0].lower(),
            )
        },
        "unknown_recipient_requires_approval": policy.unknown_recipient_requires_approval,
    }
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def hash_policy(policy: AgentPolicy) -> str:
    """Return the SHA-256 hex digest of the canonical policy v1 UTF-8 serialization."""
    return hashlib.sha256(canonical_policy(policy).encode("utf-8")).hexdigest()
