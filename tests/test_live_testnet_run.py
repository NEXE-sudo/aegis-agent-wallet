"""Mocked safety tests for the guarded live runner; no network or real keys are used."""
from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.live_testnet_run import (
    LiveRunError,
    _cast_env,
    _command_result,
    _run_step,
    redact,
    run_live,
    validate_configuration,
)

# Explicitly non-secret, throwaway test-only key; never use it on any network.
FIXTURE_PRIVATE_KEY = "0x" + "11" * 32
FIXTURE_OWNER = "0x1111111111111111111111111111111111111111"
FIXTURE_RECIPIENT = "0x4444444444444444444444444444444444444444"


def env_fixture() -> dict[str, str]:
    return {
        "BASE_SEPOLIA_RPC_URL": "https://rpc.fixture.invalid/v1/fixture-secret",
        "AEGIS_VAULT_OWNER_ADDRESS": FIXTURE_OWNER,
        "OWNER_KEYSTORE_ACCOUNT": "fixture-owner-keystore",
        "AEGIS_TESTNET_PRIVATE_KEY": FIXTURE_PRIVATE_KEY,
        "AEGIS_AGENT_TOKEN": "fixture-agent-token",
        "AEGIS_APPROVAL_TOKEN": "fixture-owner-token",
        "AEGIS_API_URL": "http://127.0.0.1:8123",
    }


def args_fixture(tmp_path: Path, *, broadcast: bool = False) -> Namespace:
    return Namespace(
        broadcast=broadcast,
        recipient=FIXTURE_RECIPIENT,
        amount_base_units=1_000_000,
        token_address="0x036cbd53842c5426634e7929541ec2318f3dcf7e",
        fund_amount_base_units=None,
        min_confirmations=12,
        output_dir=str(tmp_path / "reports" / "live"),
    )


def test_dry_run_makes_no_rpc_or_subprocess_calls(tmp_path: Path) -> None:
    calls: list[object] = []

    def rpc(*_args: object, **_kwargs: object) -> object:
        calls.append("rpc")
        raise AssertionError("dry run must not make RPC calls")

    result = run_live(args_fixture(tmp_path), root=tmp_path, env=env_fixture(), rpc_call=rpc)
    assert result["mode"] == "dry_run"
    assert result["broadcast"] is False
    assert calls == []
    assert (tmp_path / "reports/live/partial-report.json").is_file()


def test_configuration_validates_public_inputs_without_printing_secrets(tmp_path: Path) -> None:
    env = env_fixture()
    public = validate_configuration(args_fixture(tmp_path), env)
    assert public["owner"] == FIXTURE_OWNER.lower()
    assert "fixture-agent-token" not in json.dumps(public)
    assert "fixture-secret" not in json.dumps(public)


def test_chain_id_refusal_happens_before_any_command_or_broadcast(tmp_path: Path) -> None:
    calls: list[tuple[object, ...]] = []

    def rpc(method: str, params: list[object], *, env: dict[str, str]) -> str:
        calls.append((method, params))
        assert method == "eth_chainId"
        return "0x1"

    with pytest.raises(LiveRunError, match="expected 84532"):
        run_live(args_fixture(tmp_path, broadcast=True), root=tmp_path, env=env_fixture(), rpc_call=rpc)
    assert calls == [("eth_chainId", [])]
    assert not (tmp_path / "reports/live/run-state.json").exists()


def test_secret_and_rpc_url_redaction() -> None:
    env = env_fixture()
    text = "token=fixture-agent-token rpc=https://rpc.fixture.invalid/v1/fixture-secret"
    result = redact(text, env)
    assert "fixture-agent-token" not in result
    assert "fixture-secret" not in result
    assert "rpc.fixture.invalid" not in result or "[URL REDACTED]" in result
    assert "REDACTED" in result


def test_completed_step_is_resumed_without_repeating_action(tmp_path: Path) -> None:
    state = {"steps": {}, "values": {}}
    path = tmp_path / "state.json"
    calls: list[str] = []

    def action() -> dict[str, str]:
        calls.append("called")
        return {"transaction_hash": "0x" + "a" * 64}

    first = _run_step("deploy", state=state, state_path=path, report_dir=tmp_path,
                      action=action, broadcast=True)
    second = _run_step("deploy", state=state, state_path=path, report_dir=tmp_path,
                       action=action, broadcast=True)
    assert first == second
    assert calls == ["called"]


def test_uncertain_broadcast_is_never_retried_automatically(tmp_path: Path) -> None:
    state = {"steps": {"deploy": {"status": "broadcast_started"}}, "values": {}}
    with pytest.raises(LiveRunError, match="may already have broadcast"):
        _run_step("deploy", state=state, state_path=tmp_path / "state.json",
                  report_dir=tmp_path, action=dict, broadcast=True)


def test_failure_saves_partial_state_and_marks_broadcast_uncertain(tmp_path: Path) -> None:
    state = {"steps": {}, "values": {}}

    def fail() -> dict[str, str]:
        raise RuntimeError("fixture failure")

    with pytest.raises(RuntimeError, match="fixture failure"):
        _run_step("deploy", state=state, state_path=tmp_path / "state.json",
                  report_dir=tmp_path, action=fail, broadcast=True)
    assert state["steps"]["deploy"]["status"] == "broadcast_uncertain"
    assert json.loads((tmp_path / "state.json").read_text())["steps"]["deploy"]["status"] == "broadcast_uncertain"


def test_non_broadcast_failure_is_marked_retryable(tmp_path: Path) -> None:
    state = {"steps": {}, "values": {}}
    with pytest.raises(RuntimeError):
        _run_step("read-only", state=state, state_path=tmp_path / "state.json",
                  report_dir=tmp_path, action=lambda: (_ for _ in ()).throw(RuntimeError("fixture")),
                  broadcast=False)
    assert state["steps"]["read-only"]["status"] == "failed"


def test_subprocess_mock_receives_rpc_url_only_in_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env = env_fixture()
    seen: dict[str, object] = {}

    def fake_run(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        seen["env"] = env
        return subprocess.CompletedProcess(command, 0, stdout="transaction hash: 0x" + "b" * 64, stderr="")

    monkeypatch.setattr("scripts.live_testnet_run._run_command", fake_run)
    safe_env = _cast_env(env, "fixture-owner-keystore")
    output = _command_result(
        ["cast", "send", "0x" + "22" * 20],
        root=tmp_path,
        env=safe_env,
        report_dir=tmp_path,
        label="mock-cast",
    )
    assert "BASE_SEPOLIA_RPC_URL" not in seen["command"]
    assert env["BASE_SEPOLIA_RPC_URL"] not in seen["command"]
    assert seen["env"]["ETH_RPC_URL"] == env["BASE_SEPOLIA_RPC_URL"]
    assert "fixture-secret" not in output
    assert "0x" + "b" * 64 in output
