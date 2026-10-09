"""Shared bearer-token authentication dependencies."""
import hmac
import os
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


def _require_token(credentials: HTTPAuthorizationCredentials | None, env_name: str) -> None:
    expected = os.environ.get(env_name, "")
    if not expected:
        raise HTTPException(status_code=503, detail=f"{env_name} is not configured; endpoint fails closed")
    supplied = credentials.credentials if credentials else ""
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid bearer token")


def require_agent(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> None:
    _require_token(credentials, "AEGIS_AGENT_TOKEN")


def require_owner(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> None:
    _require_token(credentials, "AEGIS_APPROVAL_TOKEN")
