import pytest
from fastapi.testclient import TestClient

from app import main
from app.chain import router as chain_router

client = TestClient(main.app)
OWNER_HEADERS = {"Authorization": "Bearer test-owner-token-with-enough-length"}


def test_chain_status_requires_owner_authentication(monkeypatch):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1])
    response = client.get("/chain/base-sepolia/status")
    assert response.status_code == 401



@pytest.mark.parametrize(
    "headers",
    [None, {"Authorization": "Bearer incorrect-owner-token"}],
    ids=["missing-token", "invalid-token"],
)
def test_chain_token_endpoint_requires_owner_authentication(
    monkeypatch, headers
):
    monkeypatch.setenv(
        "AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1]
    )

    def unexpected_rpc():
        raise AssertionError("RPC client must not be created for an unauthenticated request")

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", unexpected_rpc)
    kwargs = {"headers": headers} if headers is not None else {}
    response = client.get(
        "/chain/base-sepolia/token/0x1111111111111111111111111111111111111111",
        **kwargs,
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing or invalid bearer token"}

def test_chain_status_fails_closed_without_rpc_url(monkeypatch):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1])
    monkeypatch.delenv("BASE_SEPOLIA_RPC_URL", raising=False)
    response = client.get("/chain/base-sepolia/status", headers=OWNER_HEADERS)
    assert response.status_code == 503
    assert "BASE_SEPOLIA_RPC_URL" in response.json()["detail"]


def test_chain_status_reports_read_only_mode(monkeypatch):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1])

    class FakeRpc:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def chain_id(self):
            return 84532

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", FakeRpc)
    response = client.get("/chain/base-sepolia/status", headers=OWNER_HEADERS)
    assert response.status_code == 200
    assert response.json() == {
        "connected": True,
        "chain_id": 84532,
        "is_base_sepolia": True,
        "expected_chain_id": 84532,
        "mode": "read-only",
    }



def test_chain_status_reports_wrong_chain_without_claiming_base_sepolia(monkeypatch):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1])

    class FakeRpc:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def chain_id(self):
            return 1

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", FakeRpc)
    response = client.get("/chain/base-sepolia/status", headers=OWNER_HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "connected": True,
        "chain_id": 1,
        "is_base_sepolia": False,
        "expected_chain_id": 84532,
        "mode": "read-only",
    }




@pytest.mark.parametrize(
    "address",
    [
        "not-an-address",
        "0x1234",
        "0x" + "g" * 40,
    ],
    ids=["not-hex-address", "too-short", "non-hex-character"],
)
def test_chain_token_endpoint_rejects_invalid_address_before_rpc(
    monkeypatch, address
):
    monkeypatch.setenv(
        "AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1]
    )

    def unexpected_rpc():
        raise AssertionError("RPC client must not be created for an invalid address")

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", unexpected_rpc)
    response = client.get(
        f"/chain/base-sepolia/token/{address}",
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Address must be a 20-byte hexadecimal EVM address"
    }

def test_chain_token_endpoint_returns_read_only_metadata(monkeypatch):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1])
    expected = {
        "address": "0x1111111111111111111111111111111111111111",
        "chain_id": 84532,
        "has_contract_code": True,
        "erc20_metadata_readable": True,
        "name": "Example Token",
        "symbol": "EXT",
        "decimals": 18,
        "verified": True,
        "warnings": ["Metadata readability does not prove token authenticity."],
    }

    class FakeRpc:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def verify_token(self, address):
            assert address == expected["address"]
            return expected

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", FakeRpc)
    response = client.get(
        "/chain/base-sepolia/token/0x1111111111111111111111111111111111111111",
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == expected


def test_chain_token_endpoint_preserves_unverified_no_code_result(monkeypatch):
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", OWNER_HEADERS["Authorization"].split()[-1])
    expected = {
        "address": "0x1111111111111111111111111111111111111111",
        "chain_id": 84532,
        "has_contract_code": False,
        "erc20_metadata_readable": False,
        "name": None,
        "symbol": None,
        "decimals": None,
        "verified": False,
        "warnings": ["No contract bytecode found at this address."],
    }

    class FakeRpc:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def verify_token(self, address):
            assert address == expected["address"]
            return expected

    monkeypatch.setattr(chain_router, "BaseSepoliaRpc", FakeRpc)
    response = client.get(
        "/chain/base-sepolia/token/0x1111111111111111111111111111111111111111",
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == expected
