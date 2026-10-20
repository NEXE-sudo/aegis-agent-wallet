import json
import sqlite3
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

import app.workflow.store as store_module
from app import main
from app.chain.base_sepolia import BASE_SEPOLIA_USDC_ADDRESS
from app.policy.canonical import hash_policy
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


def test_api_version_matches_package_version():
    root = Path(__file__).resolve().parents[1]
    project_version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    lock_packages = tomllib.loads((root / "uv.lock").read_text())["package"]
    locked_project_version = next(
        package["version"]
        for package in lock_packages
        if package["name"] == "aegis-agent-wallet"
    )
    frontend = (root / "frontend" / "index.html").read_text()
    assert main.app.version == project_version
    assert locked_project_version == project_version
    assert f"v{project_version} API" in frontend


def test_recent_transactions_list_requires_owner_and_returns_persisted_records():
    unauthorized = client.get("/transactions")
    assert unauthorized.status_code == 401

    first = propose()
    second = propose(_payload(recipient="0x4444444444444444444444444444444444444444"))
    assert first.status_code == 201
    assert second.status_code == 201

    response = client.get("/transactions?limit=10", headers=OWNER_HEADERS)
    assert response.status_code == 200
    records = response.json()
    assert {record["transaction_id"] for record in records} == {
        first.json()["transaction_id"], second.json()["transaction_id"]
    }
    assert all("proposal" in record and "risk_score" in record for record in records)


def test_recent_transactions_list_respects_limit_and_offset():
    first = propose()
    second = propose(_payload(recipient="0x3333333333333333333333333333333333333333"))
    third = propose(_payload(recipient="0x4444444444444444444444444444444444444444"))
    assert [first.status_code, second.status_code, third.status_code] == [201, 201, 201]

    all_records = client.get("/transactions?limit=10", headers=OWNER_HEADERS)
    limited_records = client.get("/transactions?limit=2", headers=OWNER_HEADERS)
    second_page = client.get("/transactions?limit=2&offset=2", headers=OWNER_HEADERS)
    invalid_offset = client.get("/transactions?offset=-1", headers=OWNER_HEADERS)

    assert all(
        response.status_code == 200
        for response in (all_records, limited_records, second_page)
    )
    assert invalid_offset.status_code == 422
    all_ids = [record["transaction_id"] for record in all_records.json()]
    limited_ids = [record["transaction_id"] for record in limited_records.json()]
    page_ids = [record["transaction_id"] for record in second_page.json()]
    assert len(all_ids) == 3
    assert len(limited_ids) == 2
    assert limited_ids == all_ids[:2]
    assert page_ids == all_ids[2:]

def test_recent_transactions_list_validates_limit():
    below_minimum = client.get("/transactions?limit=0", headers=OWNER_HEADERS)
    above_maximum = client.get("/transactions?limit=101", headers=OWNER_HEADERS)
    at_maximum = client.get("/transactions?limit=100", headers=OWNER_HEADERS)

    assert below_minimum.status_code == 422
    assert above_maximum.status_code == 422
    assert at_maximum.status_code == 200


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


def test_policy_endpoint_reports_amount_and_projected_daily_spend():
    response = client.post(
        "/policy/evaluate",
        json={**_payload(amount_base_units=1_000_000), "daily_spent_base_units": 5_000_000},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "allow"
    assert body["amount_base_units"] == 1_000_000
    assert body["projected_daily_spend_base_units"] == 6_000_000
    assert body["reasons"]


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


def test_risk_endpoint_reports_amount_and_projected_daily_spend():
    response = client.post(
        "/risk/assess",
        json={**_payload(amount_base_units=1_000_000), "daily_spent_base_units": 5_000_000},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["amount_base_units"] == 1_000_000
    assert body["projected_daily_spend_base_units"] == 6_000_000
    assert body["reasons"]


def test_risk_endpoint_rejects_unknown_agent():
    response = client.post("/risk/assess", json={**_payload(), "agent_id": "unknown-agent"})
    assert response.status_code == 404


@pytest.mark.parametrize("endpoint", ["/policy/evaluate", "/risk/assess"])
@pytest.mark.parametrize(
    "overrides",
    [
        {"daily_spent_base_units": -1},
        {"daily_spent_base_units": 2**63},
        {"amount_base_units": 0},
        {"amount_base_units": 2**63},
        {"chain_id": -1},
        {"chain_id": 0},
        {"chain_id": 2**63},
        {"token_decimals": -1},
        {"token_decimals": 37},
    ],
    ids=[
        "negative-daily-spend",
        "daily-spend-above-sqlite-range",
        "zero-amount",
        "amount-above-sqlite-range",
        "negative-chain-id",
        "zero-chain-id",
        "chain-id-above-sqlite-range",
        "negative-token-decimals",
        "decimals-above-supported-range",
    ],
)
def test_standalone_evaluation_endpoints_reject_out_of_range_inputs(endpoint, overrides):
    response = client.post(
        endpoint,
        json={**_payload(), "daily_spent_base_units": 0, **overrides},
    )

    assert response.status_code == 422


def test_workflow_requires_distinct_authentication():
    assert client.post("/transactions/propose", json=_payload()).status_code == 401
    assert client.post("/transactions/propose", headers=OWNER_HEADERS, json=_payload()).status_code == 401
    record = propose().json()
    assert client.get(f"/transactions/{record['transaction_id']}").status_code == 401
    assert owner_post(f"/transactions/{record['transaction_id']}/execute").status_code == 200


def test_agent_token_cannot_pause_or_resume_and_pause_blocks_execution():
    paused = client.post("/pause", headers=AGENT_HEADERS, json={"reason": "agent override"})
    assert paused.status_code == 401

    resumed = client.post("/resume", headers=AGENT_HEADERS, json={"reason": "agent override"})
    assert resumed.status_code == 401

    owner_pause = owner_post("/pause", json={"reason": "maintenance"})
    assert owner_pause.status_code == 200
    assert owner_pause.json()["paused"] is True

    payload = _payload()
    proposal = client.post("/transactions/propose", headers=AGENT_HEADERS, json=payload)
    assert proposal.status_code == 201
    assert proposal.json()["status"] == "blocked"

    execution = owner_post(f"/transactions/{proposal.json()['transaction_id']}/execute")
    assert execution.status_code == 409
    assert execution.json()["detail"]["code"] == "wallet_paused"

    owner_resume = owner_post("/resume", json={"reason": "recovered"})
    assert owner_resume.status_code == 200
    assert owner_resume.json()["paused"] is False


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/transactions/propose"),
        ("get", "/chain/base-sepolia/status"),
    ],
)
def test_non_ascii_bearer_header_returns_401_not_500(method, path):
    headers = [("Authorization", b"Bearer caf\xe9")]
    if method == "post":
        response = client.post(path, headers=headers, json=_payload())
    else:
        response = client.get(path, headers=headers)

    assert response.status_code == 401


def test_simulated_execution_is_terminal_and_cannot_be_approved():
    record = propose().json()
    transaction_id = record["transaction_id"]

    executed = owner_post(f"/transactions/{transaction_id}/execute")
    assert executed.status_code == 200
    assert executed.json()["status"] == "executed_simulated"

    approval = owner_post(
        f"/transactions/{transaction_id}/approve",
        json={
            "transaction_fingerprint": record["fingerprint"],
            "confirmation": "APPROVE",
        },
    )
    assert approval.status_code == 409

    current = client.get(
        f"/transactions/{transaction_id}", headers=OWNER_HEADERS
    )
    assert current.status_code == 200
    assert current.json()["status"] == "executed_simulated"
    assert current.json()["approval_fingerprint"] is None

    events = client.get(
        f"/transactions/{transaction_id}/audit", headers=OWNER_HEADERS
    ).json()
    event_types = [event["event_type"] for event in events]
    assert event_types.count("simulated_execution") == 1
    assert "approval_granted" not in event_types


def test_low_risk_allowlisted_transaction_executes_without_separate_approval():
    proposed = propose(_payload(amount_base_units=2_500_000))
    assert proposed.status_code == 201
    record = proposed.json()
    assert record["status"] == "ready"
    assert record["policy_decision"] == "allow"
    assert record["risk_level"] == "low"

    executed = owner_post(f"/transactions/{record['transaction_id']}/execute")
    assert executed.status_code == 200
    assert executed.json()["status"] == "executed_simulated"
    assert executed.json()["execution_reference"].startswith("simulated:")
    assert executed.json()["approval_fingerprint"] is None

    persisted = main.store.get(record["transaction_id"])
    assert persisted["status"] == "executed_simulated"
    assert persisted["approval_fingerprint"] is None
    assert [
        event["event_type"] for event in main.store.audit_events(record["transaction_id"])
    ] == ["proposal_created", "simulated_execution"]

    assert owner_post(f"/transactions/{record['transaction_id']}/execute").status_code == 409


@pytest.mark.parametrize(
    "approval_body",
    [
        {"transaction_fingerprint": "short", "confirmation": "APPROVE"},
        {"transaction_fingerprint": "a" * 64, "confirmation": "approve"},
        {"transaction_fingerprint": "a" * 64},
    ],
    ids=["malformed-fingerprint", "invalid-confirmation", "missing-confirmation"],
)
def test_approval_request_validation_rejects_invalid_payloads(approval_body):
    record = propose(
        _payload(recipient="0x4444444444444444444444444444444444444444")
    ).json()

    response = owner_post(
        f"/transactions/{record['transaction_id']}/approve",
        json=approval_body,
    )

    assert response.status_code == 422


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


def test_owner_can_cancel_reservation_and_restore_daily_budget():
    proposals = [
        propose(_payload(amount_base_units=50_000_000)).json()
        for _ in range(3)
    ]
    assert sum(record["status"] != "blocked" for record in proposals) == 3
    transaction_id = proposals[0]["transaction_id"]
    assert main.store.daily_spend(84532, BASE_SEPOLIA_USDC_ADDRESS) == 150_000_000

    unauthorized = client.post(f"/transactions/{transaction_id}/cancel")
    assert unauthorized.status_code == 401

    cancelled = owner_post(f"/transactions/{transaction_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert main.store.daily_spend(84532, BASE_SEPOLIA_USDC_ADDRESS) == 100_000_000
    assert owner_post(f"/transactions/{transaction_id}/execute").status_code == 409

    replacement = propose(_payload(amount_base_units=50_000_000))
    assert replacement.status_code == 201
    assert replacement.json()["status"] != "blocked"

    audit = client.get(
        f"/transactions/{transaction_id}/audit", headers=OWNER_HEADERS
    )
    assert audit.status_code == 200
    event = audit.json()[-1]
    assert event["event_type"] == "transaction_cancelled"
    assert event["details"]["reservation_released"] is True


def test_owner_can_reject_reservation_and_restore_daily_budget():
    proposals = [
        propose(_payload(amount_base_units=50_000_000)).json()
        for _ in range(3)
    ]
    assert sum(record["status"] != "blocked" for record in proposals) == 3
    previously_blocked = propose(_payload(amount_base_units=50_000_000)).json()
    assert previously_blocked["status"] == "blocked"
    transaction_id = proposals[0]["transaction_id"]
    assert main.store.daily_spend(84532, BASE_SEPOLIA_USDC_ADDRESS) == 150_000_000

    unauthorized = client.post(
        f"/transactions/{transaction_id}/reject", headers=AGENT_HEADERS
    )
    assert unauthorized.status_code == 401

    rejected = owner_post(f"/transactions/{transaction_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert main.store.daily_spend(84532, BASE_SEPOLIA_USDC_ADDRESS) == 100_000_000

    replacement = propose(_payload(amount_base_units=50_000_000))
    assert replacement.status_code == 201
    assert replacement.json()["status"] != "blocked"

    audit = client.get(
        f"/transactions/{transaction_id}/audit", headers=OWNER_HEADERS
    )
    assert audit.status_code == 200
    event = audit.json()[-1]
    assert event["event_type"] == "transaction_rejected"
    assert event["from_status"] == proposals[0]["status"]
    assert event["to_status"] == "rejected"
    assert event["actor_role"] == "owner"
    assert event["details"]["reservation_released"] is True


def test_reject_is_idempotent_without_duplicate_audit_event():
    record = propose().json()
    path = f"/transactions/{record['transaction_id']}/reject"

    first = owner_post(path)
    second = owner_post(path)

    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "rejected"
    events = client.get(
        f"/transactions/{record['transaction_id']}/audit", headers=OWNER_HEADERS
    ).json()
    assert [event["event_type"] for event in events] == [
        "proposal_created",
        "transaction_rejected",
    ]


@pytest.mark.parametrize("initial_status", ["blocked", "executed_simulated"])
def test_reject_returns_clear_error_for_blocked_or_executed_transactions(initial_status):
    record = propose(_payload(chain_id=1) if initial_status == "blocked" else None).json()
    if initial_status == "executed_simulated":
        executed = owner_post(f"/transactions/{record['transaction_id']}/execute")
        assert executed.status_code == 200

    response = owner_post(f"/transactions/{record['transaction_id']}/reject")

    assert response.status_code == 409
    assert response.json()["detail"] == "Transaction cannot be rejected in its current state"


def test_cancel_rejects_terminal_transactions():
    record = propose().json()
    executed = owner_post(f"/transactions/{record['transaction_id']}/execute")
    assert executed.status_code == 200

    response = owner_post(f"/transactions/{record['transaction_id']}/cancel")

    assert response.status_code == 409
    assert response.json()["detail"] == "Transaction cannot be cancelled in its current state"


@pytest.mark.parametrize("key", ["        ", "\t\t\t\t\t\t\t\t", " \t       "])
def test_idempotency_key_rejects_whitespace_only_values(key):
    response = client.post(
        "/transactions/propose",
        headers={**AGENT_HEADERS, "Idempotency-Key": key},
        json=_payload(),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Idempotency-Key must not be blank"


def test_concurrent_idempotency_retries_create_one_transaction(tmp_path):
    local_store = WorkflowStore(tmp_path / "concurrent-idempotency.sqlite3")
    controller = TransactionController(main.DEMO_POLICY, local_store)
    proposal = TransactionProposal(**_payload())
    retry_count = 8
    barrier = Barrier(retry_count)

    def submit_same_request():
        barrier.wait(timeout=5)
        return controller.propose(proposal, "concurrent-retry-key")

    with ThreadPoolExecutor(max_workers=retry_count) as executor:
        outcomes = list(executor.map(lambda _: submit_same_request(), range(retry_count)))

    assert sum(outcome == "created" for outcome, _ in outcomes) == 1
    assert sum(outcome == "replayed" for outcome, _ in outcomes) == retry_count - 1
    transaction_ids = {record["transaction_id"] for _, record in outcomes}
    assert len(transaction_ids) == 1

    transaction_id = transaction_ids.pop()
    events = local_store.audit_events(transaction_id)
    event_types = [event["event_type"] for event in events]
    assert event_types.count("proposal_created") == 1
    assert event_types.count("proposal_replayed") == retry_count - 1


def test_concurrent_idempotency_key_conflict_creates_only_one_proposal(tmp_path):
    local_store = WorkflowStore(tmp_path / "concurrent-idempotency-conflict.sqlite3")
    controller = TransactionController(main.DEMO_POLICY, local_store)
    proposal_count = 2
    barrier = Barrier(proposal_count)
    proposals = [
        TransactionProposal(**_payload(amount_base_units=amount))
        for amount in (1_000_000, 2_000_000)
    ]

    def submit(proposal):
        barrier.wait(timeout=5)
        return controller.propose(proposal, "concurrent-conflict-key")

    with ThreadPoolExecutor(max_workers=proposal_count) as executor:
        outcomes = list(executor.map(submit, proposals))

    assert sorted(outcome for outcome, _ in outcomes) == [
        "created",
        "idempotency_conflict",
    ]
    records = [record for _, record in outcomes]
    assert len({record["transaction_id"] for record in records}) == 1
    transaction_id = records[0]["transaction_id"]

    events = local_store.audit_events(transaction_id)
    event_types = [event["event_type"] for event in events]
    assert event_types.count("proposal_created") == 1
    assert event_types.count("idempotency_conflict") == 1
    assert event_types.count("proposal_replayed") == 0


def test_concurrent_distinct_proposals_cannot_overspend_daily_limit(tmp_path):
    local_store = WorkflowStore(tmp_path / "concurrent-reservations.sqlite3")
    controller = TransactionController(main.DEMO_POLICY, local_store)
    proposal_count = 4
    barrier = Barrier(proposal_count)
    proposals = [
        TransactionProposal(
            **_payload(
                recipient=f"0x{index:040x}",
                amount_base_units=45_000_000,
            )
        )
        for index in range(1, proposal_count + 1)
    ]

    def submit(proposal):
        barrier.wait(timeout=5)
        return controller.propose(proposal)[1]

    with ThreadPoolExecutor(max_workers=proposal_count) as executor:
        records = list(executor.map(submit, proposals))

    assert sum(record["status"] != "blocked" for record in records) == 3
    assert sum(record["status"] == "blocked" for record in records) == 1
    assert local_store.daily_spend(84532, BASE_SEPOLIA_USDC_ADDRESS) == 135_000_000
    assert sum(
        event["event_type"] == "proposal_created"
        for record in records
        for event in local_store.audit_events(record["transaction_id"])
    ) == proposal_count


def test_idempotency_replay_returns_same_transaction_and_conflict_for_changed_payload():
    headers = {**AGENT_HEADERS, "Idempotency-Key": "retry-key-0001"}
    first = client.post("/transactions/propose", headers=headers, json=_payload())
    second = client.post("/transactions/propose", headers=headers, json=_payload())
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["transaction_id"] == second.json()["transaction_id"]
    conflict = client.post("/transactions/propose", headers=headers, json=_payload(amount_base_units=2_000_000))
    assert conflict.status_code == 409


def test_idempotency_replay_after_execution_preserves_terminal_state():
    headers = {**AGENT_HEADERS, "Idempotency-Key": "executed-replay-key-0001"}
    first = client.post("/transactions/propose", headers=headers, json=_payload())
    assert first.status_code == 201
    transaction_id = first.json()["transaction_id"]

    executed = owner_post(f"/transactions/{transaction_id}/execute")
    assert executed.status_code == 200
    assert executed.json()["status"] == "executed_simulated"

    replay = client.post("/transactions/propose", headers=headers, json=_payload())
    assert replay.status_code == 200
    assert replay.json()["transaction_id"] == transaction_id
    assert replay.json()["status"] == "executed_simulated"

    events = client.get(f"/transactions/{transaction_id}/audit", headers=OWNER_HEADERS).json()
    event_types = [event["event_type"] for event in events]
    assert event_types.count("proposal_created") == 1
    assert event_types.count("proposal_replayed") == 1
    assert event_types.count("simulated_execution") == 1


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


def test_documented_demo_token_symbol_mismatch_is_hard_blocked():
    response = propose(_payload(token_symbol="FAKE"))
    assert response.status_code == 201
    record = response.json()
    assert record["status"] == "blocked"
    assert record["policy_decision"] == "block"
    assert any("symbol does not match" in reason.lower() for reason in record["policy_reasons"])

    approval = owner_post(
        f"/transactions/{record['transaction_id']}/approve",
        json={
            "transaction_fingerprint": record["fingerprint"],
            "confirmation": "APPROVE",
        },
    )
    assert approval.status_code == 409
    execution = owner_post(f"/transactions/{record['transaction_id']}/execute")
    assert execution.status_code == 403


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("agent_id", "a" * 65),
        ("token_symbol", "S" * 17),
        ("token_address", "0x" + "a" * 41),
        ("recipient", "0x" + "2" * 41),
        ("recipient", "0x" + "2" * 40 + "x" * 10000),
    ],
)
def test_proposal_rejects_oversized_text_fields(field, value):
    response = propose(_payload(**{field: value}))
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chain_id", True),
        ("amount_base_units", True),
        ("amount_base_units", "1000000"),
        ("amount_base_units", 1_000_000.0),
        ("token_decimals", 6.0),
    ],
)
def test_proposal_rejects_non_strict_integer_fields(field, value):
    response = propose(_payload(**{field: value}))
    assert response.status_code == 422


def test_proposal_canonicalizes_ascii_text_before_fingerprinting():
    headers = {**AGENT_HEADERS, "Idempotency-Key": "canonical-retry-key"}
    clean_payload = _payload()
    first = client.post("/transactions/propose", headers=headers, json=clean_payload)
    assert first.status_code == 201

    noncanonical_payload = _payload(
        token_symbol=" usdc ",
        token_address=f" {BASE_SEPOLIA_USDC_ADDRESS.upper()} ",
        recipient=f"  {clean_payload['recipient']}\n",
    )
    retry = client.post("/transactions/propose", headers=headers, json=noncanonical_payload)

    assert retry.status_code == 200
    assert retry.json()["transaction_id"] == first.json()["transaction_id"]
    assert retry.json()["fingerprint"] == first.json()["fingerprint"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("token_symbol", "UſDC"),
        ("recipient", "\u20030x2222222222222222222222222222222222222222"),
    ],
)
def test_proposal_rejects_non_ascii_lookalikes_and_padding(field, value):
    response = propose(_payload(**{field: value}))
    assert response.status_code == 422


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



def test_owner_can_read_existing_transaction():
    created = propose()
    assert created.status_code == 201
    record = created.json()

    response = client.get(
        f"/transactions/{record['transaction_id']}",
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["transaction_id"] == record["transaction_id"]
    assert body["status"] == record["status"] == "ready"
    assert body["fingerprint"] == record["fingerprint"]
    assert body["proposal"] == record["proposal"]


@pytest.mark.parametrize(
    "path",
    ["/transactions/missing-transaction", "/transactions/missing-transaction/audit"],
)
def test_owner_read_endpoints_return_404_for_unknown_transaction(path):
    response = client.get(path, headers=OWNER_HEADERS)

    assert response.status_code == 404
    assert response.json()["detail"] == "Transaction not found"


def test_audit_records_proposal_approval_and_simulated_execution():
    record = propose(
        _payload(recipient="0x4444444444444444444444444444444444444444")
    ).json()
    tx_id = record["transaction_id"]

    unauthenticated = client.get(f"/transactions/{tx_id}/audit")
    assert unauthenticated.status_code == 401

    agent_authenticated = client.get(
        f"/transactions/{tx_id}/audit", headers=AGENT_HEADERS
    )
    assert agent_authenticated.status_code == 401

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


def test_concurrent_duplicate_approvals_grant_approval_once():
    record = propose(
        _payload(recipient="0x4444444444444444444444444444444444444444")
    ).json()
    path = f"/transactions/{record['transaction_id']}/approve"
    body = {
        "transaction_fingerprint": record["fingerprint"],
        "confirmation": "APPROVE",
    }

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: owner_post(path, json=body),
                range(2),
            )
        )

    assert sorted(response.status_code for response in responses) == [200, 409]
    current = client.get(
        f"/transactions/{record['transaction_id']}", headers=OWNER_HEADERS
    )
    assert current.status_code == 200
    assert current.json()["status"] == "approved"
    assert current.json()["approval_fingerprint"] == record["fingerprint"]

    events = client.get(
        f"/transactions/{record['transaction_id']}/audit", headers=OWNER_HEADERS
    ).json()
    assert sum(event["event_type"] == "approval_granted" for event in events) == 1


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


def test_approval_rejects_persisted_proposal_tampering():
    record = propose(
        _payload(recipient="0x4444444444444444444444444444444444444444")
    ).json()
    changed_proposal = dict(record["proposal"])
    changed_proposal["recipient"] = "0x3333333333333333333333333333333333333333"
    with main.store._connect() as db:
        db.execute(
            "UPDATE transactions SET proposal_json = ? WHERE transaction_id = ?",
            (json.dumps(changed_proposal, sort_keys=True), record["transaction_id"]),
        )

    response = owner_post(
        f"/transactions/{record['transaction_id']}/approve",
        json={
            "transaction_fingerprint": record["fingerprint"],
            "confirmation": "APPROVE",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Persisted proposal failed integrity verification"
    events = client.get(
        f"/transactions/{record['transaction_id']}/audit", headers=OWNER_HEADERS
    ).json()
    assert events[-1]["event_type"] == "proposal_integrity_mismatch"
    assert events[-1]["details"]["operation"] == "approve"


def test_execution_rejects_persisted_proposal_tampering():
    record = propose().json()
    changed_proposal = dict(record["proposal"])
    changed_proposal["amount_base_units"] += 1
    with main.store._connect() as db:
        db.execute(
            "UPDATE transactions SET proposal_json = ? WHERE transaction_id = ?",
            (json.dumps(changed_proposal, sort_keys=True), record["transaction_id"]),
        )

    response = owner_post(f"/transactions/{record['transaction_id']}/execute")

    assert response.status_code == 409
    assert response.json()["detail"] == "Persisted proposal failed integrity verification"
    events = client.get(
        f"/transactions/{record['transaction_id']}/audit", headers=OWNER_HEADERS
    ).json()
    assert events[-1]["event_type"] == "proposal_integrity_mismatch"
    assert events[-1]["details"]["operation"] == "execute"


def test_idempotency_replay_rejects_tampered_persisted_proposal():
    payload = _payload()
    first = client.post(
        "/transactions/propose",
        json=payload,
        headers={**AGENT_HEADERS, "Idempotency-Key": "integrity-replay-01"},
    )
    assert first.status_code == 201
    record = first.json()
    changed_proposal = dict(record["proposal"])
    changed_proposal["amount_base_units"] += 1
    with main.store._connect() as db:
        db.execute(
            "UPDATE transactions SET proposal_json = ? WHERE transaction_id = ?",
            (json.dumps(changed_proposal, sort_keys=True), record["transaction_id"]),
        )

    replay = client.post(
        "/transactions/propose",
        json=payload,
        headers={**AGENT_HEADERS, "Idempotency-Key": "integrity-replay-01"},
    )

    assert replay.status_code == 409
    assert replay.json()["detail"] == "Idempotency-Key was already used for a different proposal"
    events = client.get(
        f"/transactions/{record['transaction_id']}/audit", headers=OWNER_HEADERS
    ).json()
    assert events[-1]["event_type"] == "proposal_integrity_mismatch"
    assert events[-1]["details"]["operation"] == "idempotency_replay"


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

        original = db.execute(
            """SELECT event_id, transaction_id, event_type, actor_role, from_status,
                      to_status, fingerprint, details_json, created_at
               FROM audit_events WHERE transaction_id = ?""",
            (record["transaction_id"],),
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute(
                """INSERT OR REPLACE INTO audit_events (
                       event_id, transaction_id, event_type, actor_role, from_status,
                       to_status, fingerprint, details_json, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    original["event_id"], original["transaction_id"], "approval_granted",
                    "owner", original["from_status"], "approved", original["fingerprint"],
                    original["details_json"], original["created_at"],
                ),
            )

        unchanged = db.execute(
            """SELECT event_type, actor_role, to_status FROM audit_events
               WHERE event_id = ?""",
            (original["event_id"],),
        ).fetchone()
        assert tuple(unchanged) == ("proposal_created", "agent", "ready")

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


def test_reapproval_attempt_expires_stale_approval_before_requiring_fresh_approval(monkeypatch):
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

    now[0] += timedelta(seconds=301)
    stale_retry = owner_post(f"/transactions/{tx_id}/approve", json=approval_body)
    assert stale_retry.status_code == 409
    assert stale_retry.json()["detail"] == "Approval has expired; submit a fresh approval"

    refreshed = client.get(f"/transactions/{tx_id}", headers=OWNER_HEADERS).json()
    assert refreshed["status"] == "awaiting_approval"
    assert refreshed["approval_fingerprint"] is None
    assert refreshed["approved_at"] is None

    events = client.get(f"/transactions/{tx_id}/audit", headers=OWNER_HEADERS).json()
    expiry_events = [event for event in events if event["event_type"] == "approval_expired"]
    assert len(expiry_events) == 1
    assert expiry_events[0]["details"]["operation"] == "approve"

    renewed = owner_post(f"/transactions/{tx_id}/approve", json=approval_body)
    assert renewed.status_code == 200
    assert renewed.json()["status"] == "approved"
    assert renewed.json()["approved_at"] == now[0].isoformat()


@pytest.mark.parametrize(
    "timestamp_case",
    ["missing", "malformed", "future", "expiry_boundary"],
)
def test_execution_invalidates_invalid_or_expired_approval_timestamps(
    monkeypatch, timestamp_case
):
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

    if timestamp_case == "missing":
        stored_timestamp = None
    elif timestamp_case == "malformed":
        stored_timestamp = "not-a-timestamp"
    elif timestamp_case == "future":
        stored_timestamp = (now[0] + timedelta(seconds=1)).isoformat()
    else:
        # Expiry is inclusive: an approval exactly at the configured age is stale.
        stored_timestamp = (now[0] - timedelta(seconds=300)).isoformat()

    with main.store._connect() as db:
        db.execute(
            "UPDATE transactions SET approved_at = ? WHERE transaction_id = ?",
            (stored_timestamp, tx_id),
        )

    response = owner_post(f"/transactions/{tx_id}/execute")
    assert response.status_code == 409
    assert response.json()["detail"] == "Human approval is required before execution"

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


def test_recipient_daily_limit_blocks_proposal_after_prior_reservations(tmp_path):
    recipient = "0x2222222222222222222222222222222222222222"
    policy = replace(
        main.DEMO_POLICY,
        recipient_daily_limits_base_units={recipient: 20_000_000},
    )
    local_store = WorkflowStore(tmp_path / "recipient-limit.sqlite3")
    controller = TransactionController(policy, local_store)

    first_proposal = TransactionProposal(**_payload(recipient=recipient, amount_base_units=12_000_000))
    first_outcome, first = controller.propose(first_proposal)
    assert first_outcome == "created"
    assert first["status"] == "ready"

    second_proposal = TransactionProposal(**_payload(recipient=recipient, amount_base_units=9_000_000))
    second_outcome, second = controller.propose(second_proposal)
    assert second_outcome == "created"
    assert second["status"] == "blocked"
    assert "Transaction would exceed the recipient daily spending limit." in second["policy_reasons"]


def test_recipient_daily_limit_is_rechecked_before_execution(tmp_path):
    recipient = "0x2222222222222222222222222222222222222222"
    permissive_policy = replace(
        main.DEMO_POLICY,
        recipient_daily_limits_base_units={recipient: 30_000_000},
    )
    local_store = WorkflowStore(tmp_path / "recipient-recheck.sqlite3")
    controller = TransactionController(permissive_policy, local_store)
    second_proposal = TransactionProposal(**_payload(recipient=recipient, amount_base_units=9_000_000))
    _, second = controller.propose(second_proposal)
    assert second["status"] == "ready"

    first_proposal = TransactionProposal(**_payload(recipient=recipient, amount_base_units=12_000_000))
    _, first = controller.propose(first_proposal)
    assert first["status"] == "ready"

    # A stricter policy is applied after both reservations were created.
    # Execution must revalidate the recipient cap under the write lock.
    strict_policy = replace(
        main.DEMO_POLICY,
        recipient_daily_limits_base_units={recipient: 20_000_000},
    )
    strict_controller = TransactionController(strict_policy, local_store)
    outcome, record = strict_controller.execute(second["transaction_id"])
    assert outcome == "blocked"
    assert record["status"] == "blocked"
    assert "Transaction would exceed the recipient daily spending limit." in record["policy_reasons"]


def test_recipient_daily_limit_configuration_cannot_be_negative():
    with pytest.raises(ValueError, match="cannot be negative"):
        replace(
            main.DEMO_POLICY,
            recipient_daily_limits_base_units={
                "0x2222222222222222222222222222222222222222": -1
            },
        )



def test_recipient_transaction_limit_allows_amount_within_cap():
    recipient = "0x2222222222222222222222222222222222222222"
    policy = replace(
        main.DEMO_POLICY,
        recipient_transaction_limits_base_units={recipient: 10_000_000},
    )
    proposal = TransactionProposal(**_payload(recipient=recipient, amount_base_units=9_000_000))

    result = main.evaluate_transaction(proposal, policy)

    assert result.decision == main.PolicyDecision.ALLOW


def test_recipient_transaction_limit_hard_blocks_amount_above_cap():
    recipient = "0x2222222222222222222222222222222222222222"
    policy = replace(
        main.DEMO_POLICY,
        recipient_transaction_limits_base_units={recipient: 10_000_000},
    )
    proposal = TransactionProposal(**_payload(recipient=recipient, amount_base_units=11_000_000))

    result = main.evaluate_transaction(proposal, policy)

    assert result.decision == main.PolicyDecision.BLOCK
    assert "Transaction exceeds the recipient-specific transaction limit." in result.reasons


@pytest.mark.parametrize(
    ("amount_base_units", "expected_status", "expected_decision"),
    [
        (20_000_000, 201, "ready"),
        (20_000_001, 201, "blocked"),
    ],
)
def test_recipient_transaction_limit_boundary(
    monkeypatch, tmp_path, amount_base_units, expected_status, expected_decision
):
    recipient = "0x2222222222222222222222222222222222222222"
    policy = replace(
        main.DEMO_POLICY,
        recipient_transaction_limits_base_units={recipient: 20_000_000},
    )
    local_store = WorkflowStore(tmp_path / "recipient-transaction-limit.sqlite3")
    monkeypatch.setattr(main, "store", local_store)
    monkeypatch.setattr(main, "controller", TransactionController(policy, local_store))

    response = client.post(
        "/transactions/propose",
        headers={**AGENT_HEADERS, "Idempotency-Key": f"recipient-cap-{amount_base_units}"},
        json={
            "agent_id": "devops-01",
            "chain_id": 84532,
            "token_symbol": "USDC",
            "token_address": BASE_SEPOLIA_USDC_ADDRESS,
            "recipient": recipient,
            "amount_base_units": amount_base_units,
            "token_decimals": 6,
        },
    )

    assert response.status_code == expected_status
    assert response.json()["status"] == expected_decision
    if expected_decision == "blocked":
        assert any(
            "recipient-specific transaction limit" in reason.lower()
            for reason in response.json()["policy_reasons"]
        )


def test_recipient_transaction_limit_normalizes_address_case():
    recipient = "0x2222222222222222222222222222222222222222"
    policy = replace(
        main.DEMO_POLICY,
        recipient_transaction_limits_base_units={recipient.upper().replace("0X", "0x"): 10_000_000},
    )
    proposal = TransactionProposal(**_payload(recipient=recipient, amount_base_units=11_000_000))

    result = main.evaluate_transaction(proposal, policy)

    assert result.decision == main.PolicyDecision.BLOCK
    assert "Transaction exceeds the recipient-specific transaction limit." in result.reasons


def test_recipient_transaction_limit_configuration_cannot_be_negative():
    with pytest.raises(ValueError, match="cannot be negative"):
        replace(
            main.DEMO_POLICY,
            recipient_transaction_limits_base_units={
                "0x2222222222222222222222222222222222222222": -1
            },
        )


def test_policy_rejects_nonpositive_global_spending_limits():
    with pytest.raises(ValueError, match="max_transaction_base_units must be positive"):
        replace(main.DEMO_POLICY, max_transaction_base_units=0)
    with pytest.raises(ValueError, match="daily_limit_base_units must be positive"):
        replace(main.DEMO_POLICY, daily_limit_base_units=-1)


def test_policy_rejects_negative_approval_threshold_but_allows_zero():
    with pytest.raises(ValueError, match="cannot be negative"):
        replace(main.DEMO_POLICY, approval_threshold_base_units=-1)

    policy = replace(main.DEMO_POLICY, approval_threshold_base_units=0)
    assert policy.approval_threshold_base_units == 0


def test_policy_rejects_invalid_chain_configuration():
    with pytest.raises(ValueError, match="positive chain IDs"):
        replace(main.DEMO_POLICY, allowed_chain_ids={0})
    with pytest.raises(ValueError, match="positive chain IDs"):
        replace(main.DEMO_POLICY, allowed_chain_ids=set())


def test_policy_rejects_empty_agent_id_and_token_symbols():
    with pytest.raises(ValueError, match="agent_id cannot be empty"):
        replace(main.DEMO_POLICY, agent_id=" ")
    with pytest.raises(ValueError, match="token symbols cannot be empty"):
        replace(main.DEMO_POLICY, allowed_token_symbols={"USDC", " "})


def test_trusted_token_rejects_invalid_metadata():
    from app.policy.models import TrustedToken

    with pytest.raises(ValueError, match="chain_id must be positive"):
        TrustedToken(chain_id=0, symbol="USDC", decimals=6)
    with pytest.raises(ValueError, match="symbol cannot be empty"):
        TrustedToken(chain_id=84532, symbol=" ", decimals=6)
    with pytest.raises(ValueError, match="decimals must be between 0 and 36"):
        TrustedToken(chain_id=84532, symbol="USDC", decimals=37)


def test_api_rejects_values_outside_sqlite_integer_range():
    oversized = 2**63
    amount_response = client.post(
        "/transactions/propose", headers=AGENT_HEADERS,
        json=_payload(amount_base_units=oversized),
    )
    assert amount_response.status_code == 422

    chain_response = client.post(
        "/transactions/propose", headers=AGENT_HEADERS,
        json=_payload(chain_id=oversized),
    )
    assert chain_response.status_code == 422

    evaluation = client.post(
        "/policy/evaluate",
        json={**_payload(), "daily_spent_base_units": oversized},
    )
    assert evaluation.status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chain_id", -1),
        ("chain_id", 0),
        ("token_decimals", -1),
        ("token_decimals", 37),
        ("amount_base_units", 0),
    ],
    ids=[
        "negative-chain-id",
        "zero-chain-id",
        "negative-token-decimals",
        "decimals-above-supported-range",
        "zero-amount",
    ],
)
def test_proposal_rejects_out_of_range_inputs(field, value):
    response = client.post(
        "/transactions/propose",
        headers=AGENT_HEADERS,
        json=_payload(**{field: value}),
    )

    assert response.status_code == 422


def test_spend_aggregation_does_not_overflow_sqlite_integer_range(tmp_path):
    recipient = "0x2222222222222222222222222222222222222222"
    amount = 2**62
    policy = replace(
        main.DEMO_POLICY,
        max_transaction_base_units=2**63 - 1,
        daily_limit_base_units=2**63 - 1,
        recipient_daily_limits_base_units={recipient: 2**63 - 1},
    )
    local_store = WorkflowStore(tmp_path / "large-spend.sqlite3")

    for index in range(2):
        payload = _payload(recipient=recipient, amount_base_units=amount)
        proposal = TransactionProposal(**payload)
        local_store.create({
            "transaction_id": f"large-reservation-{index}",
            "fingerprint": fingerprint_proposal(proposal),
            "proposal": payload,
            "policy_decision": "allow",
            "policy_reasons": ["test reservation"],
            "risk_score": 0,
            "risk_level": "low",
            "risk_reasons": [],
            "status": "ready",
        })

    controller = TransactionController(policy, local_store)
    outcome, record = controller.propose(
        TransactionProposal(**_payload(recipient=recipient, amount_base_units=1))
    )

    assert outcome == "created"
    assert record["status"] == "blocked"
    assert "Transaction would exceed the daily spending limit." in record["policy_reasons"]
    assert "Transaction would exceed the recipient daily spending limit." in record["policy_reasons"]


def test_policy_rejects_limits_outside_sqlite_integer_range():
    with pytest.raises(ValueError, match="must be positive"):
        replace(main.DEMO_POLICY, max_transaction_base_units=2**63)
    with pytest.raises(ValueError, match="SQLite integer range"):
        replace(
            main.DEMO_POLICY,
            recipient_daily_limits_base_units={
                "0x2222222222222222222222222222222222222222": 2**63
            },
        )



def test_idempotency_key_is_not_exposed_in_audit_event():
    idempotency_key = "opaque-client-idempotency-key-2026-10-10"
    headers = {**AGENT_HEADERS, "Idempotency-Key": idempotency_key}

    first = client.post("/transactions/propose", headers=headers, json=_payload())
    replay = client.post("/transactions/propose", headers=headers, json=_payload())

    assert first.status_code == 201
    assert replay.status_code == 200
    events = client.get(
        f"/transactions/{first.json()['transaction_id']}/audit",
        headers=OWNER_HEADERS,
    ).json()
    replay_event = next(event for event in events if event["event_type"] == "proposal_replayed")

    assert replay_event["details"] == {
        "idempotency_key_present": True,
        "policy_hash": hash_policy(main.DEMO_POLICY),
    }
    assert idempotency_key not in json.dumps(events)


def test_malformed_persisted_proposal_returns_sanitized_server_error():
    record = propose().json()
    secret_marker = "must-not-leak-from-corrupt-db"
    with sqlite3.connect(main.store.db_path) as db:
        db.execute(
            "UPDATE transactions SET proposal_json = ? WHERE transaction_id = ?",
            (json.dumps([secret_marker]), record["transaction_id"]),
        )

    response = client.get(
        f"/transactions/{record['transaction_id']}", headers=OWNER_HEADERS
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Stored workflow data is invalid"}
    assert secret_marker not in response.text


@pytest.mark.parametrize(
    ("column", "secret_marker"),
    [
        ("policy_reasons_json", "must-not-leak-from-policy-reasons"),
        ("risk_reasons_json", "must-not-leak-from-risk-reasons"),
    ],
    ids=["malformed-policy-reasons", "malformed-risk-reasons"],
)
def test_malformed_persisted_reason_data_returns_sanitized_server_error(
    column, secret_marker
):
    record = propose().json()
    with sqlite3.connect(main.store.db_path) as db:
        db.execute(
            f"UPDATE transactions SET {column} = ? WHERE transaction_id = ?",
            (json.dumps({"secret": secret_marker}), record["transaction_id"]),
        )

    response = client.get(
        f"/transactions/{record['transaction_id']}", headers=OWNER_HEADERS
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Stored workflow data is invalid"}
    assert secret_marker not in response.text


@pytest.mark.parametrize(
    ("column", "secret_marker"),
    [
        ("policy_reasons_json", "must-not-leak-from-policy-reason-item"),
        ("risk_reasons_json", "must-not-leak-from-risk-reason-item"),
    ],
    ids=["non-string-policy-reason", "non-string-risk-reason"],
)
def test_persisted_reason_lists_reject_non_string_items(column, secret_marker):
    record = propose().json()
    with sqlite3.connect(main.store.db_path) as db:
        db.execute(
            f"UPDATE transactions SET {column} = ? WHERE transaction_id = ?",
            (
                json.dumps(["valid reason", {"unexpected": secret_marker}]),
                record["transaction_id"],
            ),
        )

    response = client.get(
        f"/transactions/{record['transaction_id']}", headers=OWNER_HEADERS
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Stored workflow data is invalid"}
    assert secret_marker not in response.text


def test_malformed_persisted_audit_details_return_sanitized_server_error():
    record = propose().json()
    secret_marker = "must-not-leak-from-corrupt-audit"
    with sqlite3.connect(main.store.db_path) as db:
        # Simulate out-of-band database-file tampering, not an ordinary application update.
        db.execute("DROP TRIGGER audit_events_no_update")
        db.execute(
            "UPDATE audit_events SET details_json = ? WHERE transaction_id = ?",
            (json.dumps(secret_marker), record["transaction_id"]),
        )

    response = client.get(
        f"/transactions/{record['transaction_id']}/audit", headers=OWNER_HEADERS
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Stored workflow data is invalid"}
    assert secret_marker not in response.text

@pytest.mark.parametrize(
    ("column", "corrupt_value"),
    [
        ("event_type", sqlite3.Binary(b"corrupt-audit-event-type")),
        ("actor_role", sqlite3.Binary(b"corrupt-audit-actor")),
        ("from_status", sqlite3.Binary(b"corrupt-audit-status")),
        ("created_at", sqlite3.Binary(b"corrupt-audit-timestamp")),
    ],
    ids=["event-type", "actor-role", "from-status", "created-at"],
)
def test_invalid_persisted_audit_metadata_returns_sanitized_server_error(
    column, corrupt_value
):
    record = propose().json()
    with sqlite3.connect(main.store.db_path) as db:
        # Simulate out-of-band database-file tampering, not an ordinary application update.
        db.execute("DROP TRIGGER audit_events_no_update")
        db.execute(
            f"UPDATE audit_events SET {column} = ? WHERE transaction_id = ?",
            (corrupt_value, record["transaction_id"]),
        )

    response = client.get(
        f"/transactions/{record['transaction_id']}/audit", headers=OWNER_HEADERS
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Stored workflow data is invalid"}
    assert "corrupt-audit" not in response.text


@pytest.mark.parametrize(
    ("column", "invalid_value"),
    [
        ("event_type", "unknown-audit-event"),
        ("actor_role", "unrecognized-actor"),
        ("from_status", "unknown-source-state"),
        ("to_status", "unknown-destination-state"),
    ],
    ids=[
        "unknown-event-type",
        "unknown-actor-role",
        "unknown-source-state",
        "unknown-destination-state",
    ],
)
def test_unknown_persisted_audit_values_return_sanitized_server_error(
    column, invalid_value
):
    record = propose().json()
    with sqlite3.connect(main.store.db_path) as db:
        # Simulate out-of-band database-file tampering, not an ordinary application update.
        db.execute("DROP TRIGGER audit_events_no_update")
        db.execute(
            f"UPDATE audit_events SET {column} = ? WHERE transaction_id = ?",
            (invalid_value, record["transaction_id"]),
        )

    response = client.get(
        f"/transactions/{record['transaction_id']}/audit", headers=OWNER_HEADERS
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Stored workflow data is invalid"}
    assert invalid_value not in response.text





@pytest.mark.parametrize(
    "corrupt_amount",
    ["1000000", True, -1, 2**63],
    ids=["string", "boolean", "negative", "too-large"],
)
def test_persisted_proposal_rejects_invalid_integer_types_and_ranges(corrupt_amount):
    record = propose().json()
    proposal = record["proposal"]
    proposal["amount_base_units"] = corrupt_amount
    with sqlite3.connect(main.store.db_path) as db:
        db.execute(
            "UPDATE transactions SET proposal_json = ? WHERE transaction_id = ?",
            (json.dumps(proposal), record["transaction_id"]),
        )

    response = client.get(
        f"/transactions/{record['transaction_id']}", headers=OWNER_HEADERS
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Stored workflow data is invalid"}


@pytest.mark.parametrize(
    ("column", "corrupt_value"),
    [
        ("fingerprint", sqlite3.Binary(b"123")),
        ("approval_fingerprint", sqlite3.Binary(b"123")),
        ("approved_at", sqlite3.Binary(b"123")),
        ("execution_reference", sqlite3.Binary(b"123")),
        ("created_at", sqlite3.Binary(b"123")),
        ("updated_at", sqlite3.Binary(b"123")),
    ],
    ids=[
        "non-string-fingerprint",
        "non-string-approval-fingerprint",
        "non-string-approved-at",
        "non-string-execution-reference",
        "non-string-created-at",
        "non-string-updated-at",
    ],
)
def test_invalid_persisted_lifecycle_field_types_return_sanitized_server_error(
    column, corrupt_value
):
    record = propose().json()
    with sqlite3.connect(main.store.db_path) as db:
        db.execute(
            f"UPDATE transactions SET {column} = ? WHERE transaction_id = ?",
            (corrupt_value, record["transaction_id"]),
        )

    response = client.get(
        f"/transactions/{record['transaction_id']}", headers=OWNER_HEADERS
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Stored workflow data is invalid"}


def test_invalid_persisted_lifecycle_field_returns_sanitized_audit_error():
    record = propose().json()
    with sqlite3.connect(main.store.db_path) as db:
        db.execute(
            "UPDATE transactions SET fingerprint = ? WHERE transaction_id = ?",
            (sqlite3.Binary(b"123"), record["transaction_id"]),
        )

    response = client.get(
        f"/transactions/{record['transaction_id']}/audit",
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Stored workflow data is invalid"}


@pytest.mark.parametrize(
    ("column", "corrupt_value"),
    [
        ("status", "unknown-state"),
        ("policy_decision", "unknown-decision"),
        ("risk_level", "critical"),
        ("risk_score", -1),
        ("risk_score", 101),
        ("risk_score", "not-an-integer"),
    ],
    ids=[
        "unknown-status",
        "unknown-policy-decision",
        "unknown-risk-level",
        "negative-risk-score",
        "risk-score-above-100",
        "non-integer-risk-score",
    ],
)
def test_invalid_persisted_workflow_metadata_returns_sanitized_server_error(
    column, corrupt_value
):
    record = propose().json()
    with sqlite3.connect(main.store.db_path) as db:
        if column == "status":
            db.execute("DROP TRIGGER transactions_status_update_guard")
        db.execute(
            f"UPDATE transactions SET {column} = ? WHERE transaction_id = ?",
            (corrupt_value, record["transaction_id"]),
        )

    response = client.get(
        f"/transactions/{record['transaction_id']}", headers=OWNER_HEADERS
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Stored workflow data is invalid"}


def test_malformed_persisted_proposal_object_returns_sanitized_server_error():
    record = propose().json()
    with sqlite3.connect(main.store.db_path) as db:
        db.execute(
            "UPDATE transactions SET proposal_json = ? WHERE transaction_id = ?",
            (json.dumps({"recipient": "invalid-but-secret"}), record["transaction_id"]),
        )

    response = client.get(
        f"/transactions/{record['transaction_id']}", headers=OWNER_HEADERS
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Stored workflow data is invalid"}
    assert "invalid-but-secret" not in response.text


@pytest.mark.parametrize(
    ("column", "corrupt_value"),
    [
        ("chain_id", 1),
        ("token_address", "0x1111111111111111111111111111111111111111"),
        ("amount_base_units", 1),
    ],
    ids=["chain-id-mismatch", "token-address-mismatch", "amount-mismatch"],
)
def test_accounting_column_tampering_fails_closed_before_new_reservation(column, corrupt_value):
    record = propose(_payload(amount_base_units=3_000_000)).json()
    with sqlite3.connect(main.store.db_path) as db:
        db.execute(
            f"UPDATE transactions SET {column} = ? WHERE transaction_id = ?",
            (corrupt_value, record["transaction_id"]),
        )

    response = propose(_payload(amount_base_units=2_000_000))

    assert response.status_code == 500
    assert response.json() == {"detail": "Stored workflow data is invalid"}


def test_malformed_persisted_proposal_fails_closed_during_recipient_spend_check():
    record = propose().json()
    with sqlite3.connect(main.store.db_path) as db:
        db.execute(
            "UPDATE transactions SET proposal_json = ? WHERE transaction_id = ?",
            (json.dumps(["corrupt"]), record["transaction_id"]),
        )

    response = propose(_payload(amount_base_units=2_000_000))

    assert response.status_code == 500
    assert response.json() == {"detail": "Stored workflow data is invalid"}


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("get", "/transactions/{transaction_id}", {}),
        ("get", "/transactions/{transaction_id}/audit", {}),
        (
            "post",
            "/transactions/{transaction_id}/approve",
            {
                "json": {
                    "transaction_fingerprint": "a" * 64,
                    "confirmation": "APPROVE",
                }
            },
        ),
        ("post", "/transactions/{transaction_id}/execute", {}),
    ],
)
def test_agent_authentication_cannot_access_owner_only_transaction_endpoints(
    method, path, kwargs
):
    record = propose().json()
    url = path.format(transaction_id=record["transaction_id"])

    response = getattr(client, method)(url, headers=AGENT_HEADERS, **kwargs)

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("get", "/transactions/{transaction_id}", {}),
        ("get", "/transactions/{transaction_id}/audit", {}),
        (
            "post",
            "/transactions/{transaction_id}/approve",
            {
                "json": {
                    "transaction_fingerprint": "a" * 64,
                    "confirmation": "APPROVE",
                }
            },
        ),
        ("post", "/transactions/{transaction_id}/execute", {}),
    ],
)
@pytest.mark.parametrize(
    "headers",
    [None, {"Authorization": "Bearer invalid-owner-token"}],
    ids=["missing-credentials", "invalid-credentials"],
)
def test_owner_only_transaction_endpoints_reject_missing_or_invalid_authentication(
    method, path, kwargs, headers
):
    record = propose().json()
    url = path.format(transaction_id=record["transaction_id"])

    response = getattr(client, method)(url, headers=headers, **kwargs)

    assert response.status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/chain/base-sepolia/status",
        "/chain/base-sepolia/token/0x1111111111111111111111111111111111111111",
    ],
)
@pytest.mark.parametrize(
    "headers",
    [None, AGENT_HEADERS],
    ids=["missing-credentials", "agent-credentials"],
)
def test_read_only_chain_endpoints_require_owner_authentication(path, headers):
    response = client.get(path, headers=headers)

    assert response.status_code == 401


def test_chain_status_maps_missing_rpc_configuration_to_503(monkeypatch):
    from app.chain import router as chain_router

    def unavailable_rpc():
        raise chain_router.RpcConfigurationError("RPC is not configured")

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", unavailable_rpc)

    response = client.get("/chain/base-sepolia/status", headers=OWNER_HEADERS)

    assert response.status_code == 503
    assert response.json() == {"detail": "RPC is not configured"}


def test_chain_status_maps_rpc_failure_to_502(monkeypatch):
    from app.chain import router as chain_router

    def failing_rpc():
        raise chain_router.RpcRequestError("RPC request failed")

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", failing_rpc)

    response = client.get("/chain/base-sepolia/status", headers=OWNER_HEADERS)

    assert response.status_code == 502
    assert response.json() == {"detail": "RPC request failed"}


def test_chain_token_endpoint_rejects_invalid_address_before_rpc(monkeypatch):
    from app.chain import router as chain_router

    def unexpected_rpc():
        raise AssertionError("RPC client must not be created for an invalid address")

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", unexpected_rpc)

    response = client.get(
        "/chain/base-sepolia/token/not-an-address",
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Address must be a 20-byte hexadecimal EVM address"}


def test_chain_token_endpoint_maps_missing_rpc_configuration_to_503(monkeypatch):
    from app.chain import router as chain_router

    def unavailable_rpc():
        raise chain_router.RpcConfigurationError("RPC is not configured")

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", unavailable_rpc)

    response = client.get(
        f"/chain/base-sepolia/token/{BASE_SEPOLIA_USDC_ADDRESS}",
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "RPC is not configured"}


def test_chain_token_endpoint_maps_rpc_failure_to_502(monkeypatch):
    from app.chain import router as chain_router

    def failing_rpc():
        raise chain_router.RpcRequestError("RPC request failed")

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", failing_rpc)

    response = client.get(
        f"/chain/base-sepolia/token/{BASE_SEPOLIA_USDC_ADDRESS}",
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "RPC request failed"}



def test_sqlite_operational_error_returns_sanitized_503_on_transaction_read(monkeypatch):
    def unavailable(_transaction_id):
        raise sqlite3.OperationalError("unable to open database file: /sensitive/path")

    monkeypatch.setattr(main.store, "get", unavailable)

    response = client.get("/transactions/missing", headers=OWNER_HEADERS)

    assert response.status_code == 503
    assert response.json() == {"detail": "Workflow database is unavailable"}
    assert "/sensitive/path" not in response.text


def test_sqlite_operational_error_returns_sanitized_503_on_proposal(monkeypatch):
    def unavailable(_proposal, _idempotency_key):
        raise sqlite3.OperationalError("database is locked: /sensitive/path")

    monkeypatch.setattr(main.controller, "propose", unavailable)

    response = propose()

    assert response.status_code == 503
    assert response.json() == {"detail": "Workflow database is unavailable"}
    assert "/sensitive/path" not in response.text



def test_execution_blocks_when_policy_changes_after_proposal():
    record = propose().json()
    assert record["status"] == "ready"

    # A policy change after proposal creation must be enforced at execution time.
    main.controller.policy = replace(main.DEMO_POLICY, allowed_chain_ids={1})

    response = owner_post(f"/transactions/{record['transaction_id']}/execute")

    assert response.status_code == 403
    assert response.json()["detail"] == "Policy-blocked transactions cannot execute"

    refreshed = client.get(
        f"/transactions/{record['transaction_id']}", headers=OWNER_HEADERS
    ).json()
    assert refreshed["status"] == "blocked"
    assert refreshed["policy_decision"] == "block"

    events = client.get(
        f"/transactions/{record['transaction_id']}/audit", headers=OWNER_HEADERS
    ).json()
    assert [event["event_type"] for event in events][-1] == "policy_revalidation_blocked"


def test_execution_requires_new_approval_when_policy_tightens_after_proposal():
    record = propose().json()
    assert record["status"] == "ready"

    # Removing the recipient from the allowlist changes this proposal from allowed
    # to approval-required. Execution must not use the stale "ready" decision.
    main.controller.policy = replace(
        main.DEMO_POLICY,
        allowed_recipients=set(),
        recipient_token_allowlist={},
    )

    response = owner_post(f"/transactions/{record['transaction_id']}/execute")

    assert response.status_code == 409
    assert response.json()["detail"] == "Human approval is required before execution"

    refreshed = client.get(
        f"/transactions/{record['transaction_id']}", headers=OWNER_HEADERS
    ).json()
    assert refreshed["status"] == "awaiting_approval"
    assert refreshed["policy_decision"] == "require_approval"

    events = client.get(
        f"/transactions/{record['transaction_id']}/audit", headers=OWNER_HEADERS
    ).json()
    assert [event["event_type"] for event in events][-1] == "policy_revalidation_requires_approval"


def test_policy_config_requires_owner_authentication():
    response = client.get("/policy/config")
    assert response.status_code == 401


def test_policy_config_returns_read_only_active_limits_and_allowlists():
    response = client.get("/policy/config", headers=OWNER_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "agent_id": main.DEMO_POLICY.agent_id,
        "enabled": main.DEMO_POLICY.enabled,
        "allowed_chain_ids": sorted(main.DEMO_POLICY.allowed_chain_ids),
        "allowed_tokens": [
            {
                "address": address.lower(),
                "symbol": token.symbol,
                "chain_id": token.chain_id,
                "decimals": token.decimals,
            }
            for address, token in sorted(
                main.DEMO_POLICY.trusted_tokens.items(),
                key=lambda item: item[0].lower(),
            )
            if address.lower()
            in {allowed.lower() for allowed in main.DEMO_POLICY.allowed_token_addresses}
        ],
        "allowed_token_symbols": sorted(main.DEMO_POLICY.allowed_token_symbols),
        "allowed_recipients": sorted(
            address.lower() for address in main.DEMO_POLICY.allowed_recipients
        ),
        "max_transaction_base_units": main.DEMO_POLICY.max_transaction_base_units,
        "daily_limit_base_units": main.DEMO_POLICY.daily_limit_base_units,
        "approval_threshold_base_units": main.DEMO_POLICY.approval_threshold_base_units,
        "approval_expires_seconds": main.DEMO_POLICY.approval_expires_seconds,
        "unknown_recipient_requires_approval": (
            main.DEMO_POLICY.unknown_recipient_requires_approval
        ),
        "recipient_token_allowlist": {
            recipient.lower(): sorted(token.lower() for token in tokens)
            for recipient, tokens in sorted(
                main.DEMO_POLICY.recipient_token_allowlist.items(),
                key=lambda item: item[0].lower(),
            )
        },
        "recipient_daily_limits_base_units": {
            recipient.lower(): limit
            for recipient, limit in sorted(
                main.DEMO_POLICY.recipient_daily_limits_base_units.items(),
                key=lambda item: item[0].lower(),
            )
        },
        "recipient_transaction_limits_base_units": {
            recipient.lower(): limit
            for recipient, limit in sorted(
                main.DEMO_POLICY.recipient_transaction_limits_base_units.items(),
                key=lambda item: item[0].lower(),
            )
        },
    }
    assert "AEGIS_AGENT_TOKEN" not in response.text
    assert "AEGIS_APPROVAL_TOKEN" not in response.text


def test_spending_summary_requires_owner_authentication():
    response = client.get("/spending/summary")
    assert response.status_code == 401


def test_spending_summary_reports_daily_reservations_and_policy_limits():
    proposal = propose()
    assert proposal.status_code == 201

    response = client.get("/spending/summary", headers=OWNER_HEADERS)
    assert response.status_code == 200
    body = response.json()

    assert body["accounting_source"] == "local_workflow"
    assert body["as_of_date_utc"] == datetime.now(UTC).date().isoformat()
    token = next(
        item for item in body["tokens"]
        if item["token_address"] == BASE_SEPOLIA_USDC_ADDRESS.lower()
    )
    assert token == {
        "chain_id": main.DEMO_POLICY.trusted_tokens[BASE_SEPOLIA_USDC_ADDRESS].chain_id,
        "token_address": BASE_SEPOLIA_USDC_ADDRESS.lower(),
        "symbol": "USDC",
        "decimals": 6,
        "spent_base_units": 1_000_000,
        "daily_limit_base_units": main.DEMO_POLICY.daily_limit_base_units,
        "remaining_base_units": max(0, main.DEMO_POLICY.daily_limit_base_units - 1_000_000),
        "limit_exceeded": False,
    }


def test_spending_summary_releases_cancelled_reservations():
    proposal = propose()
    assert proposal.status_code == 201
    transaction_id = proposal.json()["transaction_id"]

    cancelled = owner_post(f"/transactions/{transaction_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    response = client.get("/spending/summary", headers=OWNER_HEADERS)
    assert response.status_code == 200
    token = next(
        item for item in response.json()["tokens"]
        if item["token_address"] == BASE_SEPOLIA_USDC_ADDRESS.lower()
    )
    assert token["spent_base_units"] == 0



def test_approved_transaction_exposes_backend_derived_expiry_timestamp(monkeypatch):
    now = [datetime(2026, 10, 20, 12, 0, tzinfo=UTC)]
    monkeypatch.setattr(store_module, "_utc_now", lambda: now[0])
    record = propose(_payload(recipient="0x4444444444444444444444444444444444444444")).json()
    assert record["status"] == "awaiting_approval"
    approval_body = {
        "transaction_fingerprint": record["fingerprint"],
        "confirmation": "APPROVE",
    }

    approved = owner_post(f"/transactions/{record['transaction_id']}/approve", json=approval_body)
    assert approved.status_code == 200
    expiry = now[0] + timedelta(seconds=main.DEMO_POLICY.approval_expires_seconds)
    assert approved.json()["approval_expires_at"] == expiry.isoformat()

    listed = client.get("/transactions", headers=OWNER_HEADERS)
    assert listed.status_code == 200
    listed_record = next(item for item in listed.json() if item["transaction_id"] == record["transaction_id"])
    assert listed_record["approval_expires_at"] == expiry.isoformat()

    # The deadline is an absolute backend-derived timestamp, not a browser estimate.
    now[0] = expiry
    refreshed = client.get(f"/transactions/{record['transaction_id']}", headers=OWNER_HEADERS)
    assert refreshed.status_code == 200
    assert refreshed.json()["approval_expires_at"] == expiry.isoformat()
