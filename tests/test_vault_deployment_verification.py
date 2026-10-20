"""Regression tests for read-only AegisVault deployment verification."""
import hashlib

import pytest

from app.chain.base_sepolia import BASE_SEPOLIA_CHAIN_ID, RpcRequestError
from scripts import verify_vault_deployment as deployment

TX_HASH = "0x" + "a" * 64
DEPLOYER = "0x1111111111111111111111111111111111111111"
OWNER = "0x2222222222222222222222222222222222222222"
VAULT = "0x3333333333333333333333333333333333333333"
OTHER = "0x4444444444444444444444444444444444444444"
CODE = "0x600160005560016000f3"


class FakeRpc:
    def __init__(
        self,
        *,
        chain_id=BASE_SEPOLIA_CHAIN_ID,
        tx_to=None,
        tx_owner=OWNER,
        receipt_status="0x1",
        contract_address=VAULT,
        receipt_block_hash="0x" + "b" * 64,
        canonical_block_hash="0x" + "b" * 64,
        latest_block=120,
        receipt_block=110,
        code=CODE,
        onchain_owner=OWNER,
        missing_tx=False,
        missing_receipt=False,
    ):
        self.configured_chain_id = chain_id
        self.tx_to = tx_to
        self.tx_owner = tx_owner
        self.receipt_status = receipt_status
        self.contract_address = contract_address
        self.receipt_block_hash = receipt_block_hash
        self.canonical_block_hash = canonical_block_hash
        self.latest_block = latest_block
        self.receipt_block = receipt_block
        self.code = code
        self.onchain_owner = onchain_owner
        self.missing_tx = missing_tx
        self.missing_receipt = missing_receipt

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def chain_id(self):
        return self.configured_chain_id

    def _rpc(self, method, params):
        if method == "eth_getTransactionByHash":
            if self.missing_tx:
                return None
            return {
                "hash": TX_HASH,
                "from": DEPLOYER,
                "to": self.tx_to,
                "input": "0x" + "60" * 20 + ("0" * 24) + self.tx_owner[2:],
            }
        if method == "eth_getTransactionReceipt":
            if self.missing_receipt:
                return None
            return {
                "transactionHash": TX_HASH,
                "status": self.receipt_status,
                "contractAddress": self.contract_address,
                "blockNumber": hex(self.receipt_block),
                "blockHash": self.receipt_block_hash,
            }
        if method == "eth_call":
            return "0x" + "0" * 24 + self.onchain_owner[2:]
        raise AssertionError(f"Unexpected RPC method: {method}")

    def block_hash_by_number(self, block_number):
        assert block_number == self.receipt_block
        return self.canonical_block_hash.lower()

    def block_number(self):
        return self.latest_block

    def get_code(self, _address):
        return self.code


def _verify(rpc=None, **kwargs):
    return deployment.verify_deployment(
        TX_HASH,
        rpc or FakeRpc(),
        expected_vault=kwargs.pop("expected_vault", VAULT),
        expected_owner=kwargs.pop("expected_owner", OWNER),
        **kwargs,
    )


def test_verifies_deployment_receipt_owner_bytecode_and_confirmation_depth():
    result = _verify(min_confirmations=10)

    assert result["chain_id"] == BASE_SEPOLIA_CHAIN_ID
    assert result["transaction_hash"] == TX_HASH
    assert result["deployer"] == DEPLOYER
    assert result["vault_address"] == VAULT
    assert result["constructor_owner"] == OWNER
    assert result["current_onchain_owner"] == OWNER
    assert result["receipt_status"] == "success"
    assert result["confirmations_observed"] == 11
    assert result["runtime_bytecode_sha256"] == hashlib.sha256(bytes.fromhex(CODE[2:])).hexdigest()
    assert result["read_only"] is True


def test_accepts_expected_bytecode_hash_with_optional_prefix():
    expected = hashlib.sha256(bytes.fromhex(CODE[2:])).hexdigest()
    assert _verify(expected_bytecode_sha256="0x" + expected)["expected_bytecode_hash_matched"]


@pytest.mark.parametrize(
    ("rpc", "message"),
    [
        (FakeRpc(chain_id=1), "not Base Sepolia"),
        (FakeRpc(tx_to=OTHER), "contract-creation transaction"),
        (FakeRpc(tx_owner=OTHER), "constructor owner does not match"),
        (FakeRpc(contract_address=OTHER), "does not match --expected-vault"),
        (FakeRpc(receipt_status="0x0"), "receipt status is not success"),
        (FakeRpc(receipt_block_hash="0x" + "c" * 64), "does not match the RPC's block"),
        (FakeRpc(latest_block=118), "fewer than the 12 required"),
        (FakeRpc(code="0x"), "No deployed runtime bytecode"),
        (FakeRpc(onchain_owner=OTHER), "on-chain vault owner does not match"),
        (FakeRpc(missing_tx=True), "transaction was not found"),
        (FakeRpc(missing_receipt=True), "receipt is not available"),
    ],
)
def test_rejects_invalid_deployment_evidence(rpc, message):
    with pytest.raises(ValueError, match=message):
        _verify(rpc)


def test_rejects_expected_runtime_bytecode_hash_mismatch():
    with pytest.raises(ValueError, match="bytecode SHA-256 does not match"):
        _verify(expected_bytecode_sha256="f" * 64)


def test_rejects_malformed_deployment_transaction_hash():
    with pytest.raises(ValueError, match="32-byte hexadecimal"):
        deployment.verify_deployment(
            "bad",
            FakeRpc(),
            expected_vault=VAULT,
            expected_owner=OWNER,
        )


def test_rejects_malformed_owner_return_data():
    class MalformedOwnerRpc(FakeRpc):
        def _rpc(self, method, params):
            if method == "eth_call":
                return "0x1234"
            return super()._rpc(method, params)

    with pytest.raises(RpcRequestError, match="malformed AegisVault owner"):
        _verify(MalformedOwnerRpc())


def test_cli_uses_read_only_verifier_and_prints_evidence(monkeypatch, capsys):
    rpc = FakeRpc()
    monkeypatch.setattr(deployment, "BaseSepoliaRpc", lambda: rpc)

    assert deployment.main([
        TX_HASH,
        "--expected-vault", VAULT,
        "--expected-owner", OWNER,
        "--min-confirmations", "10",
    ]) == 0
    output = capsys.readouterr().out
    assert "AegisVault deployment verification: PASS" in output
    assert TX_HASH in output
    assert "Read-only verification; no transaction was signed or broadcast." in output


def test_cli_returns_failure_without_claiming_success(monkeypatch, capsys):
    monkeypatch.setattr(deployment, "BaseSepoliaRpc", lambda: FakeRpc(chain_id=1))

    assert deployment.main([
        TX_HASH,
        "--expected-vault", VAULT,
        "--expected-owner", OWNER,
    ]) == 1
    captured = capsys.readouterr()
    assert "Deployment verification failed" in captured.err
    assert "verification: PASS" not in captured.out



def test_cli_writes_machine_readable_evidence_with_integrity_digest(
    monkeypatch, tmp_path, capsys
):
    import hashlib
    import json

    output_path = tmp_path / "deployment" / "evidence.json"
    monkeypatch.setattr(deployment, "BaseSepoliaRpc", lambda: FakeRpc())

    assert deployment.main([
        TX_HASH,
        "--expected-vault", VAULT,
        "--expected-owner", OWNER,
        "--min-confirmations", "10",
        "--output", str(output_path),
    ]) == 0
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    digest = saved.pop("deployment_evidence_sha256")
    canonical = json.dumps(saved, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert digest == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert "JSON evidence:" in capsys.readouterr().out
