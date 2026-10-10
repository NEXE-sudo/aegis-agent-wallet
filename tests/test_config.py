"""Configuration validation regression tests."""
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.auth import _require_token
from app.workflow.store import WorkflowStore


@pytest.mark.parametrize("db_path", ["", " ", "\t", "\n"])
def test_workflow_store_rejects_blank_database_path(db_path):
    with pytest.raises(ValueError, match="db_path cannot be empty"):
        WorkflowStore(db_path)


@pytest.mark.parametrize("env_name", ["AEGIS_AGENT_TOKEN", "AEGIS_APPROVAL_TOKEN"])
def test_auth_rejects_identical_agent_and_owner_tokens(monkeypatch, env_name):
    monkeypatch.setenv("AEGIS_AGENT_TOKEN", "shared-development-token")
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", "shared-development-token")
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials="shared-development-token",
    )

    with pytest.raises(HTTPException) as exc_info:
        _require_token(credentials, env_name)

    assert exc_info.value.status_code == 503
    assert "must be different" in exc_info.value.detail


@pytest.mark.parametrize("env_name", ["AEGIS_AGENT_TOKEN", "AEGIS_APPROVAL_TOKEN"])
@pytest.mark.parametrize("token_value", ["", " ", "\t"])
def test_auth_rejects_blank_configured_tokens(monkeypatch, env_name, token_value):
    monkeypatch.setenv(env_name, token_value)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="anything")

    with pytest.raises(HTTPException) as exc_info:
        _require_token(credentials, env_name)

    assert exc_info.value.status_code == 503
    assert f"{env_name} is not configured" in exc_info.value.detail


@pytest.mark.parametrize(
    ("env_name", "missing_name"),
    [
        ("AEGIS_AGENT_TOKEN", "AEGIS_APPROVAL_TOKEN"),
        ("AEGIS_APPROVAL_TOKEN", "AEGIS_AGENT_TOKEN"),
    ],
)
def test_auth_fails_closed_when_other_token_is_missing(monkeypatch, env_name, missing_name):
    monkeypatch.setenv(env_name, "configured-token")
    monkeypatch.delenv(missing_name, raising=False)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="configured-token")

    with pytest.raises(HTTPException) as exc_info:
        _require_token(credentials, env_name)

    assert exc_info.value.status_code == 503
    assert "Both AEGIS_AGENT_TOKEN and AEGIS_APPROVAL_TOKEN must be configured" in exc_info.value.detail


def test_auth_allows_distinct_configured_tokens(monkeypatch):
    monkeypatch.setenv("AEGIS_AGENT_TOKEN", "agent-token")
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", "owner-token")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="agent-token")

    _require_token(credentials, "AEGIS_AGENT_TOKEN")



def test_workflow_store_migration_skips_non_object_persisted_proposals(tmp_path):
    import json
    import sqlite3

    db_path = tmp_path / "legacy-corrupt-workflow.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """CREATE TABLE transactions (
                transaction_id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                proposal_json TEXT NOT NULL,
                policy_decision TEXT NOT NULL,
                policy_reasons_json TEXT NOT NULL,
                risk_score INTEGER NOT NULL,
                risk_level TEXT NOT NULL,
                risk_reasons_json TEXT NOT NULL,
                status TEXT NOT NULL,
                approval_fingerprint TEXT,
                approved_at TEXT,
                execution_reference TEXT,
                idempotency_key TEXT,
                chain_id INTEGER NOT NULL DEFAULT 0,
                token_address TEXT NOT NULL DEFAULT '',
                amount_base_units INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        db.execute(
            """INSERT INTO transactions (
                transaction_id, fingerprint, proposal_json, policy_decision,
                policy_reasons_json, risk_score, risk_level, risk_reasons_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("corrupt-legacy-row", "fingerprint", json.dumps(["not-a-proposal"]),
             "block", "[]", 0, "low", "[]", "blocked"),
        )

    store = WorkflowStore(db_path)

    assert store.get("corrupt-legacy-row")["transaction_id"] == "corrupt-legacy-row"
