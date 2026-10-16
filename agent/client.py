"""Agent-token-only asynchronous client for the Aegis API."""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote as url_quote
from urllib.parse import urlsplit

import httpx


class AgentApiError(RuntimeError):
    """The Aegis API rejected or failed an agent request."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"Aegis API request failed with HTTP {status_code}")
        self.status_code = status_code


class AegisApiClient:
    """Expose only agent-authorized API operations; owner credentials are not accepted."""

    def __init__(
        self,
        base_url: str,
        agent_token: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("Aegis API base URL must not be empty")
        if not agent_token.strip():
            raise ValueError("AEGIS_AGENT_TOKEN must not be empty")
        try:
            parsed = urlsplit(base_url)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Aegis API base URL is invalid") from exc
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (parsed.scheme == "http" and parsed.hostname not in local_hosts)
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError(
                "Aegis API base URL must be HTTPS, or local HTTP without embedded credentials"
            )
        self._base_url = base_url.rstrip("/")
        self._agent_token = agent_token
        self._http_client = http_client

    @classmethod
    def from_environment(
        cls, http_client: httpx.AsyncClient | None = None
    ) -> AegisApiClient:
        return cls(
            os.environ.get("AEGIS_API_URL", "http://127.0.0.1:8000"),
            os.environ.get("AEGIS_AGENT_TOKEN", ""),
            http_client,
        )

    async def propose_transaction(self, proposal: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/transactions/propose", json=proposal)

    async def get_market_quote(self, asset_class: str, symbol: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/agent/market-data/{url_quote(asset_class, safe='')}/{url_quote(symbol, safe='')}",
        )

    async def get_transaction_status(self, transaction_id: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"/agent/transactions/{url_quote(transaction_id, safe='')}"
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self._agent_token}"
        if self._http_client is not None:
            response = await self._http_client.request(
                method, f"{self._base_url}{path}", headers=headers, **kwargs
            )
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.request(
                    method, f"{self._base_url}{path}", headers=headers, **kwargs
                )
        if response.is_error:
            raise AgentApiError(response.status_code)
        result = response.json()
        if not isinstance(result, dict):
            raise TypeError("Aegis API returned an unexpected response shape")
        return result
