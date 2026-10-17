"""Base Sepolia execution tests use mocked JSON-RPC transports only."""
import hashlib
import json
import secrets
import sqlite3

import httpx
import pytest
from eth_account import Account
from eth_utils import keccak
from fastapi.testclient import TestClient

import app.chain.execution as execution_module
from app import main
from app.chain.base_sepolia import (
    BASE_SEPOLIA_CHAIN_ID,
    BASE_SEPOLIA_USDC_ADDRESS,
    BaseSepoliaRpc,
)
from app.chain.execution import (
    BaseSepoliaExecutor,
    BaseSepoliaVaultExecutor,
    ExecutionConfigurationError,
    ExecutionSettings,
    VaultExecutionError,
)
from app.policy.canonical import hash_policy
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


def test_vault_execution_settings_require_valid_address_and_agent_key(monkeypatch):
    private_key = Account.create().key.hex()
    monkeypatch.setenv("AEGIS_EXECUTION_MODE", "base_sepolia_vault")
    monkeypatch.setenv("AEGIS_TESTNET_PRIVATE_KEY", private_key)
    monkeypatch.setenv("BASE_SEPOLIA_RPC_URL", "https://rpc.invalid")
    monkeypatch.delenv("AEGIS_VAULT_ADDRESS", raising=False)
    with pytest.raises(ExecutionConfigurationError, match="AEGIS_VAULT_ADDRESS"):
        ExecutionSettings.from_environment()

    monkeypatch.setenv("AEGIS_VAULT_ADDRESS", "not-an-address")
    with pytest.raises(ExecutionConfigurationError, match="AEGIS_VAULT_ADDRESS"):
        ExecutionSettings.from_environment()

    monkeypatch.setenv(
        "AEGIS_VAULT_ADDRESS",
        "0x9999999999999999999999999999999999999999",
    )
    settings = ExecutionSettings.from_environment()
    assert settings.mode == "base_sepolia_vault"
    assert settings.vault_address == "0x9999999999999999999999999999999999999999"
    assert settings.preflight_enabled is True
    assert settings.private_key == private_key


def _vault_settings(monkeypatch, private_key=None, vault_address=None):
    private_key = private_key or Account.create().key.hex()
    monkeypatch.setenv("AEGIS_EXECUTION_MODE", "base_sepolia_vault")
    monkeypatch.setenv("AEGIS_TESTNET_PRIVATE_KEY", private_key)
    monkeypatch.setenv("BASE_SEPOLIA_RPC_URL", "https://rpc.invalid")
    monkeypatch.setenv(
        "AEGIS_VAULT_ADDRESS",
        vault_address or "0x9999999999999999999999999999999999999999",
    )
    monkeypatch.delenv("AEGIS_PREFLIGHT_ENABLED", raising=False)
    return ExecutionSettings.from_environment()


def _vault_rpc_factory(private_key, owner=None, agent=None, code="0x60016000", failure=None):
    signer = Account.from_key(private_key).address.lower()
    owner = (owner or "0x2222222222222222222222222222222222222222").lower()
    agent = (agent or signer).lower()
    owner_selector = "0x" + keccak(text="owner()")[:4].hex()
    agent_selector = "0x" + keccak(text="agent()")[:4].hex()
    transfer_selector = "0x" + keccak(
        text="transferToken(address,address,uint256,bytes32)"
    )[:4].hex()
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        method = payload["method"]
        if method == "eth_call":
            data = payload["params"][0]["data"]
            if data == owner_selector:
                result = "0x" + owner[2:].rjust(64, "0")
            elif data == agent_selector:
                result = "0x" + agent[2:].rjust(64, "0")
            elif data.startswith(transfer_selector) and failure is not None:
                selector = "0x" + keccak(text=failure)[:4].hex()
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {"code": 3, "message": "execution reverted", "data": selector},
                    },
                )
            else:
                result = "0x"
        else:
            result = {
                "eth_chainId": hex(BASE_SEPOLIA_CHAIN_ID),
                "eth_getCode": code,
                "eth_getTransactionCount": "0x2",
                "eth_gasPrice": "0x3b9aca00",
                "eth_estimateGas": "0xea60",
            }[method]
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": result}
        )

    transport = httpx.MockTransport(handler)
    return (lambda url: BaseSepoliaRpc(url, transport=transport)), calls


def _vault_proposal():
    return {
        "agent_id": "devops-01",
        "chain_id": BASE_SEPOLIA_CHAIN_ID,
        "token_symbol": "USDC",
        "token_address": BASE_SEPOLIA_USDC_ADDRESS,
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 1_000_000,
        "token_decimals": 6,
    }


def test_vault_executor_checks_code_owner_and_configured_agent(monkeypatch):
    settings = _vault_settings(monkeypatch)
    signer = Account.from_key(settings.private_key).address.lower()
    factory, _ = _vault_rpc_factory(settings.private_key, owner=signer)
    executor = BaseSepoliaVaultExecutor(settings, rpc_factory=factory)
    with pytest.raises(VaultExecutionError, match="is the owner"):
        executor.prepare(_vault_proposal())

    factory, _ = _vault_rpc_factory(settings.private_key, agent="0x3333333333333333333333333333333333333333")
    executor = BaseSepoliaVaultExecutor(settings, rpc_factory=factory)
    with pytest.raises(VaultExecutionError, match="not the configured agent"):
        executor.prepare(_vault_proposal())

    factory, _ = _vault_rpc_factory(settings.private_key, code="0x")
    executor = BaseSepoliaVaultExecutor(settings, rpc_factory=factory)
    with pytest.raises(VaultExecutionError, match="no contract code"):
        executor.prepare(_vault_proposal())


def test_vault_mode_rejects_simulated_policy_failure_without_workflow_transition(
    isolated_app, monkeypatch
):
    store, agent_headers, owner_headers = isolated_app
    settings = _vault_settings(monkeypatch)
    factory, calls = _vault_rpc_factory(
        settings.private_key,
        failure="TransferApprovalRequired(address,address,uint256,bytes32)",
    )
    monkeypatch.setattr(
        main,
        "BaseSepoliaVaultExecutor",
        lambda configured: BaseSepoliaVaultExecutor(configured, rpc_factory=factory),
    )
    monkeypatch.setattr(main, "execution_settings", settings)
    sign_calls = []
    monkeypatch.setattr(
        Account,
        "sign_transaction",
        lambda *args, **kwargs: sign_calls.append((args, kwargs)),
    )
    proposal = client.post(
        "/transactions/propose",
        headers=agent_headers,
        json=_vault_proposal(),
    ).json()

    response = client.post(
        f"/transactions/{proposal['transaction_id']}/execute",
        headers=owner_headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "onchain_approval_missing"
    assert not sign_calls
    assert not any(call["method"] == "eth_sendRawTransaction" for call in calls)
    assert store.get(proposal["transaction_id"])["status"] == "ready"
    assert [event["event_type"] for event in store.audit_events(
        proposal["transaction_id"]
    )] == ["proposal_created"]


def test_vault_executor_success_uses_independently_encoded_transfer_calldata(
    monkeypatch,
):
    settings = _vault_settings(monkeypatch)
    factory, calls = _vault_rpc_factory(settings.private_key)
    executor = BaseSepoliaVaultExecutor(settings, rpc_factory=factory)
    signed_transactions = []
    signer = Account.sign_transaction

    def capture_and_sign(transaction, private_key):
        signed_transactions.append(transaction.copy())
        return signer(transaction, private_key)

    monkeypatch.setattr(Account, "sign_transaction", capture_and_sign)
    proposal = _vault_proposal()
    result = executor.prepare(proposal)

    fingerprint = hashlib.sha256(
        json.dumps(proposal, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    expected_calldata = (
        "0x"
        + keccak(text="transferToken(address,address,uint256,bytes32)")[:4].hex()
        + proposal["token_address"][2:].lower().rjust(64, "0")
        + proposal["recipient"][2:].lower().rjust(64, "0")
        + proposal["amount_base_units"].to_bytes(32, "big").hex()
        + fingerprint
    )
    assert signed_transactions[0]["to"].lower() == settings.vault_address
    assert signed_transactions[0]["value"] == 0
    assert signed_transactions[0]["data"] == expected_calldata
    simulated_call = next(
        call for call in calls
        if call["method"] == "eth_call"
        and call["params"][0]["data"].startswith(
            "0x" + keccak(text="transferToken(address,address,uint256,bytes32)")[:4].hex()
        )
    )
    assert simulated_call["params"][0]["to"] == settings.vault_address
    assert result.raw_transaction.startswith("0x")
    assert len(result.transaction_hash) == 66
    assert settings.private_key.removeprefix("0x") not in result.raw_transaction


@pytest.mark.parametrize(
    ("error_signature", "expected_code"),
    [
        (
            "TransferApprovalRequired(address,address,uint256,bytes32)",
            "onchain_approval_missing",
        ),
        (
            "DailyCapExceeded(uint256,uint256,uint256)",
            "vault_daily_cap_exceeded",
        ),
    ],
)
def test_vault_simulation_custom_reverts_are_clear_and_never_sign(
    monkeypatch, error_signature, expected_code
):
    settings = _vault_settings(monkeypatch)
    factory, calls = _vault_rpc_factory(
        settings.private_key,
        failure=error_signature,
    )
    executor = BaseSepoliaVaultExecutor(settings, rpc_factory=factory)
    sign_calls = []

    def reject_signing(*args, **kwargs):
        sign_calls.append((args, kwargs))
        raise AssertionError("reverted simulation must not sign")

    monkeypatch.setattr(Account, "sign_transaction", reject_signing)
    with pytest.raises(VaultExecutionError) as exc_info:
        executor.prepare(_vault_proposal())

    assert exc_info.value.code == expected_code
    assert not sign_calls
    assert not any(call["method"] == "eth_sendRawTransaction" for call in calls)


@pytest.mark.parametrize(
    ("field", "expected_code"),
    [
        ("chainId", "chain_id_mismatch"),
        ("to", "vault_address_mismatch"),
        ("value", "value_mismatch"),
        ("selector", "selector_mismatch"),
        ("token", "token_address_mismatch"),
        ("recipient", "recipient_mismatch"),
        ("amount", "amount_mismatch"),
        ("ref", "reference_mismatch"),
    ],
)
def test_mutated_vault_transaction_aborts_before_signing(
    monkeypatch, field, expected_code
):
    settings = _vault_settings(monkeypatch)
    factory, _ = _vault_rpc_factory(settings.private_key)
    executor = BaseSepoliaVaultExecutor(settings, rpc_factory=factory)
    original_validator = execution_module._validate_vault_unsigned_transaction
    sign_calls = []

    def mutate_then_validate(transaction, vault, token, recipient, amount, ref):
        if field == "chainId":
            transaction["chainId"] = 1
        elif field == "to":
            transaction["to"] = "0x1111111111111111111111111111111111111111"
        elif field == "value":
            transaction["value"] = 1
        elif field == "selector":
            transaction["data"] = "0xdeadbeef" + transaction["data"][10:]
        elif field == "token":
            transaction["data"] = transaction["data"][:34] + "1" * 40 + transaction["data"][74:]
        elif field == "recipient":
            transaction["data"] = transaction["data"][:98] + "1" * 40 + transaction["data"][138:]
        elif field == "amount":
            transaction["data"] = (
                transaction["data"][:138]
                + (amount + 1).to_bytes(32, "big").hex()
                + transaction["data"][202:]
            )
        else:
            transaction["data"] = transaction["data"][:202] + "0" * 64
        original_validator(transaction, vault, token, recipient, amount, ref)

    def reject_signing(*args, **kwargs):
        sign_calls.append((args, kwargs))
        raise AssertionError("mutated vault transaction must not sign")

    monkeypatch.setattr(
        execution_module, "_validate_vault_unsigned_transaction", mutate_then_validate
    )
    monkeypatch.setattr(Account, "sign_transaction", reject_signing)
    with pytest.raises(execution_module.VaultTransactionInvariantError) as exc_info:
        executor.prepare(_vault_proposal())
    assert exc_info.value.code == expected_code
    assert not sign_calls

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


@pytest.mark.parametrize(
    ("field", "error_code"),
    [
        ("chain", "chain_id_mismatch"),
        ("to", "token_address_mismatch"),
        ("value", "value_mismatch"),
        ("selector", "selector_mismatch"),
        ("recipient", "recipient_mismatch"),
        ("amount", "amount_mismatch"),
    ],
)
def test_mutated_unsigned_transaction_is_rejected_before_signing(
    isolated_app, monkeypatch, field, error_code
):
    store, agent_headers, owner_headers = isolated_app
    _mocked_executor(monkeypatch, _mock_methods(lambda _: "0x" + "22" * 32))
    original_validator = execution_module._validate_unsigned_transaction
    sign_calls = []

    def reject_signing(*args, **kwargs):
        sign_calls.append((args, kwargs))
        raise AssertionError("mutated transaction must not reach the signer")

    def mutate_then_validate(transaction, token_address, recipient, amount):
        if field == "chain":
            transaction["chainId"] = 1
        elif field == "to":
            transaction["to"] = "0x1111111111111111111111111111111111111111"
        elif field == "value":
            transaction["value"] = 1
        elif field == "selector":
            transaction["data"] = "0xdeadbeef" + transaction["data"][10:]
        elif field == "recipient":
            transaction["data"] = (
                transaction["data"][:34]
                + "1" * 40
                + transaction["data"][74:]
            )
        else:
            transaction["data"] = (
                transaction["data"][:74]
                + (amount + 1).to_bytes(32, "big").hex()
            )
        original_validator(transaction, token_address, recipient, amount)

    monkeypatch.setattr(
        execution_module, "_validate_unsigned_transaction", mutate_then_validate
    )
    monkeypatch.setattr(Account, "sign_transaction", reject_signing)
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
    assert response.json()["detail"]["code"] == error_code
    assert not sign_calls
    assert store.get(proposal["transaction_id"])["status"] == "ready"
    assert [event["event_type"] for event in store.audit_events(
        proposal["transaction_id"]
    )] == ["proposal_created"]


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
    policy_digest = hash_policy(main.DEMO_POLICY)
    assert events[1]["details"]["policy_hash"] == policy_digest
    assert events[-1]["details"] == {
        "transaction_hash": transaction_hash,
        "policy_hash": policy_digest,
    }
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
