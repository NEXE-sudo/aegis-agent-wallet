"""Base Sepolia evidence verification uses mocked JSON-RPC only."""
import json
import sqlite3

import httpx
import pytest

from app.chain.base_sepolia import (
    BASE_SEPOLIA_USDC_ADDRESS,
    BaseSepoliaRpc,
    RpcRequestError,
)
from scripts import verify_testnet_evidence as evidence_script

TRANSACTION_HASH = "0x" + "a" * 64
OTHER_TRANSACTION_HASH = "0x" + "b" * 64
SENDER = "0x1111111111111111111111111111111111111111"
RECIPIENT = "0x2222222222222222222222222222222222222222"
OTHER_RECIPIENT = "0x3333333333333333333333333333333333333333"
AMOUNT_BASE_UNITS = 2_500_000
TRANSACTION_ID = "txn-test-evidence"
_USE_DEFAULT_TRANSACTION = object()


def _transaction(calldata=None, sender=SENDER):
    calldata = calldata or (
        "0xa9059cbb"
        + ("0" * 24)
        + RECIPIENT[2:]
        + f"{AMOUNT_BASE_UNITS:064x}"
    )
    return {
        "hash": TRANSACTION_HASH,
        "from": sender,
        "to": BASE_SEPOLIA_USDC_ADDRESS,
        "input": calldata,
    }


def _rpc(method_results):
    def handler(request):
        payload = json.loads(request.content)
        result = method_results[payload["method"]]
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result}
        )

    return BaseSepoliaRpc(
        "https://rpc.invalid",
        transport=httpx.MockTransport(handler),
    )


def _results(
    *,
    chain_id="0x14a34",
    transaction=_USE_DEFAULT_TRANSACTION,
    receipt_status="0x1",
    receipt=True,
):
    return {
        "eth_chainId": chain_id,
        "eth_getTransactionByHash": (
            _transaction() if transaction is _USE_DEFAULT_TRANSACTION else transaction
        ),
        "eth_getTransactionReceipt": (
            {
                "transactionHash": TRANSACTION_HASH,
                "status": receipt_status,
                "blockNumber": "0x123",
            }
            if receipt
            else None
        ),
    }


def test_verifies_successful_base_sepolia_transfer():
    with _rpc(_results()) as rpc:
        evidence = evidence_script.verify_evidence(TRANSACTION_HASH, rpc)

    assert evidence == {
        "chain_id": 84532,
        "receipt_status": "success",
        "block_number": 0x123,
        "token_contract": BASE_SEPOLIA_USDC_ADDRESS,
        "sender": SENDER,
        "recipient": RECIPIENT,
        "amount_base_units": AMOUNT_BASE_UNITS,
        "transaction_hash": TRANSACTION_HASH,
        "explorer_url": f"https://sepolia.basescan.org/tx/{TRANSACTION_HASH}",
    }


def test_reports_failed_receipt_for_manual_failure_review():
    with _rpc(_results(receipt_status="0x0")) as rpc:
        evidence = evidence_script.verify_evidence(TRANSACTION_HASH, rpc)

    assert evidence["receipt_status"] == "failed"


def test_cli_reports_failed_receipt_as_failure(monkeypatch, capsys):
    class MockRpc:
        def __enter__(self):
            self.rpc = _rpc(_results(receipt_status="0x0"))
            return self.rpc

        def __exit__(self, *_):
            self.rpc.close()

    monkeypatch.setattr(evidence_script, "BaseSepoliaRpc", MockRpc)
    assert evidence_script.main(
        [TRANSACTION_HASH, "--expected-sender", SENDER]
    ) == 1
    captured = capsys.readouterr()
    assert "Receipt status: failed" in captured.out
    assert "receipt status is not success" in captured.err


def test_cli_prints_transfer_evidence(monkeypatch, capsys):
    class MockRpc:
        def __enter__(self):
            self.rpc = _rpc(_results())
            return self.rpc

        def __exit__(self, *_):
            self.rpc.close()

    monkeypatch.setattr(evidence_script, "BaseSepoliaRpc", MockRpc)
    assert evidence_script.main(
        [TRANSACTION_HASH, "--expected-sender", SENDER]
    ) == 0
    captured = capsys.readouterr()
    assert "Chain ID: 84532 (Base Sepolia; expected 84532)" in captured.out
    assert f"Sender: {SENDER}" in captured.out
    assert "Receipt status: success" in captured.out
    assert "Block number: 291" in captured.out
    assert f"Token contract: {BASE_SEPOLIA_USDC_ADDRESS}" in captured.out
    assert f"Recipient: {RECIPIENT}" in captured.out
    assert f"Amount (base units): {AMOUNT_BASE_UNITS}" in captured.out
    assert f"https://sepolia.basescan.org/tx/{TRANSACTION_HASH}" in captured.out
    assert "Audit cross-check: not performed" in captured.out


def _patch_rpc(monkeypatch, results):
    class MockRpc:
        def __enter__(self):
            self.rpc = _rpc(results)
            return self.rpc

        def __exit__(self, *_):
            self.rpc.close()

    monkeypatch.setattr(evidence_script, "BaseSepoliaRpc", MockRpc)


def test_rpc_rejects_malformed_sender_address():
    with (
        _rpc(_results(transaction=_transaction(sender="not-an-address"))) as rpc,
        pytest.raises(RpcRequestError, match="malformed transaction"),
    ):
        evidence_script.verify_evidence(TRANSACTION_HASH, rpc)


def test_rpc_normalizes_sender_address():
    uppercase_sender = "0x" + SENDER[2:].upper()
    with _rpc(_results(transaction=_transaction(sender=uppercase_sender))) as rpc:
        evidence = evidence_script.verify_evidence(TRANSACTION_HASH, rpc)
    assert evidence["sender"] == SENDER


def test_cli_compares_expected_sender_case_insensitively(monkeypatch, capsys):
    _patch_rpc(monkeypatch, _results())
    expected_sender = "0x" + SENDER[2:].upper()
    assert evidence_script.main(
        [TRANSACTION_HASH, "--expected-sender", expected_sender]
    ) == 0
    assert f"Sender: {SENDER}" in capsys.readouterr().out


def test_cli_rejects_wrong_expected_sender(monkeypatch, capsys):
    _patch_rpc(monkeypatch, _results())
    assert evidence_script.main(
        [TRANSACTION_HASH, "--expected-sender", OTHER_RECIPIENT]
    ) == 1
    assert "does not match --expected-sender" in capsys.readouterr().err


def test_cli_requires_sender_choice(monkeypatch):
    _patch_rpc(monkeypatch, _results())
    with pytest.raises(SystemExit) as exc_info:
        evidence_script.main([TRANSACTION_HASH])
    assert exc_info.value.code == 2


def test_cli_warns_when_sender_check_is_skipped(monkeypatch, capsys):
    _patch_rpc(monkeypatch, _results())
    assert evidence_script.main(
        [TRANSACTION_HASH, "--skip-sender-check"]
    ) == 0
    assert "WARNING: Sender check was explicitly skipped" in capsys.readouterr().out


def _create_audit_database(
    path,
    *,
    execution_reference=TRANSACTION_HASH,
    status="submitted_onchain",
    recipient=RECIPIENT,
    amount=AMOUNT_BASE_UNITS,
    missing_event=None,
    approved=False,
    out_of_order=False,
):
    proposal = {
        "agent_id": "evidence-test-agent",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": BASE_SEPOLIA_USDC_ADDRESS,
        "token_decimals": 6,
        "recipient": recipient,
        "amount_base_units": amount,
    }
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE transactions (
                transaction_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                execution_reference TEXT,
                proposal_json TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                token_address TEXT NOT NULL,
                recipient_normalized TEXT NOT NULL,
                amount_base_units INTEGER NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE audit_events (
                event_id INTEGER PRIMARY KEY,
                transaction_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT,
                details_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            """INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                TRANSACTION_ID,
                status,
                execution_reference,
                json.dumps(proposal),
                84532,
                BASE_SEPOLIA_USDC_ADDRESS,
                recipient.lower(),
                amount,
            ),
        )
        events = [
            (
                1,
                "proposal_created",
                "agent",
                None,
                "awaiting_approval" if approved else "ready",
                {},
            ),
        ]
        if approved:
            events.append(
                (2, "approval_granted", "owner", "awaiting_approval", "approved", {})
            )
        signed_id = len(events) + 1
        signed_from = "approved" if approved else "ready"
        events.extend(
            [
                (
                    signed_id,
                    "onchain_transaction_signed",
                    "owner",
                    signed_from,
                    "submitting_onchain",
                    {"transaction_hash": TRANSACTION_HASH},
                ),
                (
                    signed_id + 1,
                    "onchain_transaction_submitted",
                    "owner",
                    "submitting_onchain",
                    "submitted_onchain",
                    {"transaction_hash": TRANSACTION_HASH},
                ),
            ]
        )
        if out_of_order:
            events = [
                (
                    signed_id if event_type == "onchain_transaction_submitted"
                    else signed_id + 1 if event_type == "onchain_transaction_signed"
                    else event_id,
                    event_type,
                    actor,
                    from_status,
                    to_status,
                    details,
                )
                for event_id, event_type, actor, from_status, to_status, details in events
            ]
        for event_id, event_type, actor, from_status, to_status, details in events:
            if event_type == missing_event:
                continue
            connection.execute(
                """INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    TRANSACTION_ID,
                    event_type,
                    actor,
                    from_status,
                    to_status,
                    json.dumps(details),
                ),
            )


def _run_cli_with_audit_db(monkeypatch, database_path):
    _patch_rpc(monkeypatch, _results())
    return evidence_script.main(
        [
            TRANSACTION_HASH,
            "--expected-sender",
            SENDER,
            "--audit-db",
            str(database_path),
        ]
    )


def test_audit_cross_check_matches_aegis_record_read_only(
    monkeypatch, capsys, tmp_path
):
    database_path = tmp_path / "workflow.sqlite3"
    _create_audit_database(database_path)
    before = database_path.read_bytes()

    assert _run_cli_with_audit_db(monkeypatch, database_path) == 0

    captured = capsys.readouterr()
    assert f"Audit cross-check: OK (transaction {TRANSACTION_ID})" in captured.out
    assert database_path.read_bytes() == before


def test_audit_cross_check_accepts_owner_approved_execution(monkeypatch, capsys, tmp_path):
    database_path = tmp_path / "workflow.sqlite3"
    _create_audit_database(database_path, approved=True)

    assert _run_cli_with_audit_db(monkeypatch, database_path) == 0
    assert f"Audit cross-check: OK (transaction {TRANSACTION_ID})" in capsys.readouterr().out


def test_audit_cross_check_fails_when_hash_has_no_record(
    monkeypatch, capsys, tmp_path
):
    database_path = tmp_path / "workflow.sqlite3"
    _create_audit_database(database_path, execution_reference=OTHER_TRANSACTION_HASH)
    before = database_path.read_bytes()

    assert _run_cli_with_audit_db(monkeypatch, database_path) == 1
    assert "No Aegis transaction record" in capsys.readouterr().err
    assert database_path.read_bytes() == before


@pytest.mark.parametrize(
    ("record_options", "message"),
    [
        ({"recipient": OTHER_RECIPIENT}, "does not match the on-chain transfer"),
        ({"amount": AMOUNT_BASE_UNITS + 1}, "does not match the on-chain transfer"),
        ({"status": "executed_simulated"}, "does not match the on-chain transfer"),
        (
            {"missing_event": "onchain_transaction_submitted"},
            "missing expected events in order",
        ),
        ({"out_of_order": True}, "missing expected events in order"),
    ],
)
def test_audit_cross_check_rejects_inconsistent_record(
    monkeypatch, capsys, tmp_path, record_options, message
):
    database_path = tmp_path / "workflow.sqlite3"
    _create_audit_database(database_path, **record_options)
    before = database_path.read_bytes()

    assert _run_cli_with_audit_db(monkeypatch, database_path) == 1
    assert message in capsys.readouterr().err
    assert database_path.read_bytes() == before


def test_rejects_rpc_endpoint_on_wrong_chain():
    with _rpc(_results(chain_id="0x1")) as rpc, pytest.raises(
        ValueError, match="not Base Sepolia"
    ):
        evidence_script.verify_evidence(TRANSACTION_HASH, rpc)


def test_rejects_transaction_not_found():
    with _rpc(_results(transaction=None)) as rpc, pytest.raises(
        ValueError, match="Transaction was not found"
    ):
        evidence_script.verify_evidence(TRANSACTION_HASH, rpc)


def test_rejects_non_transfer_calldata():
    with _rpc(
        _results(transaction=_transaction("0x095ea7b3" + ("0" * 128)))
    ) as rpc, pytest.raises(ValueError, match="not an ERC-20 transfer"):
        evidence_script.verify_evidence(TRANSACTION_HASH, rpc)


def test_rejects_missing_receipt():
    with _rpc(_results(receipt=False)) as rpc, pytest.raises(
        ValueError, match="receipt is not available"
    ):
        evidence_script.verify_evidence(TRANSACTION_HASH, rpc)
