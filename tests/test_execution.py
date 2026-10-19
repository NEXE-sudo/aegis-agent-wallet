"""Base Sepolia execution tests use mocked JSON-RPC transports only."""
import hashlib
import json
import secrets

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
    BaseSepoliaVaultExecutor,
    ExecutionConfigurationError,
    ExecutionSettings,
    VaultExecutionError,
)

from app.workflow.controller import TransactionController
from app.workflow.store import WorkflowStore

client = TestClient(main.app)


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


def test_execution_settings_rejects_direct_eoa_mode(monkeypatch):
    monkeypatch.setenv("AEGIS_EXECUTION_MODE", "base_sepolia")
    monkeypatch.setenv("AEGIS_TESTNET_PRIVATE_KEY", Account.create().key.hex())
    monkeypatch.setenv("BASE_SEPOLIA_RPC_URL", "https://rpc.invalid")

    with pytest.raises(ExecutionConfigurationError, match="AEGIS_EXECUTION_MODE"):
        ExecutionSettings.from_environment()


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

def test_agent_execution_requires_vault_mode(isolated_app, monkeypatch):
    _, agent_headers, _ = isolated_app
    proposal = client.post(
        "/transactions/propose",
        headers=agent_headers,
        json=_vault_proposal(),
    ).json()
    monkeypatch.setattr(main, "execution_settings", ExecutionSettings(mode="simulated"))

    response = client.post(
        f"/agent/transactions/{proposal['transaction_id']}/execute",
        headers=agent_headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "agent_execution_requires_vault"


def test_agent_execution_requires_agent_authentication(isolated_app, monkeypatch):
    _, agent_headers, owner_headers = isolated_app
    proposal = client.post(
        "/transactions/propose",
        headers=agent_headers,
        json=_vault_proposal(),
    ).json()
    settings = _vault_settings(monkeypatch)
    monkeypatch.setattr(main, "execution_settings", settings)

    response = client.post(
        f"/agent/transactions/{proposal['transaction_id']}/execute",
        headers=owner_headers,
    )

    assert response.status_code == 401


def test_agent_execution_routes_ready_proposal_through_vault_executor(
    isolated_app, monkeypatch
):
    store, agent_headers, _ = isolated_app
    proposal = client.post(
        "/transactions/propose",
        headers=agent_headers,
        json=_vault_proposal(),
    ).json()
    settings = _vault_settings(monkeypatch)
    monkeypatch.setattr(main, "execution_settings", settings)
    executor_marker = object()
    monkeypatch.setattr(
        main,
        "BaseSepoliaVaultExecutor",
        lambda configured: executor_marker,
    )
    execute_calls = []

    def execute(transaction_id, executor):
        execute_calls.append((transaction_id, executor))
        return "ready", store.get(transaction_id)

    monkeypatch.setattr(main.controller, "execute", execute)

    response = client.post(
        f"/agent/transactions/{proposal['transaction_id']}/execute",
        headers=agent_headers,
    )

    assert response.status_code == 200
    assert response.json()["transaction_id"] == proposal["transaction_id"]
    assert execute_calls == [(proposal["transaction_id"], executor_marker)]
