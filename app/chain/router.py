"""Read-only Base Sepolia RPC endpoints."""
from fastapi import APIRouter, Depends, HTTPException

from app.chain.base_sepolia import (
    BASE_SEPOLIA_CHAIN_ID,
    BaseSepoliaRpc,
    RpcConfigurationError,
    RpcRequestError,
)
from app.main import require_owner

router = APIRouter(prefix="/chain/base-sepolia", tags=["read-only blockchain"], dependencies=[Depends(require_owner)])


@router.get("/status")
def rpc_status() -> dict:
    try:
        chain_id = BaseSepoliaRpc().chain_id()
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
        return BaseSepoliaRpc().verify_token(address)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RpcConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RpcRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
