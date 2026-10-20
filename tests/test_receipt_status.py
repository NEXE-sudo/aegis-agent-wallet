"""Tests for receipt confirmation and canonical-block assessment."""

import json

import httpx
import pytest

from app.chain.base_sepolia import BaseSepoliaRpc
from app.chain.execution import BaseSepoliaVaultExecutor, ExecutionSettings

TX_HASH = "0x" + "a" * 64
BLOCK_HASH = "0x" + "b" * 64
REPLACED_BLOCK_HASH = "0x" + "c" * 64
VAULT = "0x9999999999999999999999999999999999999999"


def make_executor(*, receipt=True, receipt_status="0x1", canonical_hash=BLOCK_HASH, head="0x20"):
    def handler(request):
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "eth_chainId":
            result = hex(84532)
        elif method == "eth_getTransactionReceipt":
            result = (
                {
                    "transactionHash": TX_HASH,
                    "status": receipt_status,
                    "blockNumber": "0x10",
                    "blockHash": BLOCK_HASH,
                    "logs": [],
                }
                if receipt
                else None
            )
        elif method == "eth_blockNumber":
            result = head
        elif method == "eth_getBlockByNumber":
            result = {"number": "0x10", "hash": canonical_hash}
        else:
            raise AssertionError(f"unexpected RPC method: {method}")
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": result}
        )

    transport = httpx.MockTransport(handler)
    settings = ExecutionSettings(
        mode="base_sepolia_vault",
        rpc_url="https://rpc.invalid",
        private_key="test-key-not-used",
        vault_address=VAULT,
    )
    return BaseSepoliaVaultExecutor(
        settings, rpc_factory=lambda url: BaseSepoliaRpc(url, transport=transport)
    )


def test_receipt_status_pending_has_no_confirmation_claim():
    result = make_executor(receipt=False).receipt_status(TX_HASH)

    assert result == {
        "transaction_hash": TX_HASH,
        "status": "pending",
        "block_number": None,
        "block_hash": None,
        "confirmations_observed": 0,
        "minimum_confirmations_required": 12,
        "canonical_block_match": None,
    }


def test_receipt_status_included_but_below_confirmation_threshold():
    result = make_executor(head="0x15").receipt_status(TX_HASH)

    assert result["status"] == "included"
    assert result["confirmations_observed"] == 6
    assert result["canonical_block_match"] is True


def test_receipt_status_confirmed_only_after_threshold_and_canonical_match():
    result = make_executor().receipt_status(TX_HASH)

    assert result["status"] == "confirmed"
    assert result["confirmations_observed"] == 17
    assert result["minimum_confirmations_required"] == 12
    assert result["canonical_block_match"] is True


def test_receipt_status_reports_reverted_receipt():
    result = make_executor(receipt_status="0x0").receipt_status(TX_HASH)

    assert result["status"] == "failed"
    assert result["canonical_block_match"] is True
    assert result["confirmations_observed"] == 17


def test_receipt_status_detects_receipt_block_reorg():
    result = make_executor(canonical_hash=REPLACED_BLOCK_HASH).receipt_status(TX_HASH)

    assert result["status"] == "reorged"
    assert result["canonical_block_match"] is False


def test_receipt_status_rejects_nonpositive_confirmation_threshold():
    with pytest.raises(ValueError, match="positive integer"):
        make_executor().receipt_status(TX_HASH, minimum_confirmations=0)
