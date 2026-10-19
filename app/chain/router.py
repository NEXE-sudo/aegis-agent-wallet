"""Read-only Base Sepolia RPC endpoints."""
from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_chain_owner
from app.chain.base_sepolia import (
    BASE_SEPOLIA_CHAIN_ID,
    BASE_SEPOLIA_USDC_ADDRESS,
    BaseSepoliaRpc,
    RpcConfigurationError,
    RpcRequestError,
    validate_address,
)

router = APIRouter(
    prefix="/chain/base-sepolia",
    tags=["read-only blockchain"],
    dependencies=[Depends(require_chain_owner)],
)


@router.get("/status")
def rpc_status() -> dict:
    try:
        with BaseSepoliaRpc() as rpc:
            chain_id = rpc.chain_id()
    except RpcConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RpcRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "connected": True,
        "chain_id": chain_id,
        "is_base_sepolia": chain_id == BASE_SEPOLIA_CHAIN_ID,
        "expected_chain_id": BASE_SEPOLIA_CHAIN_ID,
        "mode": "read-only",
    }


@router.get("/token/{address}")
def inspect_token(address: str) -> dict:
    try:
        normalized_address = validate_address(address)
        with BaseSepoliaRpc() as rpc:
            return rpc.verify_token(normalized_address)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RpcConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RpcRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

def _format_units(amount: int, decimals: int) -> str:
    """Format an integer token amount without floating-point rounding."""
    scale = 10**decimals
    whole, fraction = divmod(amount, scale)
    if decimals == 0 or fraction == 0:
        return str(whole)
    fractional_text = f"{fraction:0{decimals}d}".rstrip("0")
    return f"{whole}.{fractional_text}"


@router.get("/address/{address}/balances")
def get_address_balances(address: str) -> dict:
    """Read native ETH and the demo USDC balance for a public Base Sepolia address."""
    try:
        normalized_address = validate_address(address)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        with BaseSepoliaRpc() as rpc:
            chain_id = rpc.chain_id()
            if chain_id != BASE_SEPOLIA_CHAIN_ID:
                raise HTTPException(
                    status_code=503,
                    detail="Configured RPC endpoint is not Base Sepolia (84532)",
                )
            native_balance = rpc.eth_balance(normalized_address)
            token_balance = rpc.token_balance(
                BASE_SEPOLIA_USDC_ADDRESS, normalized_address
            )
    except HTTPException:
        raise
    except RpcConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RpcRequestError as exc:
        raise HTTPException(
            status_code=502,
            detail="Base Sepolia balance data is unavailable or malformed",
        ) from exc

    return {
        "address": normalized_address,
        "chain_id": chain_id,
        "native_balance": {
            "symbol": "ETH",
            "decimals": 18,
            "balance_base_units": native_balance,
            "formatted_balance": _format_units(native_balance, 18),
        },
        "tokens": [
            {
                "address": BASE_SEPOLIA_USDC_ADDRESS,
                "symbol": "USDC",
                "decimals": 6,
                "balance_base_units": token_balance,
                "formatted_balance": _format_units(token_balance, 6),
            }
        ],
        "read_only": True,
    }

