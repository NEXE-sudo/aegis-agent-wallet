from dataclasses import replace

from app import main
from app.chain.base_sepolia import BASE_SEPOLIA_USDC_ADDRESS
from app.policy.models import TransactionProposal
from app.workflow.controller import TransactionController
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
               SET created_at = date('now', '-1 day') || ' 23:59:59',
                   created_day = date('now', '-1 day')
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


def _proposal(amount: int, recipient: str = RECIPIENT) -> TransactionProposal:
    return TransactionProposal(
        agent_id="devops-01",
        chain_id=CHAIN_ID,
        token_symbol="USDC",
        token_address=BASE_SEPOLIA_USDC_ADDRESS,
        recipient=recipient,
        amount_base_units=amount,
        token_decimals=6,
    )


def _backdate_creation(store: WorkflowStore, transaction_id: str) -> None:
    with store._connect() as db:
        db.execute(
            """UPDATE transactions
               SET created_at = date('now', '-1 day') || ' 23:59:59',
                   created_day = date('now', '-1 day')
               WHERE transaction_id = ?""",
            (transaction_id,),
        )


def _approve_and_execute(
    controller: TransactionController, record: dict
) -> tuple[str, dict | None]:
    if record["status"] == "awaiting_approval":
        outcome, approved = controller.approve(record["transaction_id"], record["fingerprint"])
        assert outcome == "approved"
        assert approved is not None
    return controller.execute(record["transaction_id"])


def test_execution_today_counts_against_daily_limit_when_proposal_is_from_yesterday(tmp_path):
    store = WorkflowStore(tmp_path / "execution-day-global-limit.sqlite3")
    controller = TransactionController(main.DEMO_POLICY, store)

    outcome, stale = controller.propose(_proposal(40_000_000))
    assert outcome == "created"
    _backdate_creation(store, stale["transaction_id"])

    outcome, executed = controller.execute(stale["transaction_id"])
    assert outcome == "executed"
    assert executed is not None
    assert executed["executed_at"] is not None
    with store._connect() as db:
        assert db.execute(
            "SELECT date(executed_at) = date('now') FROM transactions WHERE transaction_id = ?",
            (stale["transaction_id"],),
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT executed_day = date('now') FROM transactions WHERE transaction_id = ?",
            (stale["transaction_id"],),
        ).fetchone()[0] == 1

    outcomes = []
    for index in range(3):
        outcome, record = controller.propose(_proposal(50_000_000))
        assert outcome == "created"
        if record["status"] == "blocked":
            outcomes.append("blocked")
            continue
        outcome, result = _approve_and_execute(controller, record)
        outcomes.append(outcome)
        if outcome == "executed":
            assert result is not None
            assert result["status"] == "executed_simulated"

    assert outcomes == ["executed", "executed", "blocked"]
    assert store.daily_spend(CHAIN_ID, BASE_SEPOLIA_USDC_ADDRESS) == 140_000_000


def test_execution_today_counts_against_recipient_daily_limit_when_proposal_is_from_yesterday(
    tmp_path,
):
    policy = replace(
        main.DEMO_POLICY,
        recipient_daily_limits_base_units={RECIPIENT: 70_000_000},
    )
    store = WorkflowStore(tmp_path / "execution-day-recipient-limit.sqlite3")
    controller = TransactionController(policy, store)

    outcome, stale = controller.propose(_proposal(40_000_000))
    assert outcome == "created"
    _backdate_creation(store, stale["transaction_id"])
    outcome, executed = controller.execute(stale["transaction_id"])
    assert outcome == "executed"
    assert executed is not None
    assert executed["status"] == "executed_simulated"

    outcome, next_record = controller.propose(_proposal(40_000_000))
    assert outcome == "created"
    assert next_record["status"] == "blocked"
    assert "recipient daily spending limit" in " ".join(next_record["policy_reasons"]).lower()



def test_daily_spend_queries_use_targeted_indexes(tmp_path):
    store = WorkflowStore(tmp_path / "indexed-spend.sqlite3")
    with store._connect() as db:
        plans = {
            "global_reservations": db.execute(
                """EXPLAIN QUERY PLAN SELECT amount_base_units FROM transactions
                   WHERE chain_id = ? AND token_address = ? AND created_day = ?
                   AND status IN ('ready', 'awaiting_approval', 'approved')""",
                (CHAIN_ID, BASE_SEPOLIA_USDC_ADDRESS, "2026-10-11"),
            ).fetchall(),
            "recipient_reservations": db.execute(
                """EXPLAIN QUERY PLAN SELECT amount_base_units FROM transactions
                   WHERE chain_id = ? AND token_address = ? AND recipient_normalized = ?
                   AND created_day = ? AND status IN ('ready', 'awaiting_approval', 'approved')""",
                (CHAIN_ID, BASE_SEPOLIA_USDC_ADDRESS, RECIPIENT.lower(), "2026-10-11"),
            ).fetchall(),
            "global_executions": db.execute(
                """EXPLAIN QUERY PLAN SELECT amount_base_units FROM transactions
                   WHERE chain_id = ? AND token_address = ? AND executed_day = ?
                   AND status = 'executed_simulated'""",
                (CHAIN_ID, BASE_SEPOLIA_USDC_ADDRESS, "2026-10-11"),
            ).fetchall(),
            "recipient_executions": db.execute(
                """EXPLAIN QUERY PLAN SELECT amount_base_units FROM transactions
                   WHERE chain_id = ? AND token_address = ? AND recipient_normalized = ?
                   AND executed_day = ? AND status = 'executed_simulated'""",
                (CHAIN_ID, BASE_SEPOLIA_USDC_ADDRESS, RECIPIENT.lower(), "2026-10-11"),
            ).fetchall(),
        }

    expected_indexes = {
        "global_reservations": "idx_transactions_daily_spend",
        "recipient_reservations": "idx_transactions_recipient_daily_spend",
        "global_executions": "idx_transactions_execution_day",
        "recipient_executions": "idx_transactions_recipient_execution_day",
    }
    for query_name, index_name in expected_indexes.items():
        details = " ".join(row["detail"] for row in plans[query_name])
        assert index_name in details, details
        assert "SCAN transactions" not in details, details
