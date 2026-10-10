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
