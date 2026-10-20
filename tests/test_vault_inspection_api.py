import pytest
from fastapi.testclient import TestClient

from app import main
from app.chain import router as chain_router
from eth_utils import keccak

client = TestClient(main.app)
OWNER_HEADERS = {"Authorization": "Bearer test-owner-token-with-enough-length"}
VAULT = "0x" + "1" * 40
TOKEN = "0x" + "2" * 40
OWNER = "0x" + "3" * 40
AGENT = "0x" + "4" * 40
ZERO = "0x" + "0" * 40


def _word(value: int) -> str:
    return f"{value:064x}"


def _address_word(address: str) -> str:
    return address[2:].lower().rjust(64, "0")


def _selector(signature: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex()


def test_vault_inspection_requires_owner_authentication(monkeypatch):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1])

    def unexpected_rpc():
        raise AssertionError("RPC must not be created for unauthenticated requests")

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", unexpected_rpc)
    response = client.get(
        f"/chain/base-sepolia/vault/{VAULT}/status",
        params={"token_address": TOKEN},
    )
    assert response.status_code == 401


def test_vault_inspection_rejects_invalid_addresses_before_rpc(monkeypatch):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1])

    def unexpected_rpc():
        raise AssertionError("RPC must not be created for invalid addresses")

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", unexpected_rpc)
    response = client.get(
        "/chain/base-sepolia/vault/not-an-address/status",
        params={"token_address": TOKEN},
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 422


def test_vault_inspection_refuses_wrong_chain_before_contract_calls(monkeypatch):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1])
    calls = []

    class FakeRpc:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def chain_id(self):
            return 1

        def get_code(self, _address):
            calls.append("get_code")
            return "0x6000"

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", FakeRpc)
    response = client.get(
        f"/chain/base-sepolia/vault/{VAULT}/status",
        params={"token_address": TOKEN},
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 503
    assert calls == []


def test_vault_inspection_reports_missing_bytecode_without_contract_calls(monkeypatch):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1])

    class FakeRpc:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def chain_id(self):
            return 84532

        def get_code(self, _address):
            return "0x"

        def call(self, *_args):
            raise AssertionError("Missing-code vault must not be queried")

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", FakeRpc)
    response = client.get(
        f"/chain/base-sepolia/vault/{VAULT}/status",
        params={"token_address": TOKEN},
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["has_contract_code"] is False
    assert body["configuration_matches_local"] is None
    assert body["read_only"] is True


def test_vault_inspection_decodes_configuration_and_checks_local_address(monkeypatch):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1])
    monkeypatch.setenv("AEGIS_VAULT_ADDRESS", VAULT.upper().replace("0X", "0x"))

    class FakeRpc:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def chain_id(self):
            return 84532

        def get_code(self, address):
            assert address == VAULT
            return "0x6000"

        def call(self, _sender, address, data):
            assert address == VAULT
            if data == _selector("owner()"):
                return "0x" + _address_word(OWNER)
            if data == _selector("agent()"):
                return "0x" + _address_word(AGENT)
            if data == _selector("paused()"):
                return "0x" + _word(0)
            if data == _selector("tokenPolicy(address)") + _address_word(TOKEN):
                return "0x" + _word(1) + _word(100) + _word(500) + _word(80)
            raise AssertionError(f"Unexpected call data: {data}")

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", FakeRpc)
    response = client.get(
        f"/chain/base-sepolia/vault/{VAULT}/status",
        params={"token_address": TOKEN},
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["owner"] == OWNER
    assert body["agent"] == AGENT
    assert body["paused"] is False
    assert body["token_policy"] == {
        "allowed": True,
        "per_transaction_cap_base_units": 100,
        "daily_cap_base_units": 500,
        "approval_threshold_base_units": 80,
    }
    assert body["configured_vault_matches"] is True
    assert body["configuration_structurally_valid"] is True
    assert body["issues"] == []
    assert body["read_only"] is True


@pytest.mark.parametrize(
    ("owner", "agent", "policy", "expected_issue"),
    [
        (ZERO, AGENT, (1, 100, 500, 80), "The vault owner is the zero address."),
        (OWNER, OWNER, (1, 100, 500, 80), "The vault owner and agent addresses are identical."),
        (OWNER, ZERO, (1, 100, 500, 80), "The vault agent is the zero address; execution is not configured."),
        (OWNER, AGENT, (0, 100, 500, 80), "The selected token is not enabled by the vault."),
        (OWNER, AGENT, (1, 0, 500, 80), "The selected token policy has a zero spending cap."),
    ],
)
def test_vault_inspection_flags_structurally_invalid_configuration(
    monkeypatch, owner, agent, policy, expected_issue
):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1])
    monkeypatch.delenv("AEGIS_VAULT_ADDRESS", raising=False)

    class FakeRpc:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def chain_id(self):
            return 84532

        def get_code(self, _address):
            return "0x6000"

        def call(self, _sender, _address, data):
            if data == _selector("owner()"):
                return "0x" + _address_word(owner)
            if data == _selector("agent()"):
                return "0x" + _address_word(agent)
            if data == _selector("paused()"):
                return "0x" + _word(0)
            if data == _selector("tokenPolicy(address)") + _address_word(TOKEN):
                return "0x" + "".join(_word(value) for value in policy)
            raise AssertionError(f"Unexpected call data: {data}")

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", FakeRpc)
    response = client.get(
        f"/chain/base-sepolia/vault/{VAULT}/status",
        params={"token_address": TOKEN},
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["configuration_structurally_valid"] is False
    assert expected_issue in body["issues"]


def test_vault_inspection_sanitises_malformed_abi_response(monkeypatch):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1])

    class FakeRpc:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def chain_id(self):
            return 84532

        def get_code(self, _address):
            return "0x6000"

        def call(self, *_args):
            raise chain_router.RpcRequestError("provider secret internal response")

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", FakeRpc)
    response = client.get(
        f"/chain/base-sepolia/vault/{VAULT}/status",
        params={"token_address": TOKEN},
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 502
    assert response.json() == {
        "detail": "Base Sepolia vault configuration is unavailable or malformed"
    }
    assert "provider secret" not in response.text
