"""Explicitly gated Base Sepolia transaction signing and execution."""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

from eth_account import Account
from eth_utils import keccak, to_checksum_address

from app.chain.base_sepolia import (
    BASE_SEPOLIA_CHAIN_ID,
    BaseSepoliaRpc,
    RpcRequestError,
    validate_address,
)
from app.policy.models import MAX_SQLITE_INTEGER

ExecutionMode = Literal["simulated", "base_sepolia_vault"]
_MAX_GAS_PRICE_WEI = 100_000_000_000
_MAX_GAS_LIMIT = 200_000
_VAULT_TRANSFER_SIGNATURE = "transferToken(address,address,uint256,bytes32)"
_VAULT_TRANSFER_SELECTOR = "0x" + keccak(text=_VAULT_TRANSFER_SIGNATURE)[:4].hex()


class ExecutionConfigurationError(ValueError):
    """Raised when execution-mode configuration is missing or invalid."""


class VaultExecutionError(RuntimeError):
    """Raised when vault identity checks or simulated policy reject a transfer."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class VaultTransactionInvariantError(RuntimeError):
    """Raised when unsigned vault calldata differs from the persisted proposal."""

    def __init__(self, code: str) -> None:
        super().__init__("Unsigned vault transaction does not match the stored proposal")
        self.code = code


_VAULT_REVERT_MESSAGES = {
    keccak(text="TransferApprovalRequired(address,address,uint256,bytes32)")[:4].hex():
        ("onchain_approval_missing", "On-chain owner approval is missing for this exact transfer."),
    keccak(text="TransferApprovalExpired(uint256)")[:4].hex():
        ("onchain_approval_expired", "On-chain owner approval for this transfer has expired."),
    keccak(text="DailyCapExceeded(uint256,uint256,uint256)")[:4].hex():
        ("vault_daily_cap_exceeded", "The vault daily spending cap would be exceeded."),
    keccak(text="PerTransactionCapExceeded(uint256,uint256)")[:4].hex():
        ("vault_transaction_cap_exceeded", "The vault per-transaction cap would be exceeded."),
    keccak(text="TokenNotAllowed(address)")[:4].hex():
        ("vault_token_disallowed", "The token is not allowed by the vault."),
    keccak(text="UnauthorizedAgent()")[:4].hex():
        ("vault_agent_mismatch", "The vault rejected this signer as its configured agent."),
    keccak(text="EnforcedPause()")[:4].hex():
        ("vault_paused", "The vault is paused."),
}


def _vault_revert_error(revert_data: str | None) -> VaultExecutionError:
    if isinstance(revert_data, str) and re.fullmatch(r"0x[0-9a-fA-F]{8,}", revert_data):
        selector = revert_data[2:10].lower()
        mapped = _VAULT_REVERT_MESSAGES.get(selector)
        if mapped is not None:
            return VaultExecutionError(*mapped)
    return VaultExecutionError(
        "vault_simulation_reverted",
        "Vault transfer simulation reverted; no transaction was signed.",
    )


def _vault_transfer_calldata(
    token_address: str,
    recipient: str,
    amount: int,
    ref: str,
) -> str:
    if not isinstance(ref, str) or re.fullmatch(r"0x[0-9a-fA-F]{64}", ref) is None:
        raise RpcRequestError("Stored proposal fingerprint is not a bytes32 value")
    return (
        _VAULT_TRANSFER_SELECTOR
        + validate_address(token_address)[2:].rjust(64, "0")
        + validate_address(recipient)[2:].rjust(64, "0")
        + amount.to_bytes(32, "big").hex()
        + ref[2:].lower()
    )


def _validate_vault_unsigned_transaction(
    transaction: Mapping[str, object],
    vault_address: str,
    token_address: str,
    recipient: str,
    amount: int,
    ref: str,
) -> None:
    if type(transaction.get("chainId")) is not int or transaction["chainId"] != BASE_SEPOLIA_CHAIN_ID:
        raise VaultTransactionInvariantError("chain_id_mismatch")
    transaction_to = transaction.get("to")
    if not isinstance(transaction_to, str):
        raise VaultTransactionInvariantError("vault_address_mismatch")
    try:
        if validate_address(transaction_to) != validate_address(vault_address):
            raise VaultTransactionInvariantError("vault_address_mismatch")
    except ValueError as exc:
        raise VaultTransactionInvariantError("vault_address_mismatch") from exc
    if type(transaction.get("value")) is not int or transaction["value"] != 0:
        raise VaultTransactionInvariantError("value_mismatch")

    data = transaction.get("data")
    if not isinstance(data, str) or re.fullmatch(r"0x[0-9a-fA-F]{264}", data) is None:
        raise VaultTransactionInvariantError("calldata_invalid")
    if data[:10].lower() != _VAULT_TRANSFER_SELECTOR:
        raise VaultTransactionInvariantError("selector_mismatch")
    words = [data[offset:offset + 64].lower() for offset in (10, 74, 138, 202)]
    if words[0][:24] != "0" * 24 or words[1][:24] != "0" * 24:
        raise VaultTransactionInvariantError("calldata_address_invalid")
    if "0x" + words[0][24:] != validate_address(token_address):
        raise VaultTransactionInvariantError("token_address_mismatch")
    if "0x" + words[1][24:] != validate_address(recipient):
        raise VaultTransactionInvariantError("recipient_mismatch")
    if int(words[2], 16) != amount:
        raise VaultTransactionInvariantError("amount_mismatch")
    if "0x" + words[3] != ref.lower():
        raise VaultTransactionInvariantError("reference_mismatch")


@dataclass(frozen=True)
class ExecutionSettings:
    mode: ExecutionMode
    rpc_url: str | None = field(default=None, repr=False)
    private_key: str | None = field(default=None, repr=False)
    vault_address: str | None = None

    @classmethod
    def from_environment(cls) -> ExecutionSettings:
        mode = os.environ.get("AEGIS_EXECUTION_MODE", "simulated")
        if mode not in {"simulated", "base_sepolia_vault"}:
            raise ExecutionConfigurationError(
                "AEGIS_EXECUTION_MODE must be 'simulated' or 'base_sepolia_vault'"
            )
        if mode == "simulated":
            return cls(mode="simulated")

        vault_address = None
        if mode == "base_sepolia_vault":
            raw_vault_address = os.environ.get("AEGIS_VAULT_ADDRESS", "")
            try:
                vault_address = validate_address(raw_vault_address)
            except (TypeError, ValueError) as exc:
                raise ExecutionConfigurationError(
                    "AEGIS_VAULT_ADDRESS must be a valid EVM address for base_sepolia_vault execution"
                ) from exc

        private_key = os.environ.get("AEGIS_TESTNET_PRIVATE_KEY", "")
        rpc_url = os.environ.get("BASE_SEPOLIA_RPC_URL", "")
        if not private_key.strip():
            raise ExecutionConfigurationError(
                "AEGIS_TESTNET_PRIVATE_KEY is required for base_sepolia_vault execution"
            )
        if not rpc_url.strip():
            raise ExecutionConfigurationError(
                "BASE_SEPOLIA_RPC_URL is required for base_sepolia_vault execution"
            )
        try:
            parsed_url = urlsplit(rpc_url)
            port = parsed_url.port
        except ValueError as exc:
            raise ExecutionConfigurationError(
                "BASE_SEPOLIA_RPC_URL must be an HTTPS URL for base_sepolia_vault execution"
            ) from exc
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or any(character.isspace() for character in parsed_url.netloc)
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ExecutionConfigurationError(
                "BASE_SEPOLIA_RPC_URL must be an HTTPS URL for base_sepolia_vault execution"
            )
        try:
            Account.from_key(private_key)
        except (TypeError, ValueError) as exc:
            raise ExecutionConfigurationError(
                "AEGIS_TESTNET_PRIVATE_KEY is invalid"
            ) from exc
        return cls(
            mode=mode,
            rpc_url=rpc_url,
            private_key=private_key,
            vault_address=vault_address,
        )


@dataclass(frozen=True)
class SignedTransfer:
    raw_transaction: str = field(repr=False)
    transaction_hash: str


class BaseSepoliaVaultExecutor:
    """Signs agent-only calls to the on-chain AegisVault enforcement boundary."""

    def __init__(
        self,
        settings: ExecutionSettings,
        rpc_factory: Callable[[str], BaseSepoliaRpc] = BaseSepoliaRpc,
    ) -> None:
        if (
            settings.mode != "base_sepolia_vault"
            or not settings.rpc_url
            or not settings.private_key
            or not settings.vault_address
        ):
            raise ExecutionConfigurationError(
                "Base Sepolia vault execution is not fully configured"
            )
        self._rpc_url = settings.rpc_url
        self._private_key = settings.private_key
        self._vault_address = validate_address(settings.vault_address)
        self._rpc_factory = rpc_factory

    def prepare(self, proposal: dict) -> SignedTransfer:
        try:
            if not isinstance(proposal, dict):
                raise TypeError
            chain_id = proposal["chain_id"]
            token_address = validate_address(proposal["token_address"])
            recipient = validate_address(proposal["recipient"])
            amount = proposal["amount_base_units"]
        except (KeyError, TypeError, ValueError) as exc:
            raise RpcRequestError(
                "Transaction proposal is invalid for Base Sepolia vault execution"
            ) from exc
        if (
            chain_id != BASE_SEPOLIA_CHAIN_ID
            or type(amount) is not int
            or not 0 < amount <= MAX_SQLITE_INTEGER
        ):
            raise RpcRequestError(
                "Transaction is not permitted for Base Sepolia vault execution"
            )

        canonical_proposal = json.dumps(proposal, sort_keys=True, separators=(",", ":"))
        fingerprint = "0x" + hashlib.sha256(canonical_proposal.encode("utf-8")).hexdigest()
        calldata = _vault_transfer_calldata(token_address, recipient, amount, fingerprint)
        account = Account.from_key(self._private_key)
        with self._rpc_factory(self._rpc_url) as rpc:
            if rpc.chain_id() != BASE_SEPOLIA_CHAIN_ID:
                raise RpcRequestError("RPC endpoint is not on Base Sepolia (84532)")
            code = rpc.get_code(self._vault_address)
            if code == "0x":
                raise VaultExecutionError(
                    "vault_code_missing",
                    "Configured AegisVault address has no contract code.",
                )

            owner_data = rpc.call(
                account.address,
                self._vault_address,
                "0x" + keccak(text="owner()")[:4].hex(),
            )
            agent_data = rpc.call(
                account.address,
                self._vault_address,
                "0x" + keccak(text="agent()")[:4].hex(),
            )
            owner = self._decode_vault_address(owner_data, "owner")
            agent = self._decode_vault_address(agent_data, "agent")
            signer = validate_address(account.address)
            if signer == owner:
                raise VaultExecutionError(
                    "vault_owner_agent_key_conflict",
                    "Vault execution signer is the owner; configure a separate agent session key.",
                )
            if signer != agent:
                raise VaultExecutionError(
                    "vault_agent_mismatch",
                    "Vault execution signer is not the configured agent address.",
                )

            transaction = {
                "chainId": BASE_SEPOLIA_CHAIN_ID,
                "to": to_checksum_address(self._vault_address),
                "value": 0,
                "data": calldata,
                "gas": 0,
            }
            _validate_vault_unsigned_transaction(
                transaction,
                self._vault_address,
                token_address,
                recipient,
                amount,
                fingerprint,
            )
            try:
                simulation_result = rpc.call(
                    account.address, self._vault_address, calldata
                )
            except RpcRequestError as exc:
                raise _vault_revert_error(exc.revert_data) from exc
            if simulation_result != "0x":
                raise VaultExecutionError(
                    "vault_simulation_invalid",
                    "Vault transfer simulation returned unexpected data; no transaction was signed.",
                )

            nonce = rpc.transaction_count(account.address)
            gas_price = rpc.gas_price()
            if gas_price > _MAX_GAS_PRICE_WEI:
                raise RpcRequestError(
                    "RPC gas price exceeds the configured execution safety limit"
                )
            estimated_gas = rpc.estimate_gas({
                "from": account.address,
                "to": self._vault_address,
                "value": "0x0",
                "data": calldata,
            })
            gas_limit = (estimated_gas * 120 + 99) // 100
            if not 0 < gas_limit <= _MAX_GAS_LIMIT:
                raise RpcRequestError(
                    "RPC gas estimate exceeds the configured execution safety limit"
                )
            transaction = {
                "chainId": BASE_SEPOLIA_CHAIN_ID,
                "nonce": nonce,
                "to": to_checksum_address(self._vault_address),
                "value": 0,
                "data": calldata,
                "gasPrice": gas_price,
                "gas": gas_limit,
            }
            _validate_vault_unsigned_transaction(
                transaction,
                self._vault_address,
                token_address,
                recipient,
                amount,
                fingerprint,
            )

        signed = Account.sign_transaction(transaction, self._private_key)
        raw_transaction = "0x" + bytes(signed.raw_transaction).hex()
        transaction_hash = "0x" + bytes(signed.hash).hex()
        return SignedTransfer(
            raw_transaction=raw_transaction,
            transaction_hash=transaction_hash,
        )

    @staticmethod
    def _decode_vault_address(data: str, function_name: str) -> str:
        if not isinstance(data, str) or re.fullmatch(r"0x[0-9a-fA-F]{64}", data) is None:
            raise VaultExecutionError(
                "vault_identity_response_invalid",
                f"AegisVault {function_name}() returned malformed address data.",
            )
        word = data[2:].lower()
        if word[:24] != "0" * 24:
            raise VaultExecutionError(
                "vault_identity_response_invalid",
                f"AegisVault {function_name}() returned malformed address data.",
            )
        return "0x" + word[24:]

    def broadcast(self, raw_transaction: str) -> str:
        with self._rpc_factory(self._rpc_url) as rpc:
            return rpc.send_raw_transaction(raw_transaction)

    def receipt(self, transaction_hash: str) -> dict[str, int | str | None]:
        with self._rpc_factory(self._rpc_url) as rpc:
            if rpc.chain_id() != BASE_SEPOLIA_CHAIN_ID:
                raise RpcRequestError("RPC endpoint is not on Base Sepolia (84532)")
            return rpc.transaction_receipt(transaction_hash)

    def receipt_status(
        self, transaction_hash: str, *, minimum_confirmations: int = 12
    ) -> dict[str, int | str | bool | None]:
        """Assess receipt inclusion against the RPC's current canonical block view."""
        if type(minimum_confirmations) is not int or minimum_confirmations < 1:
            raise ValueError("minimum_confirmations must be a positive integer")
        with self._rpc_factory(self._rpc_url) as rpc:
            if rpc.chain_id() != BASE_SEPOLIA_CHAIN_ID:
                raise RpcRequestError("RPC endpoint is not on Base Sepolia (84532)")
            receipt = rpc.transaction_receipt(transaction_hash)
            if receipt["status"] == "pending":
                return {
                    "transaction_hash": receipt["transaction_hash"],
                    "status": "pending",
                    "block_number": None,
                    "block_hash": None,
                    "confirmations_observed": 0,
                    "minimum_confirmations_required": minimum_confirmations,
                    "canonical_block_match": None,
                }

            block_number = receipt["block_number"]
            block_hash = receipt["block_hash"]
            if not isinstance(block_number, int) or not isinstance(block_hash, str):
                raise RpcRequestError("RPC returned an incomplete mined receipt")
            latest_block = rpc.block_number()
            if block_number > latest_block:
                raise RpcRequestError("RPC receipt block is ahead of the reported chain head")
            canonical_hash = rpc.block_hash_by_number(block_number)
            canonical_match = canonical_hash == block_hash.lower()
            confirmations = latest_block - block_number + 1
            if not canonical_match:
                status = "reorged"
            elif receipt["status"] == "failed":
                status = "failed"
            elif confirmations >= minimum_confirmations:
                status = "confirmed"
            else:
                status = "included"

            return {
                "transaction_hash": receipt["transaction_hash"],
                "status": status,
                "block_number": block_number,
                "block_hash": block_hash.lower(),
                "confirmations_observed": confirmations,
                "minimum_confirmations_required": minimum_confirmations,
                "canonical_block_match": canonical_match,
            }
