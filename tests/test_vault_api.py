import pytest
from eth_utils import keccak
from fastapi.testclient import TestClient

from app import main
from app.chain.base_sepolia import BASE_SEPOLIA_USDC_ADDRESS
from app.workflow.controller import TransactionController
from app.workflow.store import WorkflowStore

AGENT_TOKEN = "test-agent-token-with-enough-length"
OWNER_TOKEN = "test-owner-token-with-enough-length"
AGENT_HEADERS = {"Authorization": f"Bearer {AGENT_TOKEN}"}
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
VAULT_ADDRESS = "0x9999999999999999999999999999999999999999"
RECIPIENT = "0x2222222222222222222222222222222222222222"
OWNER_ADDRESS = "0x1111111111111111111111111111111111111111"
AGENT_ADDRESS = "0x3333333333333333333333333333333333333333"
client = TestClient(main.app)


def _payload(**overrides):
    payload = {
        "agent_id": "devops-01",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": BASE_SEPOLIA_USDC_ADDRESS,
        "recipient": RECIPIENT,
        "amount_base_units": 50_000_000,
        "token_decimals": 6,
    }
    payload.update(overrides)
    return payload


def _selector(signature):
    return "0x" + keccak(text=signature)[:4].hex()


def _word(value):
    return value.to_bytes(32, "big").hex()


def _address_word(address):
    return address[2:].lower().rjust(64, "0")


def _fake_rpc(monkeypatch, *, cap_override=None, recipient_override=None, malformed=None):
    expected_recipients = {
        address.lower() for address in main.DEMO_POLICY.allowed_recipients
    }
    recipient_allowlist = {
        address.lower(): {token.lower() for token in tokens}
        for address, tokens in main.DEMO_POLICY.recipient_token_allowlist.items()
    }

    class FakeRpc:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def chain_id(self):
            return 84532

        def get_code(self, address):
            assert address == VAULT_ADDRESS
            return "0x6000"

        def call(self, sender, address, data):
            assert sender == "0x" + "0" * 40
            assert address == VAULT_ADDRESS
            selector, arguments = data[:10], data[10:]
            if selector == _selector("owner()"):
                return "0x12" if malformed == "owner" else "0x" + _address_word(OWNER_ADDRESS)
            if selector == _selector("agent()"):
                return "0x" + _address_word(AGENT_ADDRESS)
            if selector == _selector("paused()"):
                return "0x" + _word(1)
            if selector == _selector("tokenPolicy(address)"):
                token = "0x" + arguments[-40:]
                assert token == BASE_SEPOLIA_USDC_ADDRESS
                cap = cap_override or main.DEMO_POLICY.max_transaction_base_units
                return "0x" + "".join(
                    (
                        _word(1),
                        _word(cap),
                        _word(main.DEMO_POLICY.daily_limit_base_units),
                        _word(main.DEMO_POLICY.approval_threshold_base_units),
                    )
                )
            if selector == _selector("todaySpend(address)"):
                return "0x" + _word(12_345)
            if selector == _selector("recipients(address)"):
                recipient = "0x" + arguments[-40:]
                allowed = recipient in expected_recipients
                tokens = recipient_allowlist.get(recipient)
                if tokens is not None and BASE_SEPOLIA_USDC_ADDRESS not in tokens:
                    allowed = False
                if recipient_override is not None and recipient == RECIPIENT:
                    allowed = recipient_override
                return "0x" + _word(int(allowed))
            raise AssertionError(f"Unexpected vault call selector: {selector}")

    monkeypatch.setattr(main, "BaseSepoliaRpc", lambda: FakeRpc())


def _setup_api(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_AGENT_TOKEN", AGENT_TOKEN)
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_TOKEN)
    monkeypatch.setenv("AEGIS_VAULT_ADDRESS", VAULT_ADDRESS)
    monkeypatch.setenv("BASE_SEPOLIA_RPC_URL", "https://rpc.invalid")
    store = WorkflowStore(tmp_path / "vault-api.sqlite3")
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "controller", TransactionController(main.DEMO_POLICY, store))
    return store


def test_vault_approval_helper_returns_independently_encoded_calldata(
    tmp_path, monkeypatch
):
    _setup_api(tmp_path, monkeypatch)
    proposed = client.post(
        "/transactions/propose",
        headers=AGENT_HEADERS,
        json=_payload(),
    )
    assert proposed.status_code == 201

    response = client.get(
        f"/transactions/{proposed.json()['transaction_id']}/vault-approval",
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 200
    result = response.json()
    args = result["arguments"]
    reference = bytes.fromhex(args["ref"][2:])
    selector = keccak(
        text="approveTransfer(address,address,uint256,bytes32,uint256)"
    )[:4]
    independent_calldata = (
        "0x"
        + selector.hex()
        + _address_word(args["token"])
        + _address_word(args["to"])
        + args["amount"].to_bytes(32, "big").hex()
        + reference.hex()
        + args["expiry"].to_bytes(32, "big").hex()
    )
    assert result["vault_address"] == VAULT_ADDRESS
    assert result["function_signature"] == (
        "approveTransfer(address,address,uint256,bytes32,uint256)"
    )
    assert result["calldata"] == independent_calldata
    assert args["ref"] == "0x" + proposed.json()["fingerprint"]
    assert args["expiry"] > 0
    assert "$OWNER_PRIVATE_KEY" in result["cast_send_command"]
    assert str(args["expiry"]) in result["cast_send_command"]


def test_vault_approval_helper_refuses_unknown_token_without_rpc(tmp_path, monkeypatch):
    _setup_api(tmp_path, monkeypatch)
    proposed = client.post(
        "/transactions/propose",
        headers=AGENT_HEADERS,
        json=_payload(token_address="0x4444444444444444444444444444444444444444"),
    )
    assert proposed.status_code == 201

    def unexpected_rpc():
        raise AssertionError("approval helper must not make RPC calls")

    monkeypatch.setattr(main, "BaseSepoliaRpc", unexpected_rpc)
    response = client.get(
        f"/transactions/{proposed.json()['transaction_id']}/vault-approval",
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "vault_approval_token_unsupported"


@pytest.mark.parametrize(
    "headers",
    [None, AGENT_HEADERS],
    ids=["missing-auth", "agent-token"],
)
@pytest.mark.parametrize(
    "route",
    ["status", "approval"],
)
def test_vault_routes_reject_missing_or_agent_authentication(
    tmp_path, monkeypatch, headers, route
):
    _setup_api(tmp_path, monkeypatch)
    if route == "approval":
        proposed = client.post(
            "/transactions/propose",
            headers=AGENT_HEADERS,
            json=_payload(),
        )
        assert proposed.status_code == 201
        path = f"/transactions/{proposed.json()['transaction_id']}/vault-approval"
    else:
        path = "/chain/base-sepolia/vault/status"
    kwargs = {"headers": headers} if headers is not None else {}
    response = client.get(path, **kwargs)
    assert response.status_code == 401


def test_vault_status_reports_synced_policy(tmp_path, monkeypatch):
    _setup_api(tmp_path, monkeypatch)
    _fake_rpc(monkeypatch)
    response = client.get(
        "/chain/base-sepolia/vault/status",
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 200
    result = response.json()
    assert result["chain_id"] == 84532
    assert result["owner"] == OWNER_ADDRESS
    assert result["agent"] == AGENT_ADDRESS
    assert result["paused"] is True
    assert result["in_sync"] is True
    assert result["differences"] == []
    assert result["tokens"][0]["today_spend_base_units"] == 12_345


def test_vault_status_lists_cap_and_recipient_differences(tmp_path, monkeypatch):
    _setup_api(tmp_path, monkeypatch)
    _fake_rpc(
        monkeypatch,
        cap_override=main.DEMO_POLICY.max_transaction_base_units - 1,
        recipient_override=False,
    )
    response = client.get(
        "/chain/base-sepolia/vault/status",
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 200
    result = response.json()
    assert result["in_sync"] is False
    assert {
        difference["field"] for difference in result["differences"]
    } == {
        f"tokens.{BASE_SEPOLIA_USDC_ADDRESS}.per_tx_cap_base_units",
        f"recipients.{RECIPIENT}.{BASE_SEPOLIA_USDC_ADDRESS}",
    }


def test_vault_status_rejects_malformed_rpc_data(tmp_path, monkeypatch):
    _setup_api(tmp_path, monkeypatch)
    _fake_rpc(monkeypatch, malformed="owner")
    response = client.get(
        "/chain/base-sepolia/vault/status",
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "AegisVault status response is unavailable or malformed"
