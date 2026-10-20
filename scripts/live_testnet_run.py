"""Guarded Base Sepolia-only live testnet run; never used for mainnet.

Dry-run is the default. Broadcasts require --broadcast and an RPC chain ID of 84532.
Secrets are read only from environment variables and are never written to reports.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from eth_account import Account

from app.chain.base_sepolia import (
    BASE_SEPOLIA_CHAIN_ID,
    BASE_SEPOLIA_USDC_ADDRESS,
    validate_address,
)

MINIMUM_WALLET_BALANCE_WEI = 10**16  # 0.01 test ETH, for deployment plus follow-up transactions
MIN_CONFIRMATIONS = 12
TOKEN_CAP = 50_000_000
TOKEN_DAILY_CAP = 150_000_000
APPROVAL_THRESHOLD = 50_000_000
DEMO_ALLOWLIST = (
    "0x2222222222222222222222222222222222222222",
    "0x3333333333333333333333333333333333333333",
)
HASH_RE = re.compile(r"0x[0-9a-fA-F]{64}")
ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
SECRET_NAMES = ("AEGIS_TESTNET_PRIVATE_KEY", "AEGIS_AGENT_TOKEN", "AEGIS_APPROVAL_TOKEN")


class LiveRunError(RuntimeError):
    """Actionable, fail-closed live-run error."""


def redact(value: str, env: dict[str, str] | os._Environ[str] | None = None) -> str:
    """Remove configured secrets and credential-bearing URLs before output is saved."""
    values = dict(os.environ if env is None else env)
    for name in SECRET_NAMES:
        secret = values.get(name, "")
        if secret:
            value = value.replace(secret, "[REDACTED]")
    rpc_url = values.get("BASE_SEPOLIA_RPC_URL", "")
    if rpc_url:
        value = value.replace(rpc_url, "[RPC URL REDACTED]")
    # Also redact arbitrary URLs that may carry userinfo or query-string API keys.
    def clean_url(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parsed = urlsplit(raw.rstrip(".,);"))
            if parsed.scheme not in {"http", "https"}:
                return raw
            host = parsed.hostname or ""
            if parsed.port:
                host += f":{parsed.port}"
            suffix = "/…" if parsed.path not in {"", "/"} else ""
            return f"{parsed.scheme}://{host}{suffix}[URL REDACTED]"
        except ValueError:
            return "[URL REDACTED]"
    return re.sub(r"https?://[^\s\"'<>]+", clean_url, value)


def _rpc_call(method: str, params: list[Any], *, env: dict[str, str] | os._Environ[str] | None = None) -> Any:
    values = os.environ if env is None else env
    endpoint = values.get("BASE_SEPOLIA_RPC_URL", "")
    try:
        parsed = urlsplit(endpoint)
    except ValueError as exc:
        raise LiveRunError("BASE_SEPOLIA_RPC_URL must be a valid HTTPS endpoint.") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise LiveRunError("BASE_SEPOLIA_RPC_URL must be an HTTPS endpoint.")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError) as exc:
        raise LiveRunError("Base Sepolia RPC request failed; check the configured endpoint and network.") from exc
    if not isinstance(payload, dict) or payload.get("error") or "result" not in payload:
        raise LiveRunError(f"Base Sepolia RPC returned an invalid {method} response.")
    return payload["result"]


def chain_id(*, env: dict[str, str] | os._Environ[str] | None = None,
             rpc_call: Callable[..., Any] = _rpc_call) -> int:
    result = rpc_call("eth_chainId", [], env=env)
    if not isinstance(result, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", result):
        raise LiveRunError("RPC returned an invalid chain ID.")
    return int(result, 16)


def validate_configuration(args: argparse.Namespace, env: dict[str, str] | os._Environ[str]) -> dict[str, str]:
    """Validate names and public inputs without printing or persisting secret values."""
    missing = [name for name in (
        "BASE_SEPOLIA_RPC_URL", "AEGIS_VAULT_OWNER_ADDRESS", "OWNER_KEYSTORE_ACCOUNT",
        "AEGIS_TESTNET_PRIVATE_KEY", "AEGIS_AGENT_TOKEN", "AEGIS_APPROVAL_TOKEN",
    ) if not str(env.get(name, "")).strip()]
    if missing:
        raise LiveRunError("Missing required environment variable(s): " + ", ".join(missing))
    rpc = str(env["BASE_SEPOLIA_RPC_URL"])
    try:
        parsed = urlsplit(rpc)
        port = parsed.port
    except ValueError as exc:
        raise LiveRunError("BASE_SEPOLIA_RPC_URL must be a valid HTTPS endpoint.") from exc
    if parsed.scheme != "https" or not parsed.hostname or (port is not None and not 1 <= port <= 65535):
        raise LiveRunError("BASE_SEPOLIA_RPC_URL must be an HTTPS endpoint.")
    try:
        owner = validate_address(str(env["AEGIS_VAULT_OWNER_ADDRESS"]))
        recipient = validate_address(args.recipient)
        token = validate_address(args.token_address)
        agent = Account.from_key(str(env["AEGIS_TESTNET_PRIVATE_KEY"])).address.lower()
    except (TypeError, ValueError) as exc:
        raise LiveRunError("A public address or the agent key in the environment is invalid.") from exc
    if env["AEGIS_AGENT_TOKEN"] == env["AEGIS_APPROVAL_TOKEN"]:
        raise LiveRunError("AEGIS_AGENT_TOKEN and AEGIS_APPROVAL_TOKEN must be distinct.")
    if owner == agent:
        raise LiveRunError("The owner keystore address and agent key must be different accounts.")
    if recipient == "0x" + "0" * 40 or recipient in {owner, agent}:
        raise LiveRunError("The transfer recipient must be a nonzero address distinct from owner and agent.")
    api_url = str(env.get("AEGIS_API_URL", "http://127.0.0.1:8000"))
    api = urlsplit(api_url)
    if api.scheme != "http" or api.hostname not in {"127.0.0.1", "localhost"} or not api.port:
        raise LiveRunError("AEGIS_API_URL must be a localhost HTTP URL with an explicit port.")
    if token != BASE_SEPOLIA_USDC_ADDRESS:
        raise LiveRunError("The current API supports the published Base Sepolia demo USDC token only.")
    if recipient in {x.lower() for x in DEMO_ALLOWLIST}:
        raise LiveRunError("The transfer recipient must be outside the demo allowlist so owner approval is required.")
    if args.amount_base_units < 1 or args.amount_base_units > TOKEN_CAP:
        raise LiveRunError("Transfer amount must be between 1 and 50000000 USDC base units (0.000001–50 USDC).")
    if args.fund_amount_base_units is not None and args.fund_amount_base_units < args.amount_base_units:
        raise LiveRunError("Funding amount must be at least the proposed transfer amount.")
    if args.min_confirmations < MIN_CONFIRMATIONS:
        raise LiveRunError("--min-confirmations must be at least 12 for this testnet evidence workflow.")
    return {"owner": owner, "recipient": recipient, "token": token}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_state(path: Path, input_fingerprint: str) -> dict[str, Any]:
    if not path.exists():
        return {"schema": "aegis-live-run-state/v1", "input_fingerprint": input_fingerprint,
                "created_at": datetime.now(UTC).isoformat(), "steps": {}, "values": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LiveRunError(f"Cannot read saved run state at {path}; preserve it and inspect manually.") from exc
    if not isinstance(state, dict) or state.get("schema") != "aegis-live-run-state/v1":
        raise LiveRunError("Saved run state has an unsupported schema; do not overwrite it.")
    if state.get("input_fingerprint") != input_fingerprint:
        raise LiveRunError("Saved run state belongs to different public inputs; use a new --output-dir.")
    if not isinstance(state.get("steps"), dict) or not isinstance(state.get("values"), dict):
        raise LiveRunError("Saved run state is malformed; preserve it and inspect manually.")
    return state


def _run_command(command: list[str], *, cwd: Path, env: dict[str, str],
                 timeout: int = 300) -> subprocess.CompletedProcess[str]:
    """Run a child process with RPC URL supplied through env, never argv."""
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True,
                          check=False, timeout=timeout)


def _run_step(
    name: str, *, state: dict[str, Any], state_path: Path, report_dir: Path,
    action: Callable[[], dict[str, Any]], broadcast: bool, command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, Any]:
    del command_runner  # reserved for compatibility with tests injecting subprocess behaviour
    record = state["steps"].get(name)
    if isinstance(record, dict) and record.get("status") == "complete":
        return record.get("result", {})
    if isinstance(record, dict) and record.get("status") in {"broadcast_started", "broadcast_uncertain"}:
        raise LiveRunError(
            f"Step {name!r} may already have broadcast. Do not retry it; inspect reports/live and the explorer, "
            "then reconcile the saved state manually."
        )
    state["steps"][name] = {"status": "broadcast_started" if broadcast else "running",
                            "updated_at": datetime.now(UTC).isoformat()}
    _write_json(state_path, state)
    try:
        result = action()
        state["steps"][name] = {"status": "complete", "result": result,
                                "updated_at": datetime.now(UTC).isoformat()}
        _write_json(state_path, state)
        return result
    except Exception:
        state["steps"][name] = {
            "status": "broadcast_uncertain" if broadcast else "failed",
            "updated_at": datetime.now(UTC).isoformat(),
            "message": "Inspect the saved partial report and reconcile before retrying.",
        }
        _write_json(state_path, state)
        raise


def _command_result(command: list[str], *, root: Path, env: dict[str, str],
                    report_dir: Path, label: str, timeout: int = 300,
                    cwd: Path | None = None, keep_agent_key: bool = False,
                    command_runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command) -> str:
    child_env = dict(env)
    if not keep_agent_key:
        child_env.pop("AEGIS_TESTNET_PRIVATE_KEY", None)
    child_env.pop("AEGIS_AGENT_TOKEN", None)
    child_env.pop("AEGIS_APPROVAL_TOKEN", None)
    try:
        result = command_runner(command, cwd=cwd or root, env=child_env, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LiveRunError(f"{label} could not be completed; inspect {report_dir / 'partial-report.json'}.") from exc
    output = redact((result.stdout or "") + (result.stderr or ""), env)
    (report_dir / f"{label}.log").write_text(output, encoding="utf-8")
    if result.returncode != 0:
        raise LiveRunError(f"{label} failed with exit code {result.returncode}; see reports/live/{label}.log.")
    return output


def _api_request(url: str, method: str, token: str, payload: dict[str, Any] | None = None,
                 extra_headers: dict[str, str] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError) as exc:
        raise LiveRunError(f"Local Aegis API {method} request failed at a redacted endpoint.") from exc
    if not isinstance(result, dict):
        raise LiveRunError("Local Aegis API returned an unexpected response.")
    return result


def _cast_env(env: dict[str, str], account: str) -> dict[str, str]:
    result = dict(env)
    # Foundry/cast read ETH_RPC_URL from the environment; do not put credential-bearing URLs in argv.
    result["ETH_RPC_URL"] = env["BASE_SEPOLIA_RPC_URL"]
    result["FOUNDRY_DISABLE_NIGHTLY_WARNING"] = "true"
    result["AEGIS_VAULT_OWNER_ADDRESS"] = env["AEGIS_VAULT_OWNER_ADDRESS"]
    result["OWNER_KEYSTORE_ACCOUNT"] = account
    return result


def _extract_hash(output: str) -> str:
    matches = HASH_RE.findall(output)
    # Prefer an explicitly labelled transaction hash, otherwise use the last hash in Forge/cast output.
    labelled = re.search(r"(?:transaction hash|transactionHash|tx hash)\s*[:=]\s*(0x[0-9a-fA-F]{64})", output, re.IGNORECASE)
    if labelled:
        return labelled.group(1).lower()
    if matches:
        return matches[-1].lower()
    raise LiveRunError("Broadcast output did not contain a transaction hash; inspect the saved log and do not retry.")


def _cast_send(root: Path, env: dict[str, str], report_dir: Path, label: str,
               target: str, signature: str, arguments: list[str], account: str) -> str:
    output = _command_result(["cast", "send", target, signature, *arguments,
                              "--account", account, "--chain", "84532"],
                             root=root, env=_cast_env(env, account), report_dir=report_dir, label=label)
    return _extract_hash(output)


def _wait_for_api(base_url: str, env: dict[str, str], root: Path, report_dir: Path,
                  vault: str, agent_key: str) -> tuple[subprocess.Popen[str] | None, dict[str, str]]:
    health_url = base_url.rstrip("/") + "/health"
    try:
        health = _api_request(health_url, "GET", env["AEGIS_APPROVAL_TOKEN"])
        if health.get("mode") != "base_sepolia_vault":
            raise LiveRunError("AEGIS_API_URL is already serving a non-vault mode; stop it before running this workflow.")
        return None, dict(env)
    except LiveRunError as exc:
        if "non-vault mode" in str(exc):
            raise
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise LiveRunError("The runner may start the API only on localhost; set AEGIS_API_URL to http://127.0.0.1:PORT.")
    if not parsed.port:
        raise LiveRunError("AEGIS_API_URL must include an explicit localhost port.")
    server_env = dict(env)
    server_env.update({
        "AEGIS_VAULT_ADDRESS": vault,
        "AEGIS_EXECUTION_MODE": "base_sepolia_vault",
        "AEGIS_TESTNET_PRIVATE_KEY": agent_key,
        "BASE_SEPOLIA_RPC_URL": env["BASE_SEPOLIA_RPC_URL"],
        "AEGIS_API_URL": base_url,
    })
    command = ["uv", "run", "--locked", "uvicorn", "app.main:app",
               "--host", parsed.hostname, "--port", str(parsed.port)]
    try:
        process = subprocess.Popen(command, cwd=root, env=server_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    except OSError as exc:
        raise LiveRunError("Could not start the local Aegis API; check uv and project dependencies.") from exc
    for _ in range(40):
        if process.poll() is not None:
            raise LiveRunError("The local Aegis API exited during startup; check the environment and port.")
        try:
            health = _api_request(health_url, "GET", env["AEGIS_APPROVAL_TOKEN"])
            if health.get("mode") == "base_sepolia_vault":
                return process, server_env
        except LiveRunError:
            pass
        time.sleep(0.5)
    process.terminate()
    raise LiveRunError("The local Aegis API did not become ready; inspect configuration and port availability.")


def _balance_wei(address: str, *, env: dict[str, str]) -> int:
    value = _rpc_call("eth_getBalance", [address, "latest"], env=env)
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", value):
        raise LiveRunError("RPC returned an invalid wallet balance.")
    return int(value, 16)


def run_live(args: argparse.Namespace, *, root: Path | None = None,
             env: dict[str, str] | None = None,
             rpc_call: Callable[..., Any] = _rpc_call,
             command_runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command,
             api_request: Callable[..., dict[str, Any]] = _api_request) -> dict[str, Any]:
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    values = dict(os.environ if env is None else env)
    public = validate_configuration(args, values)
    report_dir = (root / args.output_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    state_path = report_dir / "run-state.json"
    public_inputs = {
        "owner": public["owner"], "agent": Account.from_key(values["AEGIS_TESTNET_PRIVATE_KEY"]).address.lower(),
        "recipient": public["recipient"], "token": public["token"],
        "amount_base_units": args.amount_base_units,
        "fund_amount_base_units": args.fund_amount_base_units,
        "min_confirmations": args.min_confirmations,
    }
    fingerprint = hashlib.sha256(json.dumps(public_inputs, sort_keys=True).encode()).hexdigest()
    state = _read_state(state_path, fingerprint)
    plan = [
        "Check Base Sepolia chain ID and owner/agent ETH balances",
        "Deploy AegisVault v2 (owner keystore)",
        "Configure bounded agent session, token policy, demo allowlist and recipient cap",
        "Optionally fund the vault with demo USDC",
        "Create a real API proposal and record owner approval",
        "Submit the exact on-chain approval and execute one agent transfer",
        "Broadcast the existing expected-revert bypass demonstration",
        "Verify deployment, success/revert bundles, confirmation depth and cross-artifact consistency",
        "Regenerate offline evaluation, evidence bundle, manifest and submission preflight",
    ]
    if not args.broadcast:
        report = {"mode": "dry_run", "broadcast": False, "chain_id_check": "not queried in dry run",
                  "public_inputs": public_inputs, "planned_steps": plan,
                  "note": "No RPC request, process launch, signing, or broadcast occurs in dry-run mode."}
        _write_json(report_dir / "partial-report.json", report)
        print("Dry run: PASS (no RPC calls or broadcasts)")
        print("\n".join(f"{i + 1}. {step}" for i, step in enumerate(plan)))
        print(f"Plan saved: {report_dir / 'partial-report.json'}")
        return report

    try:
        actual_chain = rpc_call("eth_chainId", [], env=values)
        if not isinstance(actual_chain, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", actual_chain):
            raise LiveRunError("RPC returned an invalid chain ID; refusing to broadcast.")
        actual_chain_id = int(actual_chain, 16)
        if actual_chain_id != BASE_SEPOLIA_CHAIN_ID:
            raise LiveRunError(f"Refusing to broadcast: RPC chain ID is {actual_chain_id}, expected 84532.")
        agent_address = Account.from_key(values["AEGIS_TESTNET_PRIVATE_KEY"]).address.lower()
        if agent_address == public["owner"]:
            raise LiveRunError("Owner and agent must be separate throwaway accounts; refusing to broadcast.")
        for label, address in (("owner", public["owner"]), ("agent", agent_address)):
            raw_balance = rpc_call("eth_getBalance", [address, "latest"], env=values)
            if not isinstance(raw_balance, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", raw_balance):
                raise LiveRunError(f"RPC returned an invalid {label} wallet balance.")
            balance = int(raw_balance, 16)
            if balance < MINIMUM_WALLET_BALANCE_WEI:
                raise LiveRunError(
                    f"The {label} wallet has less than 0.01 test ETH. Fund it from a public Base Sepolia faucet, "
                    "then rerun; no transaction has been broadcast yet."
                )
    except Exception as exc:
        _write_json(report_dir / "partial-report.json", {
            "mode": "broadcast", "broadcast": False, "result": "INCOMPLETE",
            "error": redact(str(exc), values),
            "note": "Chain and balance preflight failed before any broadcast step was started.",
        })
        raise
    state["chain_id"] = actual_chain_id
    state["agent_address"] = agent_address
    state["owner_address"] = public["owner"]
    _write_json(state_path, state)
    process: subprocess.Popen[str] | None = None
    try:
        # The state machine marks every broadcast step before invoking the command. An uncertain step is never retried.
        if not state["values"].get("vault_address"):
            def deploy() -> dict[str, Any]:
                output = _command_result(
                    ["forge", "script", "script/Deploy.s.sol:Deploy",
                     "--account", values["OWNER_KEYSTORE_ACCOUNT"], "--sender", public["owner"], "--broadcast"],
                    root=root, env=_cast_env(values, values["OWNER_KEYSTORE_ACCOUNT"]),
                    report_dir=report_dir, label="deploy-vault", timeout=300, cwd=root / "contracts")
                address_match = re.search(r"AegisVault deployed at\s+(0x[0-9a-fA-F]{40})", output, re.IGNORECASE)
                if not address_match:
                    raise LiveRunError("Forge output did not contain the deployed vault address; inspect deploy-vault.log.")
                return {"vault_address": address_match.group(1).lower(), "transaction_hash": _extract_hash(output)}
            deployed = _run_step("deploy-vault", state=state, state_path=state_path, report_dir=report_dir,
                                 action=deploy, broadcast=True)
            state["values"]["vault_address"] = deployed["vault_address"]
            state["values"]["deployment_transaction_hash"] = deployed["transaction_hash"]
            _write_json(state_path, state)
        vault = str(state["values"]["vault_address"])
        if not ADDRESS_RE.fullmatch(vault):
            raise LiveRunError("Saved vault address is invalid; inspect the run state and deployment output.")
        values["AEGIS_VAULT_ADDRESS"] = vault
        values["AEGIS_VAULT_OWNER_ADDRESS"] = public["owner"]
        values["AEGIS_DB_PATH"] = str((report_dir / "workflow.sqlite3").resolve())
        values["AEGIS_EXECUTION_MODE"] = "base_sepolia_vault"
        values["ETH_RPC_URL"] = values["BASE_SEPOLIA_RPC_URL"]

        now = int(time())
        account = values["OWNER_KEYSTORE_ACCOUNT"]
        config_steps = [
            ("set-agent", vault, "setAgent(address,uint256)", [agent_address, str(now + 29 * 24 * 3600)]),
            ("set-token-policy", vault, "setTokenPolicy(address,bool,uint256,uint256,uint256)",
             [public["token"], "true", str(TOKEN_CAP), str(TOKEN_DAILY_CAP), str(APPROVAL_THRESHOLD)]),
            ("allow-demo-recipient-1", vault, "setRecipient(address,bool)", [DEMO_ALLOWLIST[0], "true"]),
            ("allow-demo-recipient-2", vault, "setRecipient(address,bool)", [DEMO_ALLOWLIST[1], "true"]),
            ("set-transfer-recipient-cap", vault, "setRecipientCap(address,address,bool,uint256,uint256)",
             [public["token"], public["recipient"], "true", str(TOKEN_CAP), str(TOKEN_DAILY_CAP)]),
        ]
        for name, target, signature, arguments in config_steps:
            _run_step(name, state=state, state_path=state_path, report_dir=report_dir,
                action=lambda n=name, t=target, s=signature, a=arguments: {
                    "transaction_hash": _cast_send(root, values, report_dir, n, t, s, a, account)
                }, broadcast=True)
        if args.fund_amount_base_units is not None:
            _run_step("fund-vault", state=state, state_path=state_path, report_dir=report_dir,
                action=lambda: {"transaction_hash": _cast_send(root, values, report_dir, "fund-vault",
                    public["token"], "transfer(address,uint256)", [vault, str(args.fund_amount_base_units)], account)},
                broadcast=True)

        def check_vault_balance() -> dict[str, Any]:
            output = _command_result(["cast", "call", public["token"], "balanceOf(address)(uint256)", vault,
                "--chain", "84532"], root=root, env=_cast_env(values, account),
                report_dir=report_dir, label="check-vault-usdc-balance", timeout=60)
            raw = output.strip().splitlines()[-1].strip()
            try:
                balance = int(raw, 0)
            except ValueError as exc:
                raise LiveRunError("Could not parse the vault USDC balance returned by cast.") from exc
            if balance < args.amount_base_units:
                raise LiveRunError(
                    f"Vault USDC balance is {balance} base units, below the proposed amount "
                    f"{args.amount_base_units}. Fund the vault with demo USDC and rerun before creating a proposal."
                )
            return {"balance_base_units": balance, "required_base_units": args.amount_base_units}
        _run_step("check-vault-usdc-balance", state=state, state_path=state_path, report_dir=report_dir,
                  action=check_vault_balance, broadcast=False)

        # Start an isolated local API with the just-deployed vault address and a dedicated SQLite database.
        base_url = values.get("AEGIS_API_URL", "http://127.0.0.1:8000")
        try:
            existing_health = api_request(base_url.rstrip("/") + "/health", "GET", values["AEGIS_APPROVAL_TOKEN"])
            if existing_health.get("mode") != "base_sepolia_vault":
                raise LiveRunError("AEGIS_API_URL already serves a non-vault mode; stop that server and rerun.")
            if getattr(args, "force_new_api", False):
                raise LiveRunError("--force-new-api is not supported; stop the existing server first.")
        except LiveRunError as exc:
            if "already serves" in str(exc) or "force-new-api" in str(exc):
                raise
            parsed = urlsplit(base_url)
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.port:
                raise LiveRunError("AEGIS_API_URL must be a localhost URL with an explicit port.") from exc
            server_env = dict(values)
            server_env.update({"AEGIS_VAULT_ADDRESS": vault, "AEGIS_EXECUTION_MODE": "base_sepolia_vault",
                               "AEGIS_DB_PATH": values["AEGIS_DB_PATH"]})
            command = ["uv", "run", "--locked", "uvicorn", "app.main:app", "--host", parsed.hostname,
                       "--port", str(parsed.port)]
            process = subprocess.Popen(command, cwd=root, env=server_env,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
            for _ in range(40):
                if process.poll() is not None:
                    raise LiveRunError("Local API exited during startup; check the port and configuration.")
                try:
                    health = api_request(base_url.rstrip("/") + "/health", "GET", values["AEGIS_APPROVAL_TOKEN"])
                    if health.get("mode") == "base_sepolia_vault":
                        break
                except LiveRunError:
                    pass
                time.sleep(0.5)
            else:
                process.terminate()
                raise LiveRunError("Local API did not become ready; no transfer was attempted.")

        # Ensure the selected recipient remains unlisted in the vault before the exact owner approval flow.
        def propose() -> dict[str, Any]:
            response = api_request(base_url.rstrip("/") + "/transactions/propose", "POST",
                values["AEGIS_AGENT_TOKEN"], {
                    "agent_id": "devops-01", "chain_id": 84532, "token_symbol": "USDC",
                    "token_address": public["token"], "recipient": public["recipient"],
                    "amount_base_units": args.amount_base_units, "token_decimals": 6,
                }, extra_headers={"Idempotency-Key": "aegis-live-" + fingerprint[:40]})
            if not isinstance(response.get("transaction_id"), str) or not isinstance(response.get("fingerprint"), str):
                raise LiveRunError("Proposal response lacks a transaction ID or fingerprint.")
            return {"transaction_id": response["transaction_id"], "fingerprint": response["fingerprint"],
                    "initial_status": response.get("status")}
        proposal = _run_step("create-proposal", state=state, state_path=state_path, report_dir=report_dir,
                             action=propose, broadcast=False)
        tx_id, fingerprint_value = proposal["transaction_id"], proposal["fingerprint"]
        def owner_approve_workflow() -> dict[str, Any]:
            transaction = api_request(base_url.rstrip("/") + f"/transactions/{tx_id}", "GET",
                                      values["AEGIS_APPROVAL_TOKEN"])
            if transaction.get("status") == "approved":
                return {"status": "approved", "already_approved": True}
            if transaction.get("status") != "awaiting_approval":
                raise LiveRunError("The proposal is not awaiting owner approval; inspect the saved workflow record.")
            return api_request(base_url.rstrip("/") + f"/transactions/{tx_id}/approve", "POST",
                values["AEGIS_APPROVAL_TOKEN"], {"transaction_fingerprint": fingerprint_value, "confirmation": "APPROVE"})
        _run_step("owner-approve-workflow", state=state, state_path=state_path, report_dir=report_dir,
            action=owner_approve_workflow, broadcast=False)
        approval = api_request(base_url.rstrip("/") + f"/transactions/{tx_id}/vault-approval", "GET",
                               values["AEGIS_APPROVAL_TOKEN"])
        a = approval.get("arguments")
        if not isinstance(a, dict):
            raise LiveRunError("API did not provide the exact on-chain approval arguments.")
        _run_step("owner-approve-vault-transfer", state=state, state_path=state_path, report_dir=report_dir,
            action=lambda: {"transaction_hash": _cast_send(root, values, report_dir, "owner-approve-vault-transfer",
                vault, "approveTransfer(address,address,uint256,bytes32,uint256)",
                [str(a["token"]), str(a["to"]), str(a["amount"]), str(a["ref"]), str(a["expiry"])], account)},
            broadcast=True)
        def execute_transfer() -> dict[str, Any]:
            result = api_request(base_url.rstrip("/") + f"/agent/transactions/{tx_id}/execute", "POST",
                                 values["AEGIS_AGENT_TOKEN"])
            tx_hash = result.get("execution_reference")
            if not isinstance(tx_hash, str) or not HASH_RE.fullmatch(tx_hash):
                raise LiveRunError("Agent execution response did not contain a real transaction hash.")
            return {"transaction_hash": tx_hash.lower(), "transaction_id": tx_id, "status": result.get("status")}
        transfer = _run_step("agent-transfer", state=state, state_path=state_path, report_dir=report_dir,
                             action=execute_transfer, broadcast=True)
        state["values"]["transfer_transaction_hash"] = transfer["transaction_hash"]
        state["values"]["transaction_id"] = tx_id
        _write_json(state_path, state)

        def bypass() -> dict[str, Any]:
            output = _command_result(["uv", "run", "--locked", "python", "-m", "scripts.vault_bypass_demo",
                "--vault", vault, "--token", public["token"], "--unlisted-recipient", public["recipient"],
                "--broadcast"], root=root, env=values, report_dir=report_dir, label="bypass-demo", timeout=300,
                keep_agent_key=True)
            hashes = HASH_RE.findall(output)
            if not hashes:
                raise LiveRunError("Bypass demo did not report a reverted transaction hash; inspect bypass-demo.log.")
            return {"transaction_hashes": [item.lower() for item in hashes],
                    "selected_reverted_transaction_hash": hashes[0].lower()}
        bypass_result = _run_step("bypass-demo", state=state, state_path=state_path, report_dir=report_dir,
                                  action=bypass, broadcast=True)
        state["values"]["bypass_transaction_hash"] = bypass_result["selected_reverted_transaction_hash"]
        _write_json(state_path, state)

        # All evidence commands use only saved real hashes and the local audit database.
        deploy_tx = state["values"]["deployment_transaction_hash"]
        _run_step("verify-deployment", state=state, state_path=state_path, report_dir=report_dir,
            action=lambda: ( _command_result(["uv", "run", "--locked", "python", "-m",
                "scripts.verify_vault_deployment", deploy_tx, "--expected-vault", vault,
                "--expected-owner", public["owner"], "--min-confirmations", str(args.min_confirmations),
                "--output", str(report_dir / "deployment-verification.json")],
                root=root, env=values, report_dir=report_dir, label="verify-deployment", timeout=180)
                and {"path": str(report_dir / "deployment-verification.json")}), broadcast=False)
        success_dir = report_dir / "success"
        revert_dir = report_dir / "revert"
        _run_step("build-success-evidence", state=state, state_path=state_path, report_dir=report_dir,
            action=lambda: (_command_result(["uv", "run", "--locked", "python", "-m", "scripts.build_evidence_bundle",
                "--transaction-hash", state["values"]["transfer_transaction_hash"], "--expected-sender", agent_address,
                "--vault", vault, "--audit-db", values["AEGIS_DB_PATH"], "--min-confirmations",
                str(args.min_confirmations), "--output-dir", str(success_dir)],
                root=root, env=values, report_dir=report_dir, label="build-success-evidence", timeout=300)
                and {"path": str(success_dir / "aegis-evidence-bundle.json")}), broadcast=False)
        _run_step("build-revert-evidence", state=state, state_path=state_path, report_dir=report_dir,
            action=lambda: (_command_result(["uv", "run", "--locked", "python", "-m", "scripts.build_evidence_bundle",
                "--transaction-hash", state["values"]["bypass_transaction_hash"], "--expected-sender", agent_address,
                "--vault", vault, "--expect-revert", "--min-confirmations", str(args.min_confirmations),
                "--output-dir", str(revert_dir)],
                root=root, env=values, report_dir=report_dir, label="build-revert-evidence", timeout=300)
                and {"path": str(revert_dir / "aegis-evidence-bundle.json")}), broadcast=False)
        for name, bundle_dir in (("verify-success-bundle", success_dir), ("verify-revert-bundle", revert_dir)):
            _run_step(name, state=state, state_path=state_path, report_dir=report_dir,
                action=lambda n=name, d=bundle_dir: (_command_result(["uv", "run", "--locked", "python", "-m",
                    "scripts.verify_evidence_bundle", str(d / "aegis-evidence-bundle.json")],
                    root=root, env=values, report_dir=report_dir, label=n, timeout=180)
                    and {"path": str(d / "aegis-evidence-bundle.json")}), broadcast=False)
        for name, bundle_dir, audit_args in (
            ("reverify-success", success_dir, ["--audit-db", values["AEGIS_DB_PATH"]]),
            ("reverify-revert", revert_dir, []),
        ):
            _run_step(name, state=state, state_path=state_path, report_dir=report_dir,
                action=lambda n=name, d=bundle_dir, aa=audit_args: (_command_result(["uv", "run", "--locked", "python", "-m",
                    "scripts.reverify_testnet_bundle", str(d / "aegis-evidence-bundle.json"),
                    "--expected-sender", agent_address, "--min-confirmations", str(args.min_confirmations), *aa],
                    root=root, env=values, report_dir=report_dir, label=n, timeout=180)
                    and {"path": str(d / "aegis-evidence-bundle.json")}), broadcast=False)
        for name, bundle_dir in (("phase5-success", success_dir), ("phase5-revert", revert_dir)):
            _run_step(name, state=state, state_path=state_path, report_dir=report_dir,
                action=lambda n=name, d=bundle_dir: (_command_result(["uv", "run", "--locked", "python", "-m",
                    "scripts.verify_phase5_evidence", str(report_dir / "deployment-verification.json"),
                    str(d / "aegis-evidence-bundle.json"), "--output", str(report_dir / f"{n}.json")],
                    root=root, env=values, report_dir=report_dir, label=n, timeout=180)
                    and {"path": str(report_dir / f"{n}.json")}), broadcast=False)

        # Regenerate the offline artifacts from the documented submission-evidence workflow.
        _command_result(["uv", "run", "--locked", "python", "-m", "agent.eval",
                         "--output-dir", "reports/submission/agent-eval"],
                        root=root, env=values, report_dir=report_dir, label="submission-agent-eval", timeout=300)
        _command_result(["uv", "run", "--locked", "python", "-m", "agent.repeated_eval", "--runs", "5",
                         "--output-dir", "reports/submission/repeated-eval"],
                        root=root, env=values, report_dir=report_dir, label="submission-repeated-eval", timeout=300)
        _command_result(["uv", "run", "--locked", "python", "-m", "scripts.build_evidence_bundle",
                         "--output-dir", "reports/submission/evidence"],
                        root=root, env=values, report_dir=report_dir, label="submission-offline-evidence", timeout=300)
        artifact_paths = [
            "reports/submission/agent-eval/agent-eval.json", "reports/submission/agent-eval/agent-eval.md",
            "reports/submission/repeated-eval/agent-repeat-eval.json", "reports/submission/repeated-eval/agent-repeat-eval.md",
            "reports/submission/evidence/aegis-evidence-bundle.json", "reports/submission/evidence/aegis-evidence-bundle.md",
            str(report_dir / "deployment-verification.json"), str(success_dir / "aegis-evidence-bundle.json"),
            str(revert_dir / "aegis-evidence-bundle.json"), str(report_dir / "phase5-success.json"),
            str(report_dir / "phase5-revert.json"),
        ]
        manifest_cmd = ["uv", "run", "--locked", "python", "-m", "scripts.submission_manifest", "build",
            "--root", ".", "--output", "reports/submission/submission-manifest.json"]
        for item in artifact_paths:
            manifest_cmd.extend(["--artifact", item])
        _command_result(manifest_cmd, root=root, env=values, report_dir=report_dir,
                        label="submission-manifest-build", timeout=180)
        _command_result(["uv", "run", "--locked", "python", "-m", "scripts.submission_manifest", "verify",
                         "reports/submission/submission-manifest.json", "--root", "."],
                        root=root, env=values, report_dir=report_dir, label="submission-manifest-verify", timeout=180)
        _command_result(["uv", "run", "--locked", "python", "-m", "scripts.submission_preflight"],
                        root=root, env=values, report_dir=report_dir, label="submission-preflight", timeout=180)
        final = {"mode": "broadcast", "chain_id": actual_chain_id, "vault_address": vault,
            "deployment_transaction_hash": deploy_tx, "transfer_transaction_hash": state["values"]["transfer_transaction_hash"],
            "reverted_bypass_transaction_hash": state["values"]["bypass_transaction_hash"],
            "confirmation_depth_required": args.min_confirmations, "result": "PASS",
            "note": "RPC observations are provider-specific; confirmations do not guarantee finality. Vault is unaudited and testnet-only."}
        _write_json(report_dir / "final-report.json", final)
        print("Live Base Sepolia run: PASS")
        print(f"Vault address: {vault}")
        print(f"Deployment transaction: {deploy_tx}")
        print(f"Successful transfer transaction: {final['transfer_transaction_hash']}")
        print(f"Reverted bypass transaction: {final['reverted_bypass_transaction_hash']}")
        print(f"Evidence saved under: {report_dir}")
        return final
    except Exception as exc:
        partial = {"mode": "broadcast", "chain_id": actual_chain_id,
                   "result": "INCOMPLETE", "error": redact(str(exc), values),
                   "state_path": str(state_path), "state": state}
        _write_json(report_dir / "partial-report.json", partial)
        print(f"Live run stopped: {redact(str(exc), values)}", file=sys.stderr)
        print(f"Partial report: {report_dir / 'partial-report.json'}", file=sys.stderr)
        raise
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guarded Base Sepolia live run; dry-run unless --broadcast is supplied.")
    parser.add_argument("--broadcast", action="store_true", help="explicitly permit Base Sepolia broadcasts after RPC chain-ID check")
    parser.add_argument("--recipient", required=True, help="public recipient address; must be outside the demo allowlist")
    parser.add_argument("--amount-base-units", required=True, type=int, help="USDC amount in six-decimal base units (maximum 50000000)")
    parser.add_argument("--token-address", default=BASE_SEPOLIA_USDC_ADDRESS, help="Base Sepolia demo USDC contract address")
    parser.add_argument("--fund-amount-base-units", type=int, help="optional amount of demo USDC to fund the vault with from the owner account")
    parser.add_argument("--min-confirmations", type=int, default=MIN_CONFIRMATIONS, help="minimum observed confirmations (default: 12)")
    parser.add_argument("--output-dir", default="reports/live", help="local report/state directory")
    args = parser.parse_args(argv)
    try:
        result = run_live(args)
        return 0 if result.get("result", "PASS") == "PASS" or result.get("mode") == "dry_run" else 1
    except (LiveRunError, OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        print(f"Live testnet runner stopped: {redact(str(exc))}", file=sys.stderr)
        report_dir = Path(args.output_dir)
        if not report_dir.is_absolute():
            report_dir = Path(__file__).resolve().parents[1] / report_dir
        report_dir.mkdir(parents=True, exist_ok=True)
        partial_path = report_dir / "partial-report.json"
        previous: dict[str, Any] = {}
        if partial_path.is_file():
            try:
                loaded = json.loads(partial_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    previous = loaded
            except (OSError, UnicodeError, json.JSONDecodeError):
                previous = {}
        previous.update({
            "mode": "broadcast" if args.broadcast else "dry_run",
            "result": "INCOMPLETE",
            "error": redact(str(exc)),
            "note": "No claim of live execution is made by this partial report.",
        })
        _write_json(partial_path, previous)
        print(f"Partial report: {partial_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
