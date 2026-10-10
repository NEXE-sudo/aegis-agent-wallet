"""Submit a proposal to a local Aegis API; execution remains simulation-only."""

import json
import os
import sys

import httpx

API_URL = os.environ.get("AEGIS_API_URL", "http://127.0.0.1:8000").rstrip("/")
AGENT_TOKEN = os.environ.get("AEGIS_AGENT_TOKEN", "")
IDEMPOTENCY_KEY = os.environ.get("AEGIS_IDEMPOTENCY_KEY")

PAYLOAD = {
    "agent_id": os.environ.get("AEGIS_AGENT_ID", "devops-01"),
    "chain_id": int(os.environ.get("AEGIS_CHAIN_ID", "84532")),
    "token_symbol": os.environ.get("AEGIS_TOKEN_SYMBOL", "USDC"),
    "token_address": os.environ.get(
        "AEGIS_TOKEN_ADDRESS",
        "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    ),
    "recipient": os.environ.get(
        "AEGIS_RECIPIENT",
        "0x2222222222222222222222222222222222222222",
    ),
    "amount_base_units": int(os.environ.get("AEGIS_AMOUNT_BASE_UNITS", "1000000")),
    "token_decimals": int(os.environ.get("AEGIS_TOKEN_DECIMALS", "6")),
}


def main() -> int:
    """Propose one transaction and print the API response."""
    if not AGENT_TOKEN.strip():
        print("Set AEGIS_AGENT_TOKEN to the agent bearer token.", file=sys.stderr)
        return 2

    headers = {"Authorization": f"Bearer {AGENT_TOKEN}"}
    if IDEMPOTENCY_KEY:
        headers["Idempotency-Key"] = IDEMPOTENCY_KEY

    try:
        response = httpx.post(
            f"{API_URL}/transactions/propose",
            headers=headers,
            json=PAYLOAD,
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        print(
            f"Proposal rejected (HTTP {exc.response.status_code}): {detail}",
            file=sys.stderr,
        )
        return 1
    except httpx.HTTPError as exc:
        print(f"Could not reach the Aegis API: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(response.json(), indent=2))
    print("\nProposal created/replayed. Execution is not performed by this client.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
