"""Mocked-RPC tests for the Base Sepolia vault bypass demonstration."""
from __future__ import annotations

from collections.abc import Callable

import pytest
from eth_account import Account
from eth_utils import keccak

from app.chain.base_sepolia import BASE_SEPOLIA_CHAIN_ID, RpcRequestError
from scripts import vault_bypass_demo as bypass_demo

VAULT = "0x4444444444444444444444444444444444444444"
TOKEN = "0x5555555555555555555555555555555555555555"
RECIPIENT = "0x6666666666666666666666666666666666666666"
OWNER = "0x1111111111111111111111111111111111111111"
PRIVATE_KEY = bytes.fromhex("01" * 32)
AGENT = Account.from_key(PRIVATE_KEY).address.lower()
PER_TX_CAP = 50_000_000
DAILY_CAP = 150_000_000


def _selector(signature):
    return "0x" + keccak(text=signature)[:4].hex()


def _word(value):
    return value.to_bytes(32, "big").hex()


def _address_word(address):
    return address[2:].lower().rjust(64, "0")


def _custom_error(signature):
    return "0x" + keccak(text=signature)[:4].hex()


class FakeRpc:
    def __init__(self, *, chain_id=BASE_SEPOLIA_CHAIN_ID, simulates=True):
        self.expected_chain_id = chain_id
        self.simulates = simulates
        self.calls = []
        self.broadcasts = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def chain_id(self):
        return self.expected_chain_id

    def get_code(self, address):
        assert address == VAULT
        return "0x6000"

    def call(self, sender, to, data):
        self.calls.append((sender.lower(), to, data))
        selector = data[:10]
        if selector == _selector("owner()"):
            return "0x" + _address_word(OWNER)
        if selector == _selector("agent()"):
            return "0x" + _address_word(AGENT)
        if selector == _selector("tokenPolicy(address)"):
            assert data[10:] == _address_word(TOKEN)
            return "0x" + "".join(
                (
                    _word(1),
                    _word(PER_TX_CAP),
                    _word(DAILY_CAP),
                    _word(50_000_000),
                )
            )
        if selector == _selector("recipients(address)"):
            assert data[10:] == _address_word(RECIPIENT)
            return "0x" + _word(0)
        if selector == _selector("todaySpend(address)"):
            assert data[10:] == _address_word(TOKEN)
            return "0x" + _word(0)
        assert selector == bypass_demo.VAULT_TRANSFER_SELECTOR
        token_address = "0x" + data[10 + 24:10 + 64]
        amount = int(data[10 + 128:10 + 192], 16)
        if token_address == VAULT:
            signature = "TokenNotAllowed(address)"
        elif amount > PER_TX_CAP:
            signature = "PerTransactionCapExceeded(uint256,uint256)"
        else:
            signature = "TransferApprovalRequired(address,address,uint256,bytes32)"
        if self.simulates:
            raise RpcRequestError("mock revert", _custom_error(signature))
        return "0x"

    def gas_price(self):
        return 1_000_000_000

    def transaction_count(self, address):
        return len(self.broadcasts)

    def send_raw_transaction(self, raw_transaction):
        self.broadcasts.append(raw_transaction)
        return "0x" + keccak(hexstr=raw_transaction).hex()


def _run(rpc, *, broadcast=False, output: Callable[[str], object] = lambda _: None):
    return bypass_demo.run_bypass_demo(
        vault_address=VAULT,
        token_address=TOKEN,
        unlisted_recipient=RECIPIENT,
        private_key=PRIVATE_KEY,
        broadcast=broadcast,
        rpc_factory=lambda _url: rpc,
        rpc_url="https://rpc.invalid",
        output=output,
    )


def test_default_simulation_checks_three_reverts_without_broadcasting():
    rpc = FakeRpc()
    output = []
    assert _run(rpc, output=output.append) == 0
    assert len(rpc.broadcasts) == 0
    assert len([call for call in rpc.calls if call[2].startswith(bypass_demo.VAULT_TRANSFER_SELECTOR)]) == 3
    assert output == [
        "over-per-transaction-cap transfer: reverted (per-transaction cap exceeded)",
        "unlisted recipient without owner approval: reverted (owner approval required)",
        "disallowed token transfer: reverted (token is not allowed)",
    ]


def test_broadcast_flag_sends_only_after_each_simulation_reverts(monkeypatch):
    rpc = FakeRpc()
    transactions = []
    signer = Account.sign_transaction

    def capture_sign(transaction, private_key):
        transactions.append(transaction.copy())
        return signer(transaction, private_key)

    monkeypatch.setattr(Account, "sign_transaction", capture_sign)
    output = []
    assert _run(rpc, broadcast=True, output=output.append) == 0
    assert len(rpc.broadcasts) == 3
    assert len(transactions) == 3
    assert all(tx["chainId"] == BASE_SEPOLIA_CHAIN_ID for tx in transactions)
    assert all(tx["to"].lower() == VAULT for tx in transactions)
    assert all(tx["gas"] == bypass_demo._BYPASS_GAS_LIMIT for tx in transactions)
    assert all("broadcast transaction hash:" in line for line in output if line.startswith("  "))


def test_simulation_unexpected_success_is_never_broadcast():
    rpc = FakeRpc(simulates=False)
    output = []
    assert _run(rpc, broadcast=True, output=output.append) == 1
    assert not rpc.broadcasts
    assert sum("unexpectedly succeeded" in line for line in output) == 3


def test_refuses_wrong_chain_before_simulation():
    rpc = FakeRpc(chain_id=1)
    with pytest.raises(bypass_demo.BypassDemoError, match="not Base Sepolia"):
        _run(rpc)
    assert not rpc.calls
    assert not rpc.broadcasts


def test_cli_simulation_is_default_and_key_is_never_printed(monkeypatch, capsys):
    rpc = FakeRpc()
    monkeypatch.setenv("AEGIS_TESTNET_PRIVATE_KEY", "0x" + PRIVATE_KEY.hex())
    monkeypatch.setenv("BASE_SEPOLIA_RPC_URL", "https://rpc.invalid")
    monkeypatch.setattr(bypass_demo, "BaseSepoliaRpc", lambda _url: rpc)
    assert bypass_demo.main(
        ["--vault", VAULT, "--token", TOKEN, "--unlisted-recipient", RECIPIENT]
    ) == 0
    output = capsys.readouterr().out
    assert "owner approval required" in output
    assert PRIVATE_KEY.hex() not in output
    assert not rpc.broadcasts


def test_cli_broadcast_requires_explicit_flag(monkeypatch, capsys):
    rpc = FakeRpc()
    monkeypatch.setenv("AEGIS_TESTNET_PRIVATE_KEY", "0x" + PRIVATE_KEY.hex())
    monkeypatch.setenv("BASE_SEPOLIA_RPC_URL", "https://rpc.invalid")
    monkeypatch.setattr(bypass_demo, "BaseSepoliaRpc", lambda _url: rpc)
    assert bypass_demo.main(
        [
            "--vault",
            VAULT,
            "--token",
            TOKEN,
            "--unlisted-recipient",
            RECIPIENT,
            "--broadcast",
        ]
    ) == 0
    assert len(rpc.broadcasts) == 3
    assert PRIVATE_KEY.hex() not in capsys.readouterr().out
