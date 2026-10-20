"""Base Sepolia evidence verification uses mocked JSON-RPC only."""
import hashlib
import json
import sqlite3

import httpx
import pytest
from eth_utils import keccak

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
VAULT = "0x4444444444444444444444444444444444444444"
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


def _word(value):
    return value.to_bytes(32, "big").hex()


def _audit_proposal(recipient=RECIPIENT, amount=AMOUNT_BASE_UNITS):
    return {
        "agent_id": "evidence-test-agent",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": BASE_SEPOLIA_USDC_ADDRESS,
        "token_decimals": 6,
        "recipient": recipient,
        "amount_base_units": amount,
    }


def _fingerprint(proposal):
    canonical = json.dumps(proposal, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _address_topic(address):
    return "0x" + address[2:].lower().rjust(64, "0")


def _vault_calldata(
    *,
    token=BASE_SEPOLIA_USDC_ADDRESS,
    recipient=RECIPIENT,
    amount=AMOUNT_BASE_UNITS,
    reference=None,
):
    reference = reference or "0x" + _fingerprint(_audit_proposal(recipient, amount))
    return (
        "0x" + keccak(text="transferToken(address,address,uint256,bytes32)")[:4].hex()
        + _address_topic(token)[2:]
        + _address_topic(recipient)[2:]
        + _word(amount)
        + reference[2:]
    )


def _vault_logs(
    *,
    vault=VAULT,
    token=BASE_SEPOLIA_USDC_ADDRESS,
    recipient=RECIPIENT,
    amount=AMOUNT_BASE_UNITS,
    reference=None,
    agent=SENDER,
):
    reference = reference or "0x" + _fingerprint(_audit_proposal(recipient, amount))
    return [
        {
            "address": vault,
            "topics": [
                "0x" + keccak(
                    text="TokenTransferred(address,address,uint256,bytes32,address)"
                ).hex(),
                _address_topic(token),
                _address_topic(recipient),
            ],
            "data": "0x" + _word(amount) + reference[2:] + _address_topic(agent)[2:],
            "transactionHash": TRANSACTION_HASH,
            "logIndex": "0x0",
        },
        {
            "address": token,
            "topics": [
                "0x" + keccak(text="Transfer(address,address,uint256)").hex(),
                _address_topic(vault),
                _address_topic(recipient),
            ],
            "data": "0x" + _word(amount),
            "transactionHash": TRANSACTION_HASH,
            "logIndex": "0x1",
        },
    ]


def _vault_transaction(*, vault=VAULT, calldata=None, sender=SENDER):
    transaction = _transaction(
        calldata=_vault_calldata() if calldata is None else calldata,
        sender=sender,
    )
    transaction["to"] = vault
    return transaction


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
    logs=None,
    latest_block_number="0x130",
    receipt_block_hash="0x" + "c" * 64,
    canonical_block_hash="0x" + "c" * 64,
    canonical_block_number="0x123",
):
    return {
        "eth_chainId": chain_id,
        "eth_blockNumber": latest_block_number,
        "eth_getTransactionByHash": (
            _transaction() if transaction is _USE_DEFAULT_TRANSACTION else transaction
        ),
        "eth_getTransactionReceipt": (
            {
                "transactionHash": TRANSACTION_HASH,
                "status": receipt_status,
                "blockNumber": "0x123",
                "blockHash": receipt_block_hash,
                "logs": [] if logs is None else logs,
            }
            if receipt
            else None
        ),
        "eth_getBlockByNumber": {
            "number": canonical_block_number,
            "hash": canonical_block_hash,
        },
    }


def test_verifies_successful_base_sepolia_transfer():
    with _rpc(_results()) as rpc:
        evidence = evidence_script.verify_evidence(TRANSACTION_HASH, rpc)

    assert evidence == {
        "chain_id": 84532,
        "receipt_status": "success",
        "block_number": 0x123,
        "receipt_block_hash": "0x" + "c" * 64,
        "canonical_block_hash": "0x" + "c" * 64,
        "latest_block_number": 0x130,
        "confirmations_observed": 0x130 - 0x123 + 1,
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


def test_verifies_reverted_vault_call_without_success_transfer_events():
    with _rpc(_results(transaction=_vault_transaction(), receipt_status="0x0")) as rpc:
        evidence = evidence_script.verify_evidence(
            TRANSACTION_HASH, rpc, VAULT, expect_revert=True
        )

    assert evidence["receipt_status"] == "failed"
    assert evidence["vault_address"] == VAULT
    assert evidence["token_contract"] == BASE_SEPOLIA_USDC_ADDRESS
    assert evidence["recipient"] == RECIPIENT
    assert evidence["amount_base_units"] == AMOUNT_BASE_UNITS


def test_reverted_vault_verification_rejects_successful_receipt():
    with (
        _rpc(_results(transaction=_vault_transaction(), receipt_status="0x1")) as rpc,
        pytest.raises(ValueError, match="requires a failed transaction receipt"),
    ):
        evidence_script.verify_evidence(
            TRANSACTION_HASH, rpc, VAULT, expect_revert=True
        )


def test_reverted_vault_verification_requires_vault_address():
    with (
        _rpc(_results()) as rpc,
        pytest.raises(ValueError, match="requires --vault"),
    ):
        evidence_script.verify_evidence(
            TRANSACTION_HASH, rpc, expect_revert=True
        )


def test_cli_verifies_reverted_vault_call(monkeypatch, capsys):
    _patch_rpc(
        monkeypatch,
        _results(transaction=_vault_transaction(), receipt_status="0x0"),
    )
    assert evidence_script.main([
        TRANSACTION_HASH,
        "--expected-sender", SENDER,
        "--vault", VAULT,
        "--expect-revert",
    ]) == 0
    captured = capsys.readouterr()
    assert "Receipt status: failed" in captured.out
    assert "Reverted vault call: receipt confirms the transaction failed" in captured.out


def test_cli_rejects_audit_db_with_reverted_vault_mode(monkeypatch):
    _patch_rpc(monkeypatch, _results(transaction=_vault_transaction(), receipt_status="0x0"))
    with pytest.raises(SystemExit) as exc_info:
        evidence_script.main([
            TRANSACTION_HASH,
            "--expected-sender", SENDER,
            "--vault", VAULT,
            "--expect-revert",
            "--audit-db", "aegis.sqlite3",
        ])
    assert exc_info.value.code == 2


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
    assert f"Receipt block hash: {'0x' + 'c' * 64}" in captured.out
    assert f"Canonical block hash at verification: {'0x' + 'c' * 64}" in captured.out
    assert "Latest observed block: 304" in captured.out
    assert "Confirmations observed: 14 (minimum required: 1)" in captured.out
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


def test_cli_fails_when_confirmation_depth_is_below_requested_minimum(monkeypatch, capsys):
    _patch_rpc(monkeypatch, _results())
    assert evidence_script.main(
        [TRANSACTION_HASH, "--expected-sender", SENDER, "--min-confirmations", "15"]
    ) == 1
    captured = capsys.readouterr()
    assert "Confirmations observed: 14 (minimum required: 15)" in captured.out
    assert "fewer than the 15 required" in captured.err


def test_cli_rejects_zero_minimum_confirmations(monkeypatch):
    _patch_rpc(monkeypatch, _results())
    with pytest.raises(SystemExit) as exc_info:
        evidence_script.main(
            [TRANSACTION_HASH, "--expected-sender", SENDER, "--min-confirmations", "0"]
        )
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
    fingerprint_override=None,
):
    proposal = _audit_proposal(recipient, amount)
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
                amount_base_units INTEGER NOT NULL,
                fingerprint TEXT NOT NULL
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
            """INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                TRANSACTION_ID,
                status,
                execution_reference,
                json.dumps(proposal),
                84532,
                BASE_SEPOLIA_USDC_ADDRESS,
                recipient.lower(),
                amount,
                fingerprint_override or _fingerprint(proposal),
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


def _run_vault_cli(monkeypatch, *, transaction=None, logs=None, audit_db=None, sender=SENDER):
    _patch_rpc(
        monkeypatch,
        _results(
            transaction=(
                _vault_transaction(sender=sender)
                if transaction is None
                else transaction
            ),
            logs=_vault_logs() if logs is None else logs,
        ),
    )
    args = [
        TRANSACTION_HASH,
        "--expected-sender",
        SENDER,
        "--vault",
        VAULT,
    ]
    if audit_db is not None:
        args.extend(["--audit-db", str(audit_db)])
    return evidence_script.main(args)


def test_vault_evidence_verifies_calldata_logs_and_audit_fingerprint(
    monkeypatch, capsys, tmp_path
):
    database_path = tmp_path / "workflow.sqlite3"
    _create_audit_database(database_path)
    assert _run_vault_cli(monkeypatch, audit_db=database_path) == 0
    output = capsys.readouterr().out
    assert f"AegisVault: {VAULT}" in output
    assert f"Proposal ref: 0x{_fingerprint(_audit_proposal())}" in output
    assert f"Audit cross-check: OK (transaction {TRANSACTION_ID})" in output


@pytest.mark.parametrize(
    ("transaction", "logs", "message"),
    [
        (
            _vault_transaction(vault=OTHER_RECIPIENT),
            _vault_logs(),
            "destination does not match --vault",
        ),
        (
            _vault_transaction(calldata="0x" + "00" * 132),
            _vault_logs(),
            "calldata is not AegisVault transferToken",
        ),
        (
            _vault_transaction(),
            [],
            "missing the vault TokenTransferred event",
        ),
        (
            _vault_transaction(),
            _vault_logs(amount=AMOUNT_BASE_UNITS + 1),
            "TokenTransferred event does not match transfer calldata",
        ),
        (
            _vault_transaction(),
            _vault_logs(recipient=OTHER_RECIPIENT),
            "TokenTransferred event does not match transfer calldata",
        ),
        (
            _vault_transaction(),
            _vault_logs(reference="0x" + "f" * 64),
            "TokenTransferred event does not match transfer calldata",
        ),
    ],
    ids=[
        "wrong-vault",
        "wrong-function",
        "missing-event",
        "amount-mismatch",
        "recipient-mismatch",
        "reference-mismatch",
    ],
)
def test_vault_evidence_rejects_mismatched_transaction_or_logs(
    monkeypatch, capsys, transaction, logs, message
):
    assert _run_vault_cli(
        monkeypatch,
        transaction=transaction,
        logs=logs,
    ) == 1
    assert message in capsys.readouterr().err


def test_vault_audit_cross_check_rejects_reference_mismatch(
    monkeypatch, capsys, tmp_path
):
    database_path = tmp_path / "workflow.sqlite3"
    _create_audit_database(database_path, fingerprint_override="d" * 64)
    assert _run_vault_cli(monkeypatch, audit_db=database_path) == 1
    assert "does not match the stored proposal fingerprint" in capsys.readouterr().err


def test_vault_evidence_rejects_wrong_expected_agent_sender(monkeypatch, capsys):
    assert _run_vault_cli(
        monkeypatch,
        transaction=_vault_transaction(sender=OTHER_RECIPIENT),
        logs=_vault_logs(agent=OTHER_RECIPIENT),
    ) == 1
    assert "does not match --expected-sender" in capsys.readouterr().err


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


def test_rejects_receipt_block_hash_that_is_no_longer_canonical():
    with (
        _rpc(_results(canonical_block_hash="0x" + "d" * 64)) as rpc,
        pytest.raises(ValueError, match="does not match the canonical block"),
    ):
        evidence_script.verify_evidence(TRANSACTION_HASH, rpc)


def test_rejects_rpc_block_with_wrong_height():
    with (
        _rpc(_results(canonical_block_number="0x124")) as rpc,
        pytest.raises(RpcRequestError, match="malformed block"),
    ):
        evidence_script.verify_evidence(TRANSACTION_HASH, rpc)


def test_rejects_latest_block_behind_receipt():
    with (
        _rpc(_results(latest_block_number="0x122")) as rpc,
        pytest.raises(RpcRequestError, match="latest block is behind"),
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


def test_rpc_rejects_malformed_receipt_log():
    with (
        _rpc(_results(logs=[{"address": "not-an-address", "topics": [], "data": "0x"}])) as rpc,
        pytest.raises(RpcRequestError, match="malformed receipt log"),
    ):
        rpc.transaction_receipt(TRANSACTION_HASH)


def test_vault_evidence_requires_matching_erc20_transfer_event():
    logs = _vault_logs()
    logs[1]["topics"][2] = _address_topic(OTHER_RECIPIENT)
    with (
        _rpc(_results(transaction=_vault_transaction(), logs=logs)) as rpc,
        pytest.raises(ValueError, match="ERC-20 Transfer event does not match"),
    ):
        evidence_script.verify_evidence(TRANSACTION_HASH, rpc, VAULT)


def test_vault_evidence_requires_token_transfer_event():
    logs = _vault_logs()[:1]
    with (
        _rpc(_results(transaction=_vault_transaction(), logs=logs)) as rpc,
        pytest.raises(ValueError, match="missing the matching token Transfer event"),
    ):
        evidence_script.verify_evidence(TRANSACTION_HASH, rpc, VAULT)
