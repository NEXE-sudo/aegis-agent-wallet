import pytest
from fastapi.testclient import TestClient

from app import main
from app.chain.base_sepolia import BASE_SEPOLIA_USDC_ADDRESS
from app.workflow.controller import TransactionController
from app.workflow.store import WorkflowStore

AGENT_HEADERS = {"Authorization": "Bearer demo-agent-token"}
OWNER_HEADERS = {"Authorization": "Bearer demo-owner-token"}
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def isolated_workflow(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_AGENT_TOKEN", "demo-agent-token")
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", "demo-owner-token")
    local_store = WorkflowStore(tmp_path / "demo-workflow.sqlite3")
    monkeypatch.setattr(main, "store", local_store)
    monkeypatch.setattr(
        main, "controller", TransactionController(main.DEMO_POLICY, local_store)
    )


def test_documented_unknown_recipient_requires_approval_before_execution():
    proposal = {
        "agent_id": "devops-01",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": BASE_SEPOLIA_USDC_ADDRESS,
        "recipient": "0x4444444444444444444444444444444444444444",
        "amount_base_units": 1_000_000,
        "token_decimals": 6,
    }

    proposed = client.post(
        "/transactions/propose",
        headers=AGENT_HEADERS,
        json=proposal,
    )

    assert proposed.status_code == 201
    record = proposed.json()
    assert record["status"] == "awaiting_approval"

    executed = client.post(
        f"/transactions/{record['transaction_id']}/execute",
        headers=OWNER_HEADERS,
    )

    assert executed.status_code == 409
    assert executed.json() == {
        "detail": "Human approval is required before execution"
    }
