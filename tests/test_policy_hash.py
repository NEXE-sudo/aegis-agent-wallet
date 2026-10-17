import hashlib
import json
import secrets
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.chain.base_sepolia import BASE_SEPOLIA_USDC_ADDRESS
from app.policy.canonical import (
    POLICY_HASH_FORMAT_VERSION,
    canonical_policy,
    hash_policy,
)
from app.policy.models import AgentPolicy, TrustedToken
from app.workflow.controller import TransactionController
from app.workflow.store import WorkflowStore

TOKEN_A = BASE_SEPOLIA_USDC_ADDRESS
TOKEN_B = "0x1111111111111111111111111111111111111111"
TOKEN_C = "0x2222222222222222222222222222222222222222"
RECIPIENT_A = "0x3333333333333333333333333333333333333333"
RECIPIENT_B = "0x4444444444444444444444444444444444444444"


def _policy(
    *,
    token_addresses=None,
    token_symbols=None,
    recipients=None,
    recipient_token_allowlist=None,
    trusted_tokens=None,
    recipient_daily_limits=None,
    recipient_transaction_limits=None,
    max_transaction=50_000_000,
    daily_limit=150_000_000,
    approval_threshold=50_000_000,
):
    return AgentPolicy(
        agent_id="devops-01",
        allowed_chain_ids={84532},
        allowed_token_addresses=set(token_addresses or {TOKEN_A, TOKEN_B}),
        allowed_token_symbols=set(token_symbols or {"USDC", "TEST"}),
        trusted_tokens=trusted_tokens
        or {
            TOKEN_A: TrustedToken(chain_id=84532, symbol="USDC", decimals=6),
            TOKEN_B: TrustedToken(chain_id=84532, symbol="TEST", decimals=18),
        },
        max_transaction_base_units=max_transaction,
        daily_limit_base_units=daily_limit,
        approval_threshold_base_units=approval_threshold,
        allowed_recipients=set(recipients or {RECIPIENT_A, RECIPIENT_B}),
        unknown_recipient_requires_approval=True,
        enabled=True,
        approval_expires_seconds=300,
        recipient_token_allowlist=recipient_token_allowlist
        or {
            RECIPIENT_A: {TOKEN_A},
            RECIPIENT_B: {TOKEN_B},
        },
        recipient_daily_limits_base_units=recipient_daily_limits
        or {RECIPIENT_A: 90_000_000},
        recipient_transaction_limits_base_units=recipient_transaction_limits
        or {RECIPIENT_B: 40_000_000},
    )


def _reference_serialization(policy: AgentPolicy) -> str:
    """Independent policy-v1 implementation used to cross-check production output."""
    payload = {
        "agent_id": policy.agent_id,
        "allowed_chain_ids": sorted(str(chain_id) for chain_id in policy.allowed_chain_ids),
        "allowed_recipients": sorted(address.lower() for address in policy.allowed_recipients),
        "allowed_token_addresses": sorted(
            address.lower() for address in policy.allowed_token_addresses
        ),
        "allowed_token_symbols": sorted(policy.allowed_token_symbols),
        "approval_expires_seconds": str(policy.approval_expires_seconds),
        "approval_threshold_base_units": str(policy.approval_threshold_base_units),
        "daily_limit_base_units": str(policy.daily_limit_base_units),
        "enabled": policy.enabled,
        "max_transaction_base_units": str(policy.max_transaction_base_units),
        "recipient_daily_limits_base_units": {
            address.lower(): str(value)
            for address, value in sorted(
                policy.recipient_daily_limits_base_units.items(),
                key=lambda item: item[0].lower(),
            )
        },
        "recipient_token_allowlist": {
            recipient.lower(): sorted(token.lower() for token in tokens)
            for recipient, tokens in sorted(
                policy.recipient_token_allowlist.items(),
                key=lambda item: item[0].lower(),
            )
        },
        "recipient_transaction_limits_base_units": {
            address.lower(): str(value)
            for address, value in sorted(
                policy.recipient_transaction_limits_base_units.items(),
                key=lambda item: item[0].lower(),
            )
        },
        "trusted_tokens": {
            address.lower(): {
                "chain_id": str(token.chain_id),
                "decimals": str(token.decimals),
                "symbol": token.symbol,
            }
            for address, token in sorted(
                policy.trusted_tokens.items(),
                key=lambda item: item[0].lower(),
            )
        },
        "unknown_recipient_requires_approval": policy.unknown_recipient_requires_approval,
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def test_hash_is_stable_across_object_and_allowlist_order_and_matches_reference():
    policy = _policy()
    reordered = replace(
        policy,
        trusted_tokens=dict(reversed(tuple(policy.trusted_tokens.items()))),
        recipient_token_allowlist={
            recipient: tokens
            for recipient, tokens in reversed(
                tuple(policy.recipient_token_allowlist.items())
            )
        },
        recipient_daily_limits_base_units=dict(
            reversed(tuple(policy.recipient_daily_limits_base_units.items()))
        ),
        recipient_transaction_limits_base_units=dict(
            reversed(tuple(policy.recipient_transaction_limits_base_units.items()))
        ),
    )

    assert hash_policy(policy) == hash_policy(reordered)
    assert canonical_policy(policy) == _reference_serialization(policy)
    assert hash_policy(policy) == hashlib.sha256(
        _reference_serialization(policy).encode("utf-8")
    ).hexdigest()
    assert '"daily_limit_base_units":"150000000"' in canonical_policy(policy)


def test_hash_normalizes_addresses_to_lowercase():
    policy = _policy()
    uppercased = replace(
        policy,
        allowed_token_addresses={address.upper() for address in policy.allowed_token_addresses},
        allowed_recipients={address.upper() for address in policy.allowed_recipients},
        trusted_tokens={
            address.upper(): token for address, token in policy.trusted_tokens.items()
        },
        recipient_token_allowlist={
            recipient.upper(): {address.upper() for address in tokens}
            for recipient, tokens in policy.recipient_token_allowlist.items()
        },
        recipient_daily_limits_base_units={
            address.upper(): limit
            for address, limit in policy.recipient_daily_limits_base_units.items()
        },
        recipient_transaction_limits_base_units={
            address.upper(): limit
            for address, limit in policy.recipient_transaction_limits_base_units.items()
        },
    )

    assert hash_policy(policy) == hash_policy(uppercased)


@pytest.mark.parametrize(
    "changed_policy",
    [
        _policy(max_transaction=50_000_001),
        _policy(daily_limit=150_000_001),
        _policy(approval_threshold=50_000_001),
        _policy(recipient_daily_limits={RECIPIENT_A: 90_000_001}),
        _policy(recipient_transaction_limits={RECIPIENT_B: 40_000_001}),
        _policy(token_addresses={TOKEN_A, TOKEN_B, TOKEN_C}),
        _policy(token_symbols={"USDC", "TEST", "THIRD"}),
        _policy(recipients={RECIPIENT_A, RECIPIENT_B, "0x5555555555555555555555555555555555555555"}),
        _policy(
            recipient_token_allowlist={
                RECIPIENT_A: {TOKEN_A, TOKEN_B},
                RECIPIENT_B: {TOKEN_B},
            }
        ),
        _policy(
            trusted_tokens={
                TOKEN_A: TrustedToken(chain_id=84532, symbol="USDC", decimals=6),
                TOKEN_B: TrustedToken(chain_id=84532, symbol="TEST", decimals=18),
                TOKEN_C: TrustedToken(chain_id=84532, symbol="THIRD", decimals=6),
            }
        ),
    ],
)
def test_hash_changes_when_a_policy_limit_or_allowlist_entry_changes(changed_policy):
    assert hash_policy(changed_policy) != hash_policy(_policy())


@pytest.fixture
def isolated_policy_app(tmp_path, monkeypatch):
    agent_token = "agent-" + secrets.token_urlsafe(24)
    owner_token = "owner-" + secrets.token_urlsafe(24)
    monkeypatch.setenv("AEGIS_AGENT_TOKEN", agent_token)
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", owner_token)
    store = WorkflowStore(tmp_path / "policy-hash.sqlite3")
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(
        main, "controller", TransactionController(main.DEMO_POLICY, store)
    )
    return TestClient(main.app), store, {
        "Authorization": f"Bearer {agent_token}"
    }, {"Authorization": f"Bearer {owner_token}"}


def test_policy_hash_endpoint_is_owner_only(isolated_policy_app):
    client, _, agent_headers, owner_headers = isolated_policy_app

    assert client.get("/policy/hash").status_code == 401
    assert client.get("/policy/hash", headers=agent_headers).status_code == 401

    response = client.get("/policy/hash", headers=owner_headers)

    assert response.status_code == 200
    assert response.json() == {
        "policy_hash": hash_policy(main.DEMO_POLICY),
        "format_version": POLICY_HASH_FORMAT_VERSION,
    }


def test_proposal_and_execution_audit_details_include_active_policy_hash(
    isolated_policy_app,
):
    client, store, agent_headers, owner_headers = isolated_policy_app
    proposal_response = client.post(
        "/transactions/propose",
        headers=agent_headers,
        json={
            "agent_id": "devops-01",
            "chain_id": 84532,
            "token_symbol": "USDC",
            "token_address": TOKEN_A,
            "recipient": "0x2222222222222222222222222222222222222222",
            "amount_base_units": 1_000_000,
            "token_decimals": 6,
        },
    )
    transaction_id = proposal_response.json()["transaction_id"]
    policy_digest = hash_policy(main.DEMO_POLICY)

    proposal_event = store.audit_events(transaction_id)[0]
    assert proposal_event["event_type"] == "proposal_created"
    assert proposal_event["details"]["policy_hash"] == policy_digest

    execution_response = client.post(
        f"/transactions/{transaction_id}/execute",
        headers=owner_headers,
    )

    assert execution_response.status_code == 200
    execution_event = store.audit_events(transaction_id)[-1]
    assert execution_event["event_type"] == "simulated_execution"
    assert execution_event["details"]["policy_hash"] == policy_digest
