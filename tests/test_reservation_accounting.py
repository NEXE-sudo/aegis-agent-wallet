from app.chain.base_sepolia import BASE_SEPOLIA_USDC_ADDRESS
from app.workflow.store import WorkflowStore

CHAIN_ID = 84532
RECIPIENT = "0x2222222222222222222222222222222222222222"


def _record(transaction_id: str, amount: int, status: str) -> dict:
    proposal = {
        "agent_id": "devops-01",
        "chain_id": CHAIN_ID,
        "token_symbol": "USDC",
        "token_address": BASE_SEPOLIA_USDC_ADDRESS,
        "recipient": RECIPIENT,
        "amount_base_units": amount,
        "token_decimals": 6,
    }
    return {
        "transaction_id": transaction_id,
        "fingerprint": transaction_id,
        "proposal": proposal,
        "policy_decision": "allow",
        "policy_reasons": [],
        "risk_score": 0,
        "risk_level": "low",
        "risk_reasons": [],
        "status": status,
    }


def test_daily_reservations_cover_workflow_states_and_utc_day_boundary(tmp_path):
    store = WorkflowStore(tmp_path / "reservation-accounting.sqlite3")
    records = [
        _record("blocked", 100, "blocked"),
        _record("ready", 10, "ready"),
        _record("awaiting-approval", 20, "awaiting_approval"),
        _record("approved", 30, "approved"),
        _record("executed", 40, "executed_simulated"),
        _record("expired-approval", 50, "approved"),
        _record("yesterday", 1_000, "ready"),
    ]
    for record in records:
        store.create(record)

    # Model the persisted state after an approval expires: the reservation
    # remains active while the transaction awaits a fresh approval.
    with store._connect() as db:
        db.execute(
            """UPDATE transactions
               SET status = 'awaiting_approval', approval_fingerprint = NULL,
                   approved_at = NULL
               WHERE transaction_id = 'expired-approval'"""
        )
        # SQLite CURRENT_TIMESTAMP is UTC. A record created yesterday must not
        # count against today's daily budget even if its workflow status reserves spend.
        db.execute(
            """UPDATE transactions
               SET created_at = date('now', '-1 day') || ' 23:59:59'
               WHERE transaction_id = 'yesterday'"""
        )
        recipient_spend = store._recipient_daily_spend(
            db, CHAIN_ID, BASE_SEPOLIA_USDC_ADDRESS, RECIPIENT
        )

    expected_today = 10 + 20 + 30 + 40 + 50
    assert store.daily_spend(CHAIN_ID, BASE_SEPOLIA_USDC_ADDRESS) == expected_today
    assert recipient_spend == expected_today
    assert store.get("expired-approval")["status"] == "awaiting_approval"
    assert store.get("expired-approval")["approved_at"] is None
