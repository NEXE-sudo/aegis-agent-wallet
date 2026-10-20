"""Tests for read-only vault deployment configuration evidence."""
import hashlib

import pytest
from eth_utils import keccak

from app.chain.base_sepolia import RpcRequestError
from scripts.vault_configuration_evidence import capture_vault_configuration

VAULT = "0x" + "4" * 40
TOKEN = "0x" + "2" * 40
OWNER = "0x" + "3" * 40
AGENT = "0x" + "1" * 40


def _word(value: int) -> str:
    return f"{value:064x}"


def _selector(signature: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex()


def _address_word(address: str) -> int:
    return int(address, 16)


class FakeRpc:
    def __init__(
        self,
        *,
        chain_id=84532,
        code="0x60006000",
        owner=OWNER,
        agent=AGENT,
        paused=0,
        policy=(1, 100, 500, 80),
    ):
        self._chain_id = chain_id
        self._code = code
        self._owner = owner
        self._agent = agent
        self._paused = paused
        self._policy = policy

    def chain_id(self):
        return self._chain_id

    def get_code(self, address):
        assert address == VAULT
        return self._code

    def call(self, _sender, address, data):
        assert address == VAULT
        if data == _selector("owner()"):
            return "0x" + _word(_address_word(self._owner))
        if data == _selector("agent()"):
            return "0x" + _word(_address_word(self._agent))
        if data == _selector("paused()"):
            return "0x" + _word(self._paused)
        if data == _selector("tokenPolicy(address)") + TOKEN[2:].rjust(64, "0"):
            return "0x" + "".join(_word(value) for value in self._policy)
        raise AssertionError(f"Unexpected eth_call data: {data}")


def test_snapshot_captures_bytecode_identity_owner_agent_and_policy():
    snapshot = capture_vault_configuration(FakeRpc(), VAULT, TOKEN)

    assert snapshot["chain_id"] == 84532
    assert snapshot["vault_address"] == VAULT
    assert snapshot["bytecode_sha256"] == (
        "0x" + hashlib.sha256(bytes.fromhex("60006000")).hexdigest()
    )
    assert snapshot["owner"] == OWNER
    assert snapshot["agent"] == AGENT
    assert snapshot["paused"] is False
    assert snapshot["token_address"] == TOKEN
    assert snapshot["token_policy"] == {
        "allowed": True,
        "per_transaction_cap_base_units": 100,
        "daily_cap_base_units": 500,
        "approval_threshold_base_units": 80,
    }
    assert snapshot["configuration_structurally_valid"] is True
    assert snapshot["issues"] == []


def test_snapshot_rejects_wrong_chain_before_reading_bytecode():
    class WrongChainRpc(FakeRpc):
        def get_code(self, _address):
            pytest.fail("Bytecode must not be read on the wrong chain")

    with pytest.raises(ValueError, match="not Base Sepolia"):
        capture_vault_configuration(WrongChainRpc(chain_id=1), VAULT, TOKEN)


def test_snapshot_rejects_missing_contract_bytecode():
    with pytest.raises(RpcRequestError, match="malformed or empty"):
        capture_vault_configuration(FakeRpc(code="0x"), VAULT, TOKEN)


def test_snapshot_reports_structurally_invalid_owner_agent_or_policy():
    snapshot = capture_vault_configuration(
        FakeRpc(owner=OWNER, agent=OWNER, policy=(0, 0, 500, 80)), VAULT, TOKEN
    )

    assert snapshot["configuration_structurally_valid"] is False
    assert "The vault owner and agent addresses are identical." in snapshot["issues"]
    assert "The selected token is not enabled by the vault." in snapshot["issues"]
    assert "The selected token policy has a zero spending cap." in snapshot["issues"]
