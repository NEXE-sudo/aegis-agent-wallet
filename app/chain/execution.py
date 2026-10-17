"""Explicitly gated Base Sepolia transaction signing and execution."""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

from eth_account import Account
from eth_utils import to_checksum_address

from app.chain.base_sepolia import (
    BASE_SEPOLIA_CHAIN_ID,
    BaseSepoliaRpc,
    RpcRequestError,
    validate_address,
)
from app.policy.models import MAX_SQLITE_INTEGER, AgentPolicy

ExecutionMode = Literal["simulated", "base_sepolia"]
_MAX_GAS_PRICE_WEI = 100_000_000_000
_MAX_GAS_LIMIT = 200_000
_ERC20_TRANSFER_SELECTOR = "0xa9059cbb"


class ExecutionConfigurationError(ValueError):
    """Raised when execution-mode configuration is missing or invalid."""


class PreflightError(RuntimeError):
    """Raised when a transfer fails the read-only execution preflight."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class UnsignedTransactionInvariantError(RuntimeError):
    """Raised when the built transaction differs from its approved proposal."""

    def __init__(self, code: str) -> None:
        super().__init__("Unsigned transaction does not match the approved proposal")
        self.code = code


def _validate_unsigned_transaction(
    transaction: Mapping[str, object],
    token_address: str,
    recipient: str,
    amount: int,
) -> None:
    if type(transaction.get("chainId")) is not int or transaction["chainId"] != 84532:
        raise UnsignedTransactionInvariantError("chain_id_mismatch")

    transaction_to = transaction.get("to")
    if not isinstance(transaction_to, str):
        raise UnsignedTransactionInvariantError("token_address_mismatch")
    try:
        normalized_to = validate_address(transaction_to)
    except ValueError as exc:
        raise UnsignedTransactionInvariantError("token_address_mismatch") from exc
    if normalized_to != validate_address(token_address):
        raise UnsignedTransactionInvariantError("token_address_mismatch")

    value = transaction.get("value")
    if type(value) is not int or value != 0:
        raise UnsignedTransactionInvariantError("value_mismatch")

    data = transaction.get("data")
    if not isinstance(data, str) or re.fullmatch(r"0x[0-9a-fA-F]{136}", data) is None:
        raise UnsignedTransactionInvariantError("calldata_invalid")
    if data[:10].lower() != _ERC20_TRANSFER_SELECTOR:
        raise UnsignedTransactionInvariantError("selector_mismatch")

    recipient_word = data[10:74].lower()
    if recipient_word[:24] != "0" * 24:
        raise UnsignedTransactionInvariantError("recipient_mismatch")
    decoded_recipient = "0x" + recipient_word[24:]
    if decoded_recipient != validate_address(recipient):
        raise UnsignedTransactionInvariantError("recipient_mismatch")

    decoded_amount = int(data[74:138], 16)
    if decoded_amount != amount:
        raise UnsignedTransactionInvariantError("amount_mismatch")
@dataclass(frozen=True)
class ExecutionSettings:
    mode: ExecutionMode
    rpc_url: str | None = field(default=None, repr=False)
    private_key: str | None = field(default=None, repr=False)
    preflight_enabled: bool = True

    @classmethod
    def from_environment(cls) -> ExecutionSettings:
        mode = os.environ.get("AEGIS_EXECUTION_MODE", "simulated")
        if mode not in {"simulated", "base_sepolia"}:
            raise ExecutionConfigurationError(
                "AEGIS_EXECUTION_MODE must be 'simulated' or 'base_sepolia'"
            )
        preflight_value = os.environ.get("AEGIS_PREFLIGHT_ENABLED")
        if preflight_value is None:
            preflight_enabled = mode == "base_sepolia"
        elif preflight_value.lower() in {"true", "false"}:
            preflight_enabled = preflight_value.lower() == "true"
        else:
            raise ExecutionConfigurationError(
                "AEGIS_PREFLIGHT_ENABLED must be 'true' or 'false'"
            )
        if mode == "simulated":
            return cls(mode="simulated", preflight_enabled=preflight_enabled)

        private_key = os.environ.get("AEGIS_TESTNET_PRIVATE_KEY", "")
        rpc_url = os.environ.get("BASE_SEPOLIA_RPC_URL", "")
        if not private_key.strip():
            raise ExecutionConfigurationError(
                "AEGIS_TESTNET_PRIVATE_KEY is required for base_sepolia execution"
            )
        if not rpc_url.strip():
            raise ExecutionConfigurationError(
                "BASE_SEPOLIA_RPC_URL is required for base_sepolia execution"
            )
        try:
            parsed_url = urlsplit(rpc_url)
            port = parsed_url.port
        except ValueError as exc:
            raise ExecutionConfigurationError(
                "BASE_SEPOLIA_RPC_URL must be an HTTPS URL for base_sepolia execution"
            ) from exc
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or any(character.isspace() for character in parsed_url.netloc)
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ExecutionConfigurationError(
                "BASE_SEPOLIA_RPC_URL must be an HTTPS URL for base_sepolia execution"
            )
        try:
            Account.from_key(private_key)
        except (TypeError, ValueError) as exc:
            raise ExecutionConfigurationError(
                "AEGIS_TESTNET_PRIVATE_KEY is invalid"
            ) from exc
        return cls(
            mode="base_sepolia",
            rpc_url=rpc_url,
            private_key=private_key,
            preflight_enabled=preflight_enabled,
        )


@dataclass(frozen=True)
class SignedTransfer:
    raw_transaction: str = field(repr=False)
    transaction_hash: str


class BaseSepoliaExecutor:
    def __init__(
        self,
        settings: ExecutionSettings,
        policy: AgentPolicy,
        rpc_factory: Callable[[str], BaseSepoliaRpc] = BaseSepoliaRpc,
    ) -> None:
        if settings.mode != "base_sepolia" or not settings.rpc_url or not settings.private_key:
            raise ExecutionConfigurationError(
                "Base Sepolia execution is not fully configured"
            )
        self._rpc_url = settings.rpc_url
        self._private_key = settings.private_key
        self._policy = policy
        self._rpc_factory = rpc_factory
        self._preflight_enabled = settings.preflight_enabled

    def prepare(self, proposal: dict) -> SignedTransfer:
        try:
            chain_id = proposal["chain_id"]
            token_address = validate_address(proposal["token_address"])
            recipient = validate_address(proposal["recipient"])
            amount = proposal["amount_base_units"]
        except (KeyError, TypeError, ValueError) as exc:
            raise RpcRequestError("Transaction proposal is invalid for Base Sepolia execution") from exc
        trusted_token = self._policy.trusted_tokens.get(token_address)
        if (
            chain_id != BASE_SEPOLIA_CHAIN_ID
            or chain_id not in self._policy.allowed_chain_ids
            or token_address not in {
                validate_address(address) for address in self._policy.allowed_token_addresses
            }
            or trusted_token is None
            or trusted_token.chain_id != BASE_SEPOLIA_CHAIN_ID
            or trusted_token.symbol != proposal["token_symbol"]
            or trusted_token.decimals != proposal["token_decimals"]
            or type(amount) is not int
            or not 0 < amount <= MAX_SQLITE_INTEGER
        ):
            raise RpcRequestError("Transaction is not permitted for Base Sepolia execution")

        account = Account.from_key(self._private_key)
        transfer_data = (
            _ERC20_TRANSFER_SELECTOR
            + recipient[2:].rjust(64, "0")
            + amount.to_bytes(32, "big").hex()
        )
        with self._rpc_factory(self._rpc_url) as rpc:
            if rpc.chain_id() != BASE_SEPOLIA_CHAIN_ID:
                raise RpcRequestError("RPC endpoint is not on Base Sepolia (84532)")
            nonce = rpc.transaction_count(account.address)
            gas_price = rpc.gas_price()
            if gas_price > _MAX_GAS_PRICE_WEI:
                raise RpcRequestError("RPC gas price exceeds the configured execution safety limit")
            transaction = {
                "chainId": BASE_SEPOLIA_CHAIN_ID,
                "nonce": nonce,
                "to": to_checksum_address(token_address),
                "value": 0,
                "data": transfer_data,
                "gasPrice": gas_price,
                "gas": 0,
            }
            estimated_gas = rpc.estimate_gas({
                "from": account.address,
                "to": token_address,
                "value": "0x0",
                "data": transfer_data,
            })
            gas_limit = (estimated_gas * 120 + 99) // 100
            if not 0 < gas_limit <= _MAX_GAS_LIMIT:
                raise RpcRequestError("RPC gas estimate exceeds the configured execution safety limit")
            transaction["gas"] = gas_limit
            if self._preflight_enabled:
                try:
                    simulation_result = rpc.call(
                        account.address, token_address, transfer_data
                    )
                except RpcRequestError as exc:
                    raise PreflightError(
                        "preflight_call_failed",
                        "Transfer simulation reverted or returned invalid data.",
                    ) from exc
                if simulation_result and (
                    len(simulation_result) != 66
                    or int(simulation_result, 16) != 1
                ):
                    raise PreflightError(
                        "preflight_call_failed",
                        "Transfer simulation did not confirm a successful transfer.",
                    )
                try:
                    token_balance = rpc.token_balance(token_address, account.address)
                    eth_balance = rpc.eth_balance(account.address)
                except RpcRequestError as exc:
                    raise PreflightError(
                        "preflight_rpc_error",
                        "A balance preflight response was invalid or unavailable.",
                    ) from exc
                if token_balance < amount:
                    raise PreflightError(
                        "insufficient_token_balance",
                        "Sender token balance is below the proposed transfer amount.",
                    )
                if eth_balance < gas_limit * gas_price:
                    raise PreflightError(
                        "insufficient_eth_balance",
                        "Sender ETH balance is below the maximum transaction gas cost.",
                    )

        _validate_unsigned_transaction(transaction, token_address, recipient, amount)
        signed = Account.sign_transaction(transaction, self._private_key)
        raw_transaction = "0x" + bytes(signed.raw_transaction).hex()
        transaction_hash = "0x" + bytes(signed.hash).hex()
        return SignedTransfer(
            raw_transaction=raw_transaction,
            transaction_hash=transaction_hash,
        )

    def broadcast(self, raw_transaction: str) -> str:
        with self._rpc_factory(self._rpc_url) as rpc:
            return rpc.send_raw_transaction(raw_transaction)

    def receipt(self, transaction_hash: str) -> dict[str, int | str | None]:
        with self._rpc_factory(self._rpc_url) as rpc:
            if rpc.chain_id() != BASE_SEPOLIA_CHAIN_ID:
                raise RpcRequestError("RPC endpoint is not on Base Sepolia (84532)")
            return rpc.transaction_receipt(transaction_hash)
