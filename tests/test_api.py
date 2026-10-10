import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.workflow.store as store_module
from app import main
from app.chain.base_sepolia import BASE_SEPOLIA_USDC_ADDRESS
from app.policy.models import TransactionProposal
from app.workflow.controller import TransactionController, fingerprint_proposal
from app.workflow.store import WorkflowStore

AGENT_TOKEN = "test-agent-token-with-enough-length"
OWNER_TOKEN = "test-owner-token-with-enough-length"
AGENT_HEADERS = {"Authorization": f"Bearer {AGENT_TOKEN}"}
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def isolated_workflow(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_AGENT_TOKEN", AGENT_TOKEN)
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_TOKEN)
    local_store = WorkflowStore(tmp_path / "workflow.sqlite3")
    monkeypatch.setattr(main, "store", local_store)
    monkeypatch.setattr(main, "controller", TransactionController(main.DEMO_POLICY, local_store))


def _payload(**overrides):
    payload = {
        "agent_id": "devops-01",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": BASE_SEPOLIA_USDC_ADDRESS,
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 1_000_000,
        "token_decimals": 6,
    }
    payload.update(overrides)
    return payload


def propose(payload=None, **overrides):
    return client.post("/transactions/propose", headers=AGENT_HEADERS, json=payload or _payload(**overrides))


def owner_post(path, **kwargs):
    return client.post(path, headers=OWNER_HEADERS, **kwargs)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_demo_policy_uses_circle_base_sepolia_usdc():
    assert main.DEMO_POLICY.allowed_token_addresses == {BASE_SEPOLIA_USDC_ADDRESS}
    assert _payload()["token_address"] == BASE_SEPOLIA_USDC_ADDRESS
    assert _payload()["token_decimals"] == 6


def test_policy_endpoint_blocks_wrong_chain():
    response = client.post("/policy/evaluate", json={**_payload(), "chain_id": 1, "daily_spent_base_units": 0})
    assert response.status_code == 200
    assert response.json()["decision"] == "block"


def test_unknown_agent_returns_404():
    response = client.post("/policy/evaluate", json={**_payload(), "agent_id": "not-configured"})
    assert response.status_code == 404


def test_risk_endpoint_returns_score_and_reasons():
    response = client.post("/risk/assess", json={
        **_payload(recipient="0x4444444444444444444444444444444444444444", amount_base_units=45_000_000),
        "daily_spent_base_units": 80_000_000,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["score"] == 85
    assert body["level"] == "high"
    assert body["reasons"]


def test_risk_endpoint_rejects_unknown_agent():
    response = client.post("/risk/assess", json={**_payload(), "agent_id": "unknown-agent"})
    assert response.status_code == 404


def test_workflow_requires_distinct_authentication():
    assert client.post("/transactions/propose", json=_payload()).status_code == 401
    assert client.post("/transactions/propose", headers=OWNER_HEADERS, json=_payload()).status_code == 401
    record = propose().json()
    assert client.get(f"/transactions/{record['transaction_id']}").status_code == 401
    assert owner_post(f"/transactions/{record['transaction_id']}/execute").status_code == 200


def test_low_risk_transaction_can_be_simulated_once():
    proposed = propose()
    assert proposed.status_code == 201
    record = proposed.json()
    assert record["status"] == "ready"
    executed = owner_post(f"/transactions/{record['transaction_id']}/execute")
    assert executed.status_code == 200
    assert executed.json()["status"] == "executed_simulated"
    assert executed.json()["execution_reference"].startswith("simulated:")
    assert owner_post(f"/transactions/{record['transaction_id']}/execute").status_code == 409


def test_unknown_recipient_requires_fingerprint_bound_approval():
    record = propose(_payload(recipient="0x4444444444444444444444444444444444444444")).json()
    assert record["status"] == "awaiting_approval"
    wrong = owner_post(f"/transactions/{record['transaction_id']}/approve", json={
        "transaction_fingerprint": "0" * 64, "confirmation": "APPROVE",
    })
    assert wrong.status_code == 409
    approved = owner_post(f"/transactions/{record['transaction_id']}/approve", json={
        "transaction_fingerprint": record["fingerprint"], "confirmation": "APPROVE",
    })
    assert approved.status_code == 200
    assert approved.json()["approval_fingerprint"] == record["fingerprint"]
    executed = owner_post(f"/transactions/{record['transaction_id']}/execute")
    assert executed.status_code == 200
    assert executed.json()["status"] == "executed_simulated"


def test_blocked_transaction_cannot_be_approved_or_executed():
    record = propose(_payload(chain_id=1)).json()
    assert record["status"] == "blocked"
    approve = owner_post(f"/transactions/{record['transaction_id']}/approve", json={
        "transaction_fingerprint": record["fingerprint"], "confirmation": "APPROVE",
    })
    assert approve.status_code == 409
    assert owner_post(f"/transactions/{record['transaction_id']}/execute").status_code == 403


def test_transaction_workflow_ignores_caller_supplied_daily_spend():
    # ProposalRequest does not accept caller-supplied accounting data. Persisted spend is authoritative.
    body = {**_payload(amount_base_units=40_000_000), "daily_spent_base_units": 149_000_000}
    record = propose(body).json()
    assert record["status"] == "ready"  # caller's fabricated spend is ignored


def test_daily_spend_reservations_block_overspend():
    first = propose(_payload(amount_base_units=45_000_000)).json()
    assert first["status"] != "blocked"
    second = propose(_payload(amount_base_units=45_000_000)).json()
    assert second["status"] != "blocked"
    third = propose(_payload(amount_base_units=45_000_000)).json()
    assert third["status"] != "blocked"
    fourth = propose(_payload(amount_base_units=20_000_000)).json()
    assert fourth["status"] == "blocked"
    assert "daily spending limit" in " ".join(fourth["policy_reasons"]).lower()


def test_idempotency_replay_returns_same_transaction_and_conflict_for_changed_payload():
    headers = {**AGENT_HEADERS, "Idempotency-Key": "retry-key-0001"}
    first = client.post("/transactions/propose", headers=headers, json=_payload())
    second = client.post("/transactions/propose", headers=headers, json=_payload())
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["transaction_id"] == second.json()["transaction_id"]
    conflict = client.post("/transactions/propose", headers=headers, json=_payload(amount_base_units=2_000_000))
    assert conflict.status_code == 409


def test_execution_rechecks_spending_and_blocks_stale_approval():
    # First reserve most of the daily budget; second proposal is initially evaluated against it.
    first = propose(_payload(amount_base_units=45_000_000)).json()
    assert first["status"] in {"ready", "awaiting_approval"}
    second = propose(_payload(amount_base_units=45_000_000)).json()
    assert second["status"] in {"ready", "awaiting_approval"}
    # Simulate an externally inserted reservation to test execution-time revalidation.
    proposal = TransactionProposal(**_payload(amount_base_units=65_000_000))
    main.store.create({
        "transaction_id": "external-reservation", "fingerprint": fingerprint_proposal(proposal),
        "proposal": _payload(amount_base_units=65_000_000), "policy_decision": "allow",
        "policy_reasons": ["test reservation"], "risk_score": 0, "risk_level": "low",
        "risk_reasons": [], "status": "ready",
    })
    result = owner_post(f"/transactions/{second['transaction_id']}/execute")
    assert result.status_code == 403
    assert result.json()["detail"] == "Policy-blocked transactions cannot execute"


def test_missing_auth_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("AEGIS_AGENT_TOKEN", raising=False)
    response = client.post("/transactions/propose", json=_payload())
    assert response.status_code == 503


def test_transaction_state_survives_store_reopen(tmp_path):
    db_path = tmp_path / "workflow.sqlite3"
    local_store = WorkflowStore(db_path)
    record = {
        "transaction_id": "test-id", "fingerprint": "a" * 64, "proposal": _payload(),
        "policy_decision": "allow", "policy_reasons": ["ok"], "risk_score": 0,
        "risk_level": "low", "risk_reasons": ["ok"], "status": "ready",
    }
    local_store.create(record)
    reopened = WorkflowStore(db_path)
    assert reopened.get("test-id")["status"] == "ready"
    assert reopened.daily_spend(84532, _payload()["token_address"]) == 1_000_000


def test_proposal_blocks_caller_supplied_token_decimals_tampering():
    response = propose(_payload(token_decimals=18))
    assert response.status_code == 201
    assert response.json()["policy_decision"] == "block"
    assert any("decimals do not match" in reason.lower() for reason in response.json()["policy_reasons"])


def test_proposal_blocks_caller_supplied_token_symbol_tampering():
    response = propose(_payload(token_symbol="USDC.e"))
    assert response.status_code == 201
    assert response.json()["policy_decision"] == "block"
    assert any("symbol does not match" in reason.lower() for reason in response.json()["policy_reasons"])


def test_malformed_recipient_is_blocked_and_cannot_be_approved_or_executed():
    response = propose(_payload(recipient="0x1234"))
    assert response.status_code == 201
    record = response.json()
    assert record["status"] == "blocked"
    assert any("valid EVM address" in reason for reason in record["policy_reasons"])

    approve = owner_post(
        f"/transactions/{record['transaction_id']}/approve",
        json={"transaction_fingerprint": record["fingerprint"], "confirmation": "APPROVE"},
    )
    assert approve.status_code == 409
    assert owner_post(f"/transactions/{record['transaction_id']}/execute").status_code == 403


def test_execution_rechecks_risk_and_requires_approval_if_risk_escalates():
    record = propose(_payload(amount_base_units=40_000_000)).json()
    assert record["status"] == "ready"
    assert record["risk_level"] == "medium"

    # New reservations raise projected daily spend to 80% of the daily limit.
    for index in range(2):
        external_payload = _payload(amount_base_units=40_000_000)
        external_proposal = TransactionProposal(**external_payload)
        main.store.create({
            "transaction_id": f"risk-reservation-{index}",
            "fingerprint": fingerprint_proposal(external_proposal),
            "proposal": external_payload,
            "policy_decision": "allow",
            "policy_reasons": ["test reservation"],
            "risk_score": 25,
            "risk_level": "medium",
            "risk_reasons": [],
            "status": "ready",
        })

    execution = owner_post(f"/transactions/{record['transaction_id']}/execute")
    assert execution.status_code == 409
    assert execution.json()["detail"] == "Human approval is required before execution"

    refreshed = client.get(
        f"/transactions/{record['transaction_id']}", headers=OWNER_HEADERS
    ).json()
    assert refreshed["status"] == "awaiting_approval"
    assert refreshed["risk_level"] == "high"
    assert refreshed["risk_score"] == 50


def test_execution_invalidates_approval_if_risk_increases_after_approval():
    record = propose(
        _payload(
            recipient="0x4444444444444444444444444444444444444444",
            amount_base_units=40_000_000,
        )
    ).json()
    assert record["status"] == "awaiting_approval"
    assert record["risk_level"] == "high"

    approved = owner_post(
        f"/transactions/{record['transaction_id']}/approve",
        json={"transaction_fingerprint": record["fingerprint"], "confirmation": "APPROVE"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    for index in range(2):
        external_payload = _payload(amount_base_units=40_000_000)
        external_proposal = TransactionProposal(**external_payload)
        main.store.create({
            "transaction_id": f"approved-risk-reservation-{index}",
            "fingerprint": fingerprint_proposal(external_proposal),
            "proposal": external_payload,
            "policy_decision": "allow",
            "policy_reasons": ["test reservation"],
            "risk_score": 25,
            "risk_level": "medium",
            "risk_reasons": [],
            "status": "ready",
        })

    execution = owner_post(f"/transactions/{record['transaction_id']}/execute")
    assert execution.status_code == 409
    assert execution.json()["detail"] == "Human approval is required before execution"

    refreshed = client.get(
        f"/transactions/{record['transaction_id']}", headers=OWNER_HEADERS
    ).json()
    assert refreshed["status"] == "awaiting_approval"
    assert refreshed["approval_fingerprint"] is None
    assert refreshed["risk_score"] == 85



def test_audit_records_proposal_approval_and_simulated_execution():
    record = propose(
        _payload(recipient="0x4444444444444444444444444444444444444444")
    ).json()
    tx_id = record["transaction_id"]

    unauthenticated = client.get(f"/transactions/{tx_id}/audit")
    assert unauthenticated.status_code == 401

    initial = client.get(f"/transactions/{tx_id}/audit", headers=OWNER_HEADERS)
    assert initial.status_code == 200
    assert [event["event_type"] for event in initial.json()] == ["proposal_created"]
    assert initial.json()[0]["actor_role"] == "agent"
    assert initial.json()[0]["from_status"] is None
    assert initial.json()[0]["to_status"] == "awaiting_approval"
    assert initial.json()[0]["fingerprint"] == record["fingerprint"]

    approved = owner_post(
        f"/transactions/{tx_id}/approve",
        json={"transaction_fingerprint": record["fingerprint"], "confirmation": "APPROVE"},
    )
    assert approved.status_code == 200
    executed = owner_post(f"/transactions/{tx_id}/execute")
    assert executed.status_code == 200

    events = client.get(f"/transactions/{tx_id}/audit", headers=OWNER_HEADERS).json()
    assert [event["event_type"] for event in events] == [
        "proposal_created", "approval_granted", "simulated_execution"
    ]
    assert [event["actor_role"] for event in events] == ["agent", "owner", "owner"]
    assert [(event["from_status"], event["to_status"]) for event in events] == [
        (None, "awaiting_approval"),
        ("awaiting_approval", "approved"),
        ("approved", "executed_simulated"),
    ]
    assert events[-1]["details"]["execution_reference"].startswith("simulated:")


def test_duplicate_approval_does_not_create_second_approval_event():
    record = propose(
        _payload(recipient="0x4444444444444444444444444444444444444444")
    ).json()
    body = {"transaction_fingerprint": record["fingerprint"], "confirmation": "APPROVE"}
    path = f"/transactions/{record['transaction_id']}/approve"

    assert owner_post(path, json=body).status_code == 200
    assert owner_post(path, json=body).status_code == 409

    events = client.get(
        f"/transactions/{record['transaction_id']}/audit", headers=OWNER_HEADERS
    ).json()
    assert sum(event["event_type"] == "approval_granted" for event in events) == 1


def test_idempotency_replay_is_audited_without_duplicate_creation():
    headers = {**AGENT_HEADERS, "Idempotency-Key": "audit-replay-key"}
    first = client.post("/transactions/propose", headers=headers, json=_payload())
    replay = client.post("/transactions/propose", headers=headers, json=_payload())
    assert first.status_code == 201
    assert replay.status_code == 200
    tx_id = first.json()["transaction_id"]

    events = client.get(f"/transactions/{tx_id}/audit", headers=OWNER_HEADERS).json()
    assert [event["event_type"] for event in events] == [
        "proposal_created", "proposal_replayed"
    ]


def test_audit_events_reject_update_and_delete(tmp_path):
    local_store = WorkflowStore(tmp_path / "audit.sqlite3")
    record = {
        "transaction_id": "audit-immutable-test",
        "fingerprint": "a" * 64,
        "proposal": _payload(),
        "policy_decision": "allow",
        "policy_reasons": ["ok"],
        "risk_score": 0,
        "risk_level": "low",
        "risk_reasons": [],
        "status": "ready",
    }
    local_store.create(record, actor_role="agent")

    with local_store._connect() as db:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("UPDATE audit_events SET actor_role = 'owner' WHERE transaction_id = ?",
                       (record["transaction_id"],))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("DELETE FROM audit_events WHERE transaction_id = ?",
                       (record["transaction_id"],))

    assert len(local_store.audit_events(record["transaction_id"])) == 1


def test_risk_escalation_is_recorded_in_audit_trail():
    record = propose(_payload(amount_base_units=40_000_000)).json()
    for index in range(2):
        external_payload = _payload(amount_base_units=40_000_000)
        external_proposal = TransactionProposal(**external_payload)
        main.store.create({
            "transaction_id": f"audit-risk-reservation-{index}",
            "fingerprint": fingerprint_proposal(external_proposal),
            "proposal": external_payload,
            "policy_decision": "allow",
            "policy_reasons": ["test reservation"],
            "risk_score": 25,
            "risk_level": "medium",
            "risk_reasons": [],
            "status": "ready",
        })

    response = owner_post(f"/transactions/{record['transaction_id']}/execute")
    assert response.status_code == 409
    events = client.get(
        f"/transactions/{record['transaction_id']}/audit", headers=OWNER_HEADERS
    ).json()
    escalation = [event for event in events if event["event_type"] == "risk_escalation_requires_approval"]
    assert len(escalation) == 1
    assert escalation[0]["from_status"] == "ready"
    assert escalation[0]["to_status"] == "awaiting_approval"
    assert escalation[0]["details"]["risk_level"] == "high"


def test_approval_expiry_requires_fresh_approval_and_is_audited(monkeypatch):
    now = [datetime(2026, 10, 10, 12, 0, tzinfo=UTC)]
    monkeypatch.setattr(store_module, "_utc_now", lambda: now[0])
    record = propose(
        _payload(recipient="0x4444444444444444444444444444444444444444")
    ).json()
    tx_id = record["transaction_id"]
    approval_body = {
        "transaction_fingerprint": record["fingerprint"],
        "confirmation": "APPROVE",
    }

    approved = owner_post(f"/transactions/{tx_id}/approve", json=approval_body)
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_at"] == now[0].isoformat()

    now[0] += timedelta(seconds=301)
    expired_execution = owner_post(f"/transactions/{tx_id}/execute")
    assert expired_execution.status_code == 409
    assert expired_execution.json()["detail"] == "Human approval is required before execution"

    refreshed = client.get(f"/transactions/{tx_id}", headers=OWNER_HEADERS).json()
    assert refreshed["status"] == "awaiting_approval"
    assert refreshed["approval_fingerprint"] is None
    assert refreshed["approved_at"] is None

    events = client.get(f"/transactions/{tx_id}/audit", headers=OWNER_HEADERS).json()
    expiry_events = [event for event in events if event["event_type"] == "approval_expired"]
    assert len(expiry_events) == 1
    assert expiry_events[0]["from_status"] == "approved"
    assert expiry_events[0]["to_status"] == "awaiting_approval"
    assert expiry_events[0]["details"]["approval_expires_seconds"] == 300

    renewed = owner_post(f"/transactions/{tx_id}/approve", json=approval_body)
    assert renewed.status_code == 200
    assert renewed.json()["status"] == "approved"
    assert renewed.json()["approved_at"] == now[0].isoformat()

    executed = owner_post(f"/transactions/{tx_id}/execute")
    assert executed.status_code == 200
    assert executed.json()["status"] == "executed_simulated"


def test_approval_expiry_configuration_must_be_positive():
    from dataclasses import replace

    with pytest.raises(ValueError, match="must be positive"):
        replace(main.DEMO_POLICY, approval_expires_seconds=0)



def test_recipient_token_allowlist_allows_configured_combination():
    recipient = "0x2222222222222222222222222222222222222222"
    policy = replace(
        main.DEMO_POLICY,
        recipient_token_allowlist={recipient: {BASE_SEPOLIA_USDC_ADDRESS}},
    )
    proposal = TransactionProposal(**_payload(recipient=recipient))

    result = main.evaluate_transaction(proposal, policy)

    assert result.decision == main.PolicyDecision.ALLOW


def test_recipient_token_allowlist_hard_blocks_unconfigured_token():
    recipient = "0x2222222222222222222222222222222222222222"
    policy = replace(main.DEMO_POLICY, recipient_token_allowlist={recipient: set()})
    proposal = TransactionProposal(**_payload(recipient=recipient))

    result = main.evaluate_transaction(proposal, policy)

    assert result.decision == main.PolicyDecision.BLOCK
    assert "Token contract is not allowed for this recipient." in result.reasons


def test_recipient_token_allowlist_normalizes_addresses():
    recipient = "0x2222222222222222222222222222222222222222"
    policy = replace(
        main.DEMO_POLICY,
        recipient_token_allowlist={
            recipient.upper().replace("0X", "0x"): {BASE_SEPOLIA_USDC_ADDRESS.upper()}
        },
    )
    proposal = TransactionProposal(**_payload(recipient=recipient))

    result = main.evaluate_transaction(proposal, policy)

    assert result.decision == main.PolicyDecision.ALLOW


def test_unconfigured_recipient_keeps_existing_approval_behavior():
    recipient = "0x4444444444444444444444444444444444444444"
    policy = replace(
        main.DEMO_POLICY,
        recipient_token_allowlist={
            "0x2222222222222222222222222222222222222222": {BASE_SEPOLIA_USDC_ADDRESS}
        },
        allowed_recipients=set(),
        unknown_recipient_requires_approval=True,
    )
    proposal = TransactionProposal(**_payload(recipient=recipient))

    result = main.evaluate_transaction(proposal, policy)

    assert result.decision == main.PolicyDecision.REQUIRE_APPROVAL
