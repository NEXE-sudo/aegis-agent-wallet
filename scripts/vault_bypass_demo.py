"""Demonstrate AegisVault enforcement boundaries on Base Sepolia."""
from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from collections.abc import Callable
from urllib.parse import urlsplit

from eth_account import Account
from eth_utils import keccak

from app.chain.base_sepolia import (
    BASE_SEPOLIA_CHAIN_ID,
    BaseSepoliaRpc,
    RpcConfigurationError,
    RpcRequestError,
    validate_address,
)

VAULT_TRANSFER_SIGNATURE = "transferToken(address,address,uint256,bytes32)"
VAULT_TRANSFER_SELECTOR = "0x" + keccak(text=VAULT_TRANSFER_SIGNATURE)[:4].hex()
_MAX_GAS_PRICE_WEI = 100_000_000_000
_BYPASS_GAS_LIMIT = 150_000

_REVERT_MESSAGES = {
    keccak(text="PerTransactionCapExceeded(uint256,uint256)")[:4].hex():
        "per-transaction cap exceeded",
    keccak(text="TransferApprovalRequired(address,address,uint256,bytes32)")[:4].hex():
        "owner approval required",
    keccak(text="TokenNotAllowed(address)")[:4].hex():
        "token is not allowed",
    keccak(text="DailyCapExceeded(uint256,uint256,uint256)")[:4].hex():
        "daily cap exceeded",
    keccak(text="EnforcedPause()")[:4].hex():
        "vault is paused",
    keccak(text="UnauthorizedAgent()")[:4].hex():
        "caller is not the configured agent",
}


class BypassDemoError(ValueError):
    """Raised when a bypass demonstration cannot be run safely."""


def _word(value: int) -> str:
    if type(value) is not int or not 0 <= value < 2**256:
        raise BypassDemoError("ABI integer argument is out of range")
    return value.to_bytes(32, "big").hex()


def _address_word(value: str) -> str:
    return validate_address(value)[2:].rjust(64, "0")


def _read_word(rpc: BaseSepoliaRpc, sender: str, vault: str, signature: str, args: str = "") -> str:
    selector = "0x" + keccak(text=signature)[:4].hex()
    result = rpc.call(sender, vault, selector + args)
    if re.fullmatch(r"0x[0-9a-fA-F]{64}", result) is None:
        raise RpcRequestError(f"AegisVault {signature} returned malformed data")
    return result[2:].lower()


def _decode_revert(revert_data: str | None) -> str:
    if isinstance(revert_data, str) and re.fullmatch(r"0x[0-9a-fA-F]{8,}", revert_data):
        message = _REVERT_MESSAGES.get(revert_data[2:10].lower())
        if message is not None:
            return message
    return "contract reverted with an unrecognized custom error"


def _calldata(token: str, recipient: str, amount: int, reference: bytes) -> str:
    if len(reference) != 32:
        raise BypassDemoError("Transfer reference must be 32 bytes")
    return (
        VAULT_TRANSFER_SELECTOR
        + _address_word(token)
        + _address_word(recipient)
        + _word(amount)
        + reference.hex()
    )


def _private_key_from_environment() -> bytes:
    private_key = os.environ.get("AEGIS_TESTNET_PRIVATE_KEY", "")
    if not private_key.strip():
        raise BypassDemoError("AEGIS_TESTNET_PRIVATE_KEY is required")
    try:
        return bytes(Account.from_key(private_key).key)
    except (TypeError, ValueError) as exc:
        raise BypassDemoError("AEGIS_TESTNET_PRIVATE_KEY is invalid") from exc


def _rpc_url_from_environment() -> str:
    rpc_url = os.environ.get("BASE_SEPOLIA_RPC_URL", "")
    try:
        parsed = urlsplit(rpc_url)
        port = parsed.port
    except ValueError as exc:
        raise BypassDemoError("BASE_SEPOLIA_RPC_URL must be an HTTPS URL") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or any(character.isspace() for character in parsed.netloc)
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise BypassDemoError("BASE_SEPOLIA_RPC_URL must be an HTTPS URL")
    return rpc_url


def run_bypass_demo(
    *,
    vault_address: str,
    token_address: str,
    unlisted_recipient: str,
    private_key: bytes,
    broadcast: bool = False,
    rpc_factory: Callable[[str], BaseSepoliaRpc] = BaseSepoliaRpc,
    rpc_url: str | None = None,
    output: Callable[[str], object] = print,
) -> int:
    vault = validate_address(vault_address)
    token = validate_address(token_address)
    recipient = validate_address(unlisted_recipient)
    if recipient == "0x" + "0" * 40 or recipient == vault:
        raise BypassDemoError("The unlisted recipient must be nonzero and distinct from the vault")
    signer = Account.from_key(private_key)
    url = rpc_url or _rpc_url_from_environment()

    with rpc_factory(url) as rpc:
        chain_id = rpc.chain_id()
        if chain_id != BASE_SEPOLIA_CHAIN_ID:
            raise BypassDemoError(
                f"RPC endpoint is on chain {chain_id}, not Base Sepolia (84532)"
            )
        if rpc.get_code(vault) in {"0x", "0x0"}:
            raise BypassDemoError("Configured AegisVault address has no contract code")

        owner_word = _read_word(rpc, signer.address, vault, "owner()")
        agent_word = _read_word(rpc, signer.address, vault, "agent()")
        if owner_word[:24] != "0" * 24 or agent_word[:24] != "0" * 24:
            raise BypassDemoError("AegisVault returned malformed identity data")
        if signer.address.lower() != "0x" + agent_word[24:]:
            raise BypassDemoError("Configured key is not the AegisVault agent key")
        if signer.address.lower() == "0x" + owner_word[24:]:
            raise BypassDemoError("The agent key must be distinct from the vault owner key")

        policy_data = rpc.call(
            signer.address,
            vault,
            "0x"
            + keccak(text="tokenPolicy(address)")[:4].hex()
            + _address_word(token),
        )
        if re.fullmatch(r"0x[0-9a-fA-F]{256}", policy_data) is None:
            raise RpcRequestError("AegisVault tokenPolicy(address) returned malformed data")
        policy_words = [
            policy_data[2 + offset:2 + offset + 64].lower()
            for offset in range(0, 256, 64)
        ]
        allowed_word = int(policy_words[0], 16)
        if allowed_word not in (0, 1):
            raise RpcRequestError("AegisVault tokenPolicy(address) returned malformed boolean data")
        if allowed_word != 1:
            raise BypassDemoError("The supplied token is not currently allowed; cannot exceed its cap")
        per_tx_cap = int(policy_words[1], 16)
        daily_cap = int(policy_words[2], 16)
        if per_tx_cap <= 0 or daily_cap <= 0:
            raise BypassDemoError("The supplied token has no active spending cap")

        recipient_word = _read_word(
            rpc,
            signer.address,
            vault,
            "recipients(address)",
            _address_word(recipient),
        )
        if int(recipient_word, 16) not in (0, 1):
            raise RpcRequestError("AegisVault recipients(address) returned malformed boolean data")
        if int(recipient_word, 16) == 1:
            raise BypassDemoError("The selected recipient is allowlisted; choose an unlisted address")

        spend_word = _read_word(
            rpc,
            signer.address,
            vault,
            "todaySpend(address)",
            _address_word(token),
        )
        daily_spend = int(spend_word, 16)
        if daily_spend >= daily_cap:
            raise BypassDemoError(
                "Today's token budget is exhausted; the unlisted-recipient case cannot be isolated"
            )

        scenarios = (
            (
                "over-per-transaction-cap transfer",
                token,
                recipient,
                per_tx_cap + 1,
                "per-transaction cap exceeded",
            ),
            (
                "unlisted recipient without owner approval",
                token,
                recipient,
                1,
                "owner approval required",
            ),
            (
                "disallowed token transfer",
                vault,
                recipient,
                1,
                "token is not allowed",
            ),
        )
        failures = 0
        for name, transfer_token, transfer_recipient, amount, expected_reason in scenarios:
            calldata = _calldata(
                transfer_token,
                transfer_recipient,
                amount,
                secrets.token_bytes(32),
            )
            try:
                simulation_result = rpc.call(signer.address, vault, calldata)
            except RpcRequestError as exc:
                reason = _decode_revert(exc.revert_data)
                output(f"{name}: reverted ({reason})")
                if reason != expected_reason:
                    output(f"  expected: {expected_reason}")
                    failures += 1
                    continue
                if not broadcast:
                    continue

                gas_price = rpc.gas_price()
                if gas_price > _MAX_GAS_PRICE_WEI:
                    raise BypassDemoError("RPC gas price exceeds the configured safety limit")
                nonce = rpc.transaction_count(signer.address)
                transaction = {
                    "chainId": BASE_SEPOLIA_CHAIN_ID,
                    "nonce": nonce,
                    "to": "0x" + vault[2:].lower(),
                    "value": 0,
                    "data": calldata,
                    "gas": _BYPASS_GAS_LIMIT,
                    "gasPrice": gas_price,
                }
                signed = Account.sign_transaction(transaction, private_key)
                tx_hash = rpc.send_raw_transaction(
                    "0x" + bytes(signed.raw_transaction).hex()
                )
                output(f"  broadcast transaction hash: {tx_hash}")
            else:
                output(f"{name}: simulation unexpectedly succeeded; no transaction sent")
                if simulation_result != "0x":
                    output("  simulation returned unexpected data")
                failures += 1

    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Demonstrate AegisVault's on-chain rejection of transfers that bypass "
            "Aegis's server-side checks."
        )
    )
    parser.add_argument("--vault", required=True, help="AegisVault address on Base Sepolia")
    parser.add_argument("--token", required=True, help="currently allowed token address")
    parser.add_argument(
        "--unlisted-recipient",
        required=True,
        help="valid recipient address that is not allowlisted in the vault",
    )
    parser.add_argument(
        "--broadcast",
        action="store_true",
        help="broadcast the calls after confirming each one reverts in simulation",
    )
    args = parser.parse_args(argv)

    try:
        private_key = _private_key_from_environment()
        rpc_url = _rpc_url_from_environment()
        return run_bypass_demo(
            vault_address=args.vault,
            token_address=args.token,
            unlisted_recipient=args.unlisted_recipient,
            private_key=private_key,
            broadcast=args.broadcast,
            rpc_factory=BaseSepoliaRpc,
            rpc_url=rpc_url,
        )
    except (BypassDemoError, RpcConfigurationError, RpcRequestError, ValueError) as exc:
        print(f"Bypass demonstration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
