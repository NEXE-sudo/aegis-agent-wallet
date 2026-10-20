"""Capture a portable, read-only snapshot of an AegisVault deployment."""
from __future__ import annotations

import re
from typing import Any

from eth_utils import keccak

from app.chain.base_sepolia import (
    BASE_SEPOLIA_CHAIN_ID,
    BaseSepoliaRpc,
    RpcRequestError,
    validate_address,
)

_ZERO_ADDRESS = "0x" + "0" * 40
_WORD_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_BYTECODE_RE = re.compile(r"^0x(?:[0-9a-fA-F]{2})+$")


def _read_word(rpc: BaseSepoliaRpc, vault: str, signature: str, token: str | None = None) -> int:
    calldata = "0x" + keccak(text=signature)[:4].hex()
    if token is not None:
        calldata += validate_address(token)[2:].rjust(64, "0")
    value = rpc.call(_ZERO_ADDRESS, vault, calldata)
    if not isinstance(value, str) or _WORD_RE.fullmatch(value) is None:
        raise RpcRequestError(f"Vault returned malformed {signature} data")
    return int(value, 16)


def capture_vault_configuration(
    rpc: BaseSepoliaRpc,
    vault_address: str,
    token_address: str,
) -> dict[str, Any]:
    """Capture deployment identity and current policy, without signing or submitting."""
    vault = validate_address(vault_address)
    token = validate_address(token_address)
    chain_id = rpc.chain_id()
    if chain_id != BASE_SEPOLIA_CHAIN_ID:
        raise ValueError(
            f"RPC endpoint is on chain {chain_id}, not Base Sepolia ({BASE_SEPOLIA_CHAIN_ID})"
        )

    code = rpc.get_code(vault)
    if not isinstance(code, str) or _BYTECODE_RE.fullmatch(code) is None:
        raise RpcRequestError("RPC returned malformed or empty AegisVault bytecode")
    bytecode_hash = "0x" + keccak(hexstr=code).hex()

    owner_word = _read_word(rpc, vault, "owner()")
    agent_word = _read_word(rpc, vault, "agent()")
    paused_word = _read_word(rpc, vault, "paused()")
    allowed_word = _read_word(rpc, vault, "tokenPolicy(address)", token)
    per_tx_cap = _read_word(rpc, vault, "tokenPolicy(address)", token)
    # tokenPolicy(address) returns four ABI words, so read the complete tuple once.
    policy_call = "0x" + keccak(text="tokenPolicy(address)")[:4].hex()
    policy_call += token[2:].rjust(64, "0")
    policy_data = rpc.call(_ZERO_ADDRESS, vault, policy_call)
    if not isinstance(policy_data, str) or not re.fullmatch(r"0x[0-9a-fA-F]{256}", policy_data):
        raise RpcRequestError("Vault returned malformed token policy data")
    policy_words = [
        int(policy_data[2 + index * 64:2 + (index + 1) * 64], 16)
        for index in range(4)
    ]
    allowed_word, per_tx_cap, daily_cap, approval_threshold = policy_words
    if (
        owner_word >= 2**160
        or agent_word >= 2**160
        or paused_word not in {0, 1}
        or allowed_word not in {0, 1}
    ):
        raise RpcRequestError("Vault returned invalid configuration values")

    owner = f"0x{owner_word:040x}"
    agent = f"0x{agent_word:040x}"
    issues = []
    if owner == _ZERO_ADDRESS:
        issues.append("The vault owner is the zero address.")
    if agent == _ZERO_ADDRESS:
        issues.append("The vault agent is the zero address.")
    if owner == agent:
        issues.append("The vault owner and agent addresses are identical.")
    if allowed_word != 1:
        issues.append("The selected token is not enabled by the vault.")
    if per_tx_cap == 0 or daily_cap == 0:
        issues.append("The selected token policy has a zero spending cap.")

    return {
        "chain_id": chain_id,
        "vault_address": vault,
        "bytecode_sha256": bytecode_hash,
        "owner": owner,
        "agent": agent,
        "paused": bool(paused_word),
        "token_address": token,
        "token_policy": {
            "allowed": bool(allowed_word),
            "per_transaction_cap_base_units": per_tx_cap,
            "daily_cap_base_units": daily_cap,
            "approval_threshold_base_units": approval_threshold,
        },
        "configuration_structurally_valid": not issues,
        "issues": issues,
    }
