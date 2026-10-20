"""Read-only Base Sepolia RPC endpoints."""
import os
import re

from fastapi import APIRouter, Depends, HTTPException
from eth_utils import keccak

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


_ZERO_ADDRESS = "0x" + "0" * 40
_WORD_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


def _decode_vault_word(data: str, field: str) -> int:
    if not isinstance(data, str) or not _WORD_RE.fullmatch(data):
        raise RpcRequestError(f"Vault returned malformed {field} data")
    return int(data[2:], 16)


def _vault_read(rpc: BaseSepoliaRpc, vault: str, signature: str, token: str | None = None) -> str:
    calldata = "0x" + keccak(text=signature)[:4].hex()
    if token is not None:
        calldata += validate_address(token)[2:].rjust(64, "0")
    return rpc.call(_ZERO_ADDRESS, vault, calldata)


@router.get("/vault/{address}/status")
def inspect_vault_configuration(address: str, token_address: str) -> dict:
    """Read and structurally validate an AegisVault and one token's on-chain policy."""
    try:
        vault_address = validate_address(address)
        normalized_token = validate_address(token_address)
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
            code = rpc.get_code(vault_address)
            if code in {"", "0x", "0x0"}:
                return {
                    "address": vault_address,
                    "token_address": normalized_token,
                    "chain_id": chain_id,
                    "has_contract_code": False,
                    "configuration_matches_local": None,
                    "issues": ["No contract bytecode found at the supplied vault address."],
                    "read_only": True,
                }

            owner_word = _decode_vault_word(
                _vault_read(rpc, vault_address, "owner()"), "owner"
            )
            agent_word = _decode_vault_word(
                _vault_read(rpc, vault_address, "agent()"), "agent"
            )
            paused_word = _decode_vault_word(
                _vault_read(rpc, vault_address, "paused()"), "paused"
            )
            policy_data = rpc.call(
                _ZERO_ADDRESS,
                vault_address,
                "0x" + keccak(text="tokenPolicy(address)")[:4].hex()
                + normalized_token[2:].rjust(64, "0"),
            )
            if not isinstance(policy_data, str) or not re.fullmatch(
                r"0x[0-9a-fA-F]{256}", policy_data
            ):
                raise RpcRequestError("Vault returned malformed token policy data")
            words = [int(policy_data[2 + i * 64:2 + (i + 1) * 64], 16) for i in range(4)]
            if owner_word >> 160 or agent_word >> 160 or paused_word not in {0, 1} or words[0] not in {0, 1}:
                raise RpcRequestError("Vault returned invalid configuration values")

            owner = "0x" + f"{owner_word:040x}"
            agent = "0x" + f"{agent_word:040x}"
            configured_vault = os.environ.get("AEGIS_VAULT_ADDRESS", "").strip()
            local_match = None
            configuration_issues = []
            issues = []
            if configured_vault:
                try:
                    local_match = validate_address(configured_vault) == vault_address
                except ValueError:
                    local_match = False
                if not local_match:
                    configuration_issues.append(
                        "The supplied vault address does not match AEGIS_VAULT_ADDRESS."
                    )
            else:
                configuration_issues.append(
                    "AEGIS_VAULT_ADDRESS is not configured; local execution settings were not compared."
                )

            if owner == _ZERO_ADDRESS:
                issues.append("The vault owner is the zero address.")
            if agent == _ZERO_ADDRESS:
                issues.append("The vault agent is the zero address; execution is not configured.")
            if agent == owner:
                issues.append("The vault owner and agent addresses are identical.")
            if words[0] != 1:
                issues.append("The selected token is not enabled by the vault.")
            if words[1] == 0 or words[2] == 0:
                issues.append("The selected token policy has a zero spending cap.")

            return {
                "address": vault_address,
                "token_address": normalized_token,
                "chain_id": chain_id,
                "expected_chain_id": BASE_SEPOLIA_CHAIN_ID,
                "has_contract_code": True,
                "owner": owner,
                "agent": agent,
                "paused": bool(paused_word),
                "token_policy": {
                    "allowed": bool(words[0]),
                    "per_transaction_cap_base_units": words[1],
                    "daily_cap_base_units": words[2],
                    "approval_threshold_base_units": words[3],
                },
                "configured_vault_matches": local_match,
                "configuration_structurally_valid": not issues,
                "issues": issues,
                "configuration_issues": configuration_issues,
                "read_only": True,
            }
    except HTTPException:
        raise
    except RpcConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RpcRequestError as exc:
        raise HTTPException(
            status_code=502,
            detail="Base Sepolia vault configuration is unavailable or malformed",
        ) from exc


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

