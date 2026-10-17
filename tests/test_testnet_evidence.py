"""Base Sepolia evidence verification uses mocked JSON-RPC only."""
import json

import httpx
import pytest

from app.chain.base_sepolia import BASE_SEPOLIA_USDC_ADDRESS, BaseSepoliaRpc
from scripts import verify_testnet_evidence as evidence_script

TRANSACTION_HASH = "0x" + "a" * 64
RECIPIENT = "0x2222222222222222222222222222222222222222"
AMOUNT_BASE_UNITS = 2_500_000
_USE_DEFAULT_TRANSACTION = object()


def _transaction(calldata=None):
    calldata = calldata or (
        "0xa9059cbb"
        + ("0" * 24)
        + RECIPIENT[2:]
        + f"{AMOUNT_BASE_UNITS:064x}"
    )
    return {
        "hash": TRANSACTION_HASH,
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
    assert evidence_script.main([TRANSACTION_HASH]) == 1
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
    assert evidence_script.main([TRANSACTION_HASH]) == 0
    captured = capsys.readouterr()
    assert "Chain ID: 84532 (Base Sepolia; expected 84532)" in captured.out
    assert "Receipt status: success" in captured.out
    assert "Block number: 291" in captured.out
    assert f"Token contract: {BASE_SEPOLIA_USDC_ADDRESS}" in captured.out
    assert f"Recipient: {RECIPIENT}" in captured.out
    assert f"Amount (base units): {AMOUNT_BASE_UNITS}" in captured.out
    assert f"https://sepolia.basescan.org/tx/{TRANSACTION_HASH}" in captured.out


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
