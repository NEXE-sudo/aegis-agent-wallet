"""Shared bearer-token authentication dependencies."""
import hmac
import os
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


def _require_token(
    credentials: HTTPAuthorizationCredentials | None,
    env_name: str,
    *,
    require_other_configured: bool = True,
) -> None:
    expected = os.environ.get(env_name, "")
    if not expected.strip():
        raise HTTPException(status_code=503, detail=f"{env_name} is not configured; endpoint fails closed")
    other_env_name = (
        "AEGIS_APPROVAL_TOKEN" if env_name == "AEGIS_AGENT_TOKEN" else "AEGIS_AGENT_TOKEN"
    )
    other = os.environ.get(other_env_name, "")
    if require_other_configured and not other.strip():
        raise HTTPException(
            status_code=503,
            detail="Both AEGIS_AGENT_TOKEN and AEGIS_APPROVAL_TOKEN must be configured; endpoint fails closed",
        )
    if other.strip() and hmac.compare_digest(expected, other):
        raise HTTPException(
            status_code=503,
            detail="AEGIS_AGENT_TOKEN and AEGIS_APPROVAL_TOKEN must be different; endpoint fails closed",
        )
    supplied = credentials.credentials if credentials else ""
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid bearer token")


def require_agent(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> None:
    _require_token(credentials, "AEGIS_AGENT_TOKEN")


def require_owner(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> None:
    _require_token(credentials, "AEGIS_APPROVAL_TOKEN")


def require_chain_owner(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    """Authenticate read-only chain inspection with the owner token alone."""
    _require_token(credentials, "AEGIS_APPROVAL_TOKEN", require_other_configured=False)
