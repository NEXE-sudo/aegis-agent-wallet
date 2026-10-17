"""Base Sepolia execution tests use mocked JSON-RPC transports only."""
import json
import secrets
import sqlite3

import httpx
import pytest
from eth_account import Account
from eth_utils import keccak
from fastapi.testclient import TestClient

from app import main
from app.chain.base_sepolia import (
    BASE_SEPOLIA_CHAIN_ID,
    BASE_SEPOLIA_USDC_ADDRESS,
    BaseSepoliaRpc,
)
from app.chain.execution import (
    BaseSepoliaExecutor,
    ExecutionConfigurationError,
    ExecutionSettings,
)
from app.workflow.controller import TransactionController
from app.workflow.store import WorkflowStore

client = TestClient(main.app)


def _test_execution_settings(monkeypatch):
    private_key = Account.create().key.hex()
    monkeypatch.setenv("AEGIS_EXECUTION_MODE", "base_sepolia")
    monkeypatch.setenv("AEGIS_TESTNET_PRIVATE_KEY", private_key)
    monkeypatch.setenv("BASE_SEPOLIA_RPC_URL", "https://rpc.invalid")
    return ExecutionSettings.from_environment()


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    agent_token = "test-" + secrets.token_urlsafe(32)
    owner_token = "test-" + secrets.token_urlsafe(32)
    monkeypatch.setenv("AEGIS_AGENT_TOKEN", agent_token)
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", owner_token)
    store = WorkflowStore(tmp_path / "base-sepolia-execution.sqlite3")
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(
        main, "controller", TransactionController(main.DEMO_POLICY, store)
    )
    monkeypatch.setattr(
        main,
        "execution_settings",
        ExecutionSettings(mode="simulated"),
    )
    return store, {"Authorization": f"Bearer {agent_token}"}, {
        "Authorization": f"Bearer {owner_token}"
    }


def _mocked_executor(monkeypatch, methods):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        result = methods[payload["method"]]
        if callable(result):
            result = result(payload["params"])
        if isinstance(result, Exception):
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32000, "message": "mock RPC error"},
                },
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": result}
        )

    transport = httpx.MockTransport(handler)
    factory = lambda url: BaseSepoliaRpc(url, transport=transport)
    monkeypatch.setattr(
        main,
        "BaseSepoliaExecutor",
        lambda settings, policy: BaseSepoliaExecutor(
            settings, policy, rpc_factory=factory
        ),
    )
    settings = _test_execution_settings(monkeypatch)
    monkeypatch.setattr(main, "execution_settings", settings)
    return settings.private_key


def _abi_uint(value):
    return "0x" + value.to_bytes(32, "big").hex()


def _mock_methods(
    send_raw_transaction,
    receipt=None,
    chain_id=BASE_SEPOLIA_CHAIN_ID,
    transfer_result=None,
    token_balance=10**18,
    token_balance_result=None,
    eth_balance=10**18,
):
    if transfer_result is None:
        transfer_result = _abi_uint(1)
    methods = {
        "eth_chainId": hex(chain_id),
        "eth_getTransactionCount": "0x2",
        "eth_gasPrice": "0x3b9aca00",
        "eth_estimateGas": "0xea60",
        "eth_sendRawTransaction": send_raw_transaction,
        "eth_call": lambda params: (
            transfer_result
            if params[0]["data"].startswith("0xa9059cbb")
            else (
                token_balance_result
                if token_balance_result is not None
                else _abi_uint(token_balance)
            )
        ),
        "eth_getBalance": hex(eth_balance),
    }
    if receipt is not None:
        methods["eth_getTransactionReceipt"] = receipt
    return methods


def test_execution_settings_require_explicit_valid_testnet_configuration(monkeypatch):
    monkeypatch.setenv("AEGIS_EXECUTION_MODE", "simulated")
    monkeypatch.setenv("AEGIS_TESTNET_PRIVATE_KEY", "not-a-private-key")
    monkeypatch.delenv("BASE_SEPOLIA_RPC_URL", raising=False)
    settings = ExecutionSettings.from_environment()
    assert settings.mode == "simulated"
    assert settings.private_key is None

    monkeypatch.setenv("AEGIS_EXECUTION_MODE", "base_sepolia")
    monkeypatch.delenv("AEGIS_TESTNET_PRIVATE_KEY", raising=False)
    with pytest.raises(ExecutionConfigurationError, match="AEGIS_TESTNET_PRIVATE_KEY"):
        ExecutionSettings.from_environment()

    monkeypatch.setenv(
        "AEGIS_TESTNET_PRIVATE_KEY", Account.create().key.hex()
    )
    with pytest.raises(ExecutionConfigurationError, match="BASE_SEPOLIA_RPC_URL"):
        ExecutionSettings.from_environment()

    monkeypatch.setenv("BASE_SEPOLIA_RPC_URL", "http://rpc.invalid")
    with pytest.raises(ExecutionConfigurationError, match="HTTPS URL"):
        ExecutionSettings.from_environment()

    monkeypatch.setenv("BASE_SEPOLIA_RPC_URL", "https://rpc.invalid:invalid")
    with pytest.raises(ExecutionConfigurationError, match="HTTPS URL"):
        ExecutionSettings.from_environment()


def test_execution_settings_reject_invalid_mode_and_key_without_echoing_key(monkeypatch):
    monkeypatch.setenv("AEGIS_EXECUTION_MODE", "mainnet")
    with pytest.raises(ExecutionConfigurationError, match="AEGIS_EXECUTION_MODE"):
        ExecutionSettings.from_environment()

    monkeypatch.setenv("AEGIS_EXECUTION_MODE", "base_sepolia")
    monkeypatch.setenv("BASE_SEPOLIA_RPC_URL", "https://rpc.invalid")
    secret_marker = "invalid-private-key-marker"
    monkeypatch.setenv("AEGIS_TESTNET_PRIVATE_KEY", secret_marker)
    with pytest.raises(ExecutionConfigurationError) as exc_info:
        ExecutionSettings.from_environment()
    assert secret_marker not in str(exc_info.value)


def test_preflight_setting_defaults_by_execution_mode_and_rejects_invalid_values(
    monkeypatch,
):
    monkeypatch.delenv("AEGIS_PREFLIGHT_ENABLED", raising=False)
    base_settings = _test_execution_settings(monkeypatch)
    assert base_settings.preflight_enabled is True

    monkeypatch.setenv("AEGIS_PREFLIGHT_ENABLED", "false")
    assert _test_execution_settings(monkeypatch).preflight_enabled is False

    monkeypatch.setenv("AEGIS_EXECUTION_MODE", "simulated")
    monkeypatch.delenv("AEGIS_PREFLIGHT_ENABLED", raising=False)
    assert ExecutionSettings.from_environment().preflight_enabled is False

    monkeypatch.setenv("AEGIS_PREFLIGHT_ENABLED", "yes")
    with pytest.raises(ExecutionConfigurationError, match="AEGIS_PREFLIGHT_ENABLED"):
        ExecutionSettings.from_environment()
    with pytest.raises(ExecutionConfigurationError), TestClient(main.app):
        pass


def test_app_startup_rejects_incomplete_base_sepolia_configuration(monkeypatch):
    monkeypatch.setenv("AEGIS_EXECUTION_MODE", "base_sepolia")
    monkeypatch.setenv("BASE_SEPOLIA_RPC_URL", "https://rpc.invalid")
    monkeypatch.delenv("AEGIS_TESTNET_PRIVATE_KEY", raising=False)

    with pytest.raises(ExecutionConfigurationError), TestClient(main.app):
        pass


def test_base_sepolia_executor_signs_only_allowlisted_transfer_with_mocked_rpc(
    monkeypatch,
):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        method = payload["method"]
        if method == "eth_call":
            result = (
                _abi_uint(1)
                if payload["params"][0]["data"].startswith("0xa9059cbb")
                else _abi_uint(proposal["amount_base_units"])
            )
        elif method == "eth_getBalance":
            maximum_cost = (
                (int("0xea60", 16) * 120 + 99) // 100 * int("0x3b9aca00", 16)
            )
            result = hex(maximum_cost)
        else:
            result = {
                "eth_chainId": hex(BASE_SEPOLIA_CHAIN_ID),
                "eth_getTransactionCount": "0x2",
                "eth_gasPrice": "0x3b9aca00",
                "eth_estimateGas": "0xea60",
            }[method]
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": result}
        )

    settings = _test_execution_settings(monkeypatch)
    rpc_factory = lambda url: BaseSepoliaRpc(
        url, transport=httpx.MockTransport(handler)
    )
    executor = BaseSepoliaExecutor(
        settings,
        main.DEMO_POLICY,
        rpc_factory=rpc_factory,
    )
    proposal = {
        "chain_id": BASE_SEPOLIA_CHAIN_ID,
        "token_symbol": "USDC",
        "token_address": BASE_SEPOLIA_USDC_ADDRESS,
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 1_000_000,
        "token_decimals": 6,
    }

    signed = executor.prepare(proposal)

    assert signed.raw_transaction.startswith("0x")
    assert len(signed.transaction_hash) == 66
    assert settings.private_key.removeprefix("0x") not in signed.raw_transaction
    estimate = next(call for call in calls if call["method"] == "eth_estimateGas")
    assert estimate["params"][0]["to"] == BASE_SEPOLIA_USDC_ADDRESS
    assert estimate["params"][0]["data"] == (
        "0xa9059cbb"
        + proposal["recipient"][2:].rjust(64, "0")
        + proposal["amount_base_units"].to_bytes(32, "big").hex()
    )
    transfer_call = next(
        call for call in calls
        if call["method"] == "eth_call"
        and call["params"][0]["data"].startswith("0xa9059cbb")
    )
    assert transfer_call["params"][0]["from"] == Account.from_key(
        settings.private_key
    ).address.lower()


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("revert", "preflight_call_failed"),
        ("false", "preflight_call_failed"),
        ("token", "insufficient_token_balance"),
        ("eth", "insufficient_eth_balance"),
        ("malformed", "preflight_rpc_error"),
    ],
)
def test_preflight_failures_never_sign_or_change_workflow_state(
    isolated_app, monkeypatch, failure, expected_code
):
    store, agent_headers, owner_headers = isolated_app
    sent = []
    methods = _mock_methods(
        lambda params: sent.append(params[0]),
        transfer_result=(
            RuntimeError("simulated revert")
            if failure == "revert"
            else _abi_uint(0) if failure == "false" else None
        ),
        token_balance=0 if failure == "token" else 10**18,
        token_balance_result="0x1" if failure == "malformed" else None,
        eth_balance=0 if failure == "eth" else 10**18,
    )
    _mocked_executor(monkeypatch, methods)
    signing_calls = []

    def unexpected_sign(*args, **kwargs):
        signing_calls.append((args, kwargs))
        raise AssertionError("preflight failure must not call the signer")

    monkeypatch.setattr(Account, "sign_transaction", unexpected_sign)
    proposal = client.post(
        "/transactions/propose",
        headers=agent_headers,
        json={
            "agent_id": "devops-01",
            "chain_id": BASE_SEPOLIA_CHAIN_ID,
            "token_symbol": "USDC",
            "token_address": BASE_SEPOLIA_USDC_ADDRESS,
            "recipient": "0x2222222222222222222222222222222222222222",
            "amount_base_units": 1_000_000,
            "token_decimals": 6,
        },
    ).json()

    response = client.post(
        f"/transactions/{proposal['transaction_id']}/execute",
        headers=owner_headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == expected_code
    assert not signing_calls
    assert not sent
    assert store.get(proposal["transaction_id"])["status"] == "ready"
    assert [event["event_type"] for event in store.audit_events(
        proposal["transaction_id"]
    )] == ["proposal_created"]


def test_disabled_preflight_skips_read_checks_and_executes(isolated_app, monkeypatch):
    _store, agent_headers, owner_headers = isolated_app
    preflight_calls = []
    methods = _mock_methods(
        lambda params: "0x" + keccak(hexstr=params[0]).hex()
    )

    def called_unexpectedly(params):
        preflight_calls.append(params)
        return _abi_uint(1)

    methods["eth_call"] = called_unexpectedly
    methods["eth_getBalance"] = called_unexpectedly
    monkeypatch.setenv("AEGIS_PREFLIGHT_ENABLED", "false")
    _mocked_executor(monkeypatch, methods)
    proposal = client.post(
        "/transactions/propose",
        headers=agent_headers,
        json={
            "agent_id": "devops-01",
            "chain_id": BASE_SEPOLIA_CHAIN_ID,
            "token_symbol": "USDC",
            "token_address": BASE_SEPOLIA_USDC_ADDRESS,
            "recipient": "0x2222222222222222222222222222222222222222",
            "amount_base_units": 1_000_000,
            "token_decimals": 6,
        },
    ).json()

    response = client.post(
        f"/transactions/{proposal['transaction_id']}/execute",
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "submitted_onchain"
    assert not preflight_calls


def test_execution_rejects_wrong_chain_before_signing_or_broadcasting(
    isolated_app, monkeypatch
):
    _, _, owner_headers = isolated_app
    _mocked_executor(
        monkeypatch,
        _mock_methods(lambda _: "0x" + "22" * 32, chain_id=12345),
    )
    proposal = client.post(
        "/transactions/propose",
        headers=isolated_app[1],
        json={
            "agent_id": "devops-01",
            "chain_id": BASE_SEPOLIA_CHAIN_ID,
            "token_symbol": "USDC",
            "token_address": BASE_SEPOLIA_USDC_ADDRESS,
            "recipient": "0x2222222222222222222222222222222222222222",
            "amount_base_units": 1_000_000,
            "token_decimals": 6,
        },
    ).json()

    response = client.post(
        f"/transactions/{proposal['transaction_id']}/execute",
        headers=owner_headers,
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "Base Sepolia transaction submission failed"}
    store = isolated_app[0]
    assert store.get(proposal["transaction_id"])["status"] == "ready"
    assert [event["event_type"] for event in store.audit_events(
        proposal["transaction_id"]
    )] == ["proposal_created"]


def test_onchain_execution_is_audited_and_receipt_is_owner_readable(
    isolated_app, monkeypatch
):
    submitted_raw = []
    transaction_hash = None

    def send(params):
        submitted_raw.append(params[0])
        return "0x" + keccak(hexstr=params[0]).hex()

    def receipt(params):
        return {
            "transactionHash": params[0],
            "status": "0x1",
            "blockNumber": "0x123",
        }

    store, agent_headers, owner_headers = isolated_app
    private_key = _mocked_executor(
        monkeypatch,
        _mock_methods(send, receipt),
    )
    proposal = client.post(
        "/transactions/propose",
        headers=agent_headers,
        json={
            "agent_id": "devops-01",
            "chain_id": BASE_SEPOLIA_CHAIN_ID,
            "token_symbol": "USDC",
            "token_address": BASE_SEPOLIA_USDC_ADDRESS,
            "recipient": "0x2222222222222222222222222222222222222222",
            "amount_base_units": 1_000_000,
            "token_decimals": 6,
        },
    ).json()

    response = client.post(
        f"/transactions/{proposal['transaction_id']}/execute",
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "submitted_onchain"
    transaction_hash = response.json()["execution_reference"]
    assert transaction_hash == "0x" + keccak(hexstr=submitted_raw[0]).hex()
    assert private_key.removeprefix("0x") not in response.text
    events = store.audit_events(proposal["transaction_id"])
    assert [event["event_type"] for event in events] == [
        "proposal_created",
        "onchain_transaction_signed",
        "onchain_transaction_submitted",
    ]
    assert events[-1]["details"] == {"transaction_hash": transaction_hash}
    assert private_key.removeprefix("0x") not in json.dumps(events)

    forbidden = client.get(
        f"/transactions/{proposal['transaction_id']}/receipt",
        headers=agent_headers,
    )
    assert forbidden.status_code == 401
    receipt_response = client.get(
        f"/transactions/{proposal['transaction_id']}/receipt",
        headers=owner_headers,
    )
    assert receipt_response.status_code == 200
    assert receipt_response.json() == {
        "transaction_id": proposal["transaction_id"],
        "transaction_hash": transaction_hash,
        "chain_id": BASE_SEPOLIA_CHAIN_ID,
        "status": "success",
        "block_number": 0x123,
    }


def test_uncertain_submission_retry_reuses_the_exact_persisted_signed_bytes(
    isolated_app, monkeypatch
):
    submitted_raw = []

    def send(params):
        submitted_raw.append(params[0])
        if len(submitted_raw) == 1:
            return RuntimeError("network may have accepted this transaction")
        return "0x" + keccak(hexstr=params[0]).hex()

    calls = {"nonce": 0, "estimate": 0}
    original_methods = _mock_methods(send)

    def count_nonce(params):
        calls["nonce"] += 1
        return "0x2"

    def count_estimate(params):
        calls["estimate"] += 1
        return "0xea60"

    original_methods["eth_getTransactionCount"] = count_nonce
    original_methods["eth_estimateGas"] = count_estimate
    store, agent_headers, owner_headers = isolated_app
    private_key = _mocked_executor(monkeypatch, original_methods)
    proposal = client.post(
        "/transactions/propose",
        headers=agent_headers,
        json={
            "agent_id": "devops-01",
            "chain_id": BASE_SEPOLIA_CHAIN_ID,
            "token_symbol": "USDC",
            "token_address": BASE_SEPOLIA_USDC_ADDRESS,
            "recipient": "0x2222222222222222222222222222222222222222",
            "amount_base_units": 1_000_000,
            "token_decimals": 6,
        },
    ).json()
    path = f"/transactions/{proposal['transaction_id']}/execute"

    failed = client.post(path, headers=owner_headers)

    assert failed.status_code == 502
    pending = store.get(proposal["transaction_id"])
    assert pending["status"] == "submitting_onchain"
    assert pending["execution_reference"] == "0x" + keccak(
        hexstr=submitted_raw[0]
    ).hex()
    assert private_key.removeprefix("0x") not in json.dumps(pending)
    assert "signed_transaction" not in pending
    with sqlite3.connect(store.db_path) as db:
        raw_in_database = db.execute(
            "SELECT signed_transaction FROM transactions WHERE transaction_id = ?",
            (proposal["transaction_id"],),
        ).fetchone()[0]
    assert raw_in_database == submitted_raw[0]

    retried = client.post(path, headers=owner_headers)

    assert retried.status_code == 200
    assert retried.json()["status"] == "submitted_onchain"
    assert submitted_raw[0] == submitted_raw[1]
    assert calls == {"nonce": 1, "estimate": 1}
    assert store.daily_spend(BASE_SEPOLIA_CHAIN_ID, BASE_SEPOLIA_USDC_ADDRESS) == 1_000_000
