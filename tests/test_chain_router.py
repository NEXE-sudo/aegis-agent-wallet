"""Read-only Base Sepolia wallet balance endpoint tests."""
import pytest
from fastapi.testclient import TestClient

from app import main
from app.chain import router as chain_router
from app.chain.base_sepolia import BASE_SEPOLIA_CHAIN_ID, BASE_SEPOLIA_USDC_ADDRESS

client = TestClient(main.app)
ADDRESS = "0x2222222222222222222222222222222222222222"
OWNER_HEADERS = {"Authorization": "Bearer test-owner-token"}
AGENT_HEADERS = {"Authorization": "Bearer test-agent-token"}


@pytest.fixture(autouse=True)
def configure_tokens(monkeypatch):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", "test-owner-token")
    monkeypatch.setenv("AEGIS_AGENT_TOKEN", "test-agent-token")


class FakeRpc:
    def __init__(
        self,
        *,
        chain_id=BASE_SEPOLIA_CHAIN_ID,
        native_balance=1_234_500_000_000_000_000,
        token_balance=1_234_567,
        failure=None,
    ):
        self.configured_chain_id = chain_id
        self.native_balance = native_balance
        self.token_balance_value = token_balance
        self.failure = failure
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def chain_id(self):
        if self.failure == "chain":
            raise chain_router.RpcRequestError("RPC chain lookup failed")
        return self.configured_chain_id

    def eth_balance(self, address):
        self.calls.append(("eth_balance", address))
        if self.failure == "eth":
            raise chain_router.RpcRequestError("RPC balance lookup failed")
        return self.native_balance

    def token_balance(self, token_address, owner_address):
        self.calls.append(("token_balance", token_address, owner_address))
        if self.failure == "token":
            raise chain_router.RpcRequestError("RPC token lookup failed")
        return self.token_balance_value


def test_wallet_balances_return_exact_base_units_and_formatted_values(monkeypatch):
    rpc = FakeRpc()
    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", lambda: rpc)

    response = client.get(
        f"/chain/base-sepolia/address/{ADDRESS}/balances",
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == {
        "address": ADDRESS.lower(),
        "chain_id": BASE_SEPOLIA_CHAIN_ID,
        "native_balance": {
            "symbol": "ETH",
            "decimals": 18,
            "balance_base_units": 1_234_500_000_000_000_000,
            "formatted_balance": "1.2345",
        },
        "tokens": [
            {
                "address": BASE_SEPOLIA_USDC_ADDRESS,
                "symbol": "USDC",
                "decimals": 6,
                "balance_base_units": 1_234_567,
                "formatted_balance": "1.234567",
            }
        ],
        "read_only": True,
    }
    assert rpc.calls == [
        ("eth_balance", ADDRESS.lower()),
        ("token_balance", BASE_SEPOLIA_USDC_ADDRESS, ADDRESS.lower()),
    ]


def test_wallet_balances_require_owner_authentication():
    response = client.get(f"/chain/base-sepolia/address/{ADDRESS}/balances")

    assert response.status_code == 401


def test_wallet_balances_reject_malformed_address_before_rpc(monkeypatch):
    def unexpected_rpc():
        pytest.fail("RPC must not be opened for an invalid address")

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", unexpected_rpc)

    response = client.get(
        "/chain/base-sepolia/address/not-an-address/balances",
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 422


def test_wallet_balances_refuse_a_non_base_sepolia_rpc(monkeypatch):
    rpc = FakeRpc(chain_id=1)
    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", lambda: rpc)

    response = client.get(
        f"/chain/base-sepolia/address/{ADDRESS}/balances",
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 503
    assert "not Base Sepolia" in response.json()["detail"]
    assert rpc.calls == []


@pytest.mark.parametrize("failure", ["chain", "eth", "token"])
def test_wallet_balances_sanitise_rpc_failures(monkeypatch, failure):
    rpc = FakeRpc(failure=failure)
    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", lambda: rpc)

    response = client.get(
        f"/chain/base-sepolia/address/{ADDRESS}/balances",
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "Base Sepolia balance data is unavailable or malformed"
    }
