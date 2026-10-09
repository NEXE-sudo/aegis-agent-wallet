# Aegis Agent Wallet

A testnet-only AI-agent wallet prototype. This build includes deterministic policy checks, explainable heuristic risk scoring, persistent transaction state, atomic daily-spend reservations, fingerprint-bound approval, bearer-token separation for agent and owner operations, idempotent proposal retries, and a simulated executor.

## macOS setup

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Create local secrets and configure the environment before starting the API:

```bash
export AEGIS_AGENT_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AEGIS_APPROVAL_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AEGIS_DB_PATH="./aegis-workflow.sqlite3"
export BASE_SEPOLIA_RPC_URL="https://YOUR_BASE_SEPOLIA_RPC_PROVIDER_URL"
python -m pytest -q
uvicorn app.main:app --reload
```

Keep both tokens private. The agent token and owner/approval token must be different. If a required token is missing, the protected endpoint fails closed with HTTP 503. A wrong or missing token returns HTTP 401. API docs: http://127.0.0.1:8000/docs

For requests, send `Authorization: Bearer <AEGIS_AGENT_TOKEN>` to `POST /transactions/propose`. Use `Authorization: Bearer <AEGIS_APPROVAL_TOKEN>` for transaction retrieval, approval, and simulated execution. `/health`, `/policy/evaluate`, and `/risk/assess` are demo endpoints; the latter two accept caller-provided spend only for isolated evaluation and do not create transactions.

## Base Sepolia integration (v0.7.0)

Set `BASE_SEPOLIA_RPC_URL` to an HTTPS JSON-RPC endpoint from your RPC provider. Do not commit provider URLs containing private API keys. The following endpoints require the owner bearer token:

- `GET /chain/base-sepolia/status` — reads `eth_chainId` and reports whether it is 84532.
- `GET /chain/base-sepolia/token/{address}` — checks the selected chain, reads contract bytecode, and calls ERC-20 `symbol()`, `decimals()`, and optional `name()` using `eth_call`.

These endpoints are read-only. They do not sign or submit transactions. The demo policy allowlists Circle's published Base Sepolia USDC contract (`0x036CbD53842c5426634e7929541eC2318f3dCF7e`; 6 decimals), cross-checked against [Circle's USDC on Base information](https://www.circle.com/multi-chain-usdc/base) and your live RPC metadata response. Bytecode and readable ERC-20 metadata alone do **not** establish token authenticity; check official sources before adding any other token. Requests fail closed if the RPC URL is missing, the endpoint is on the wrong chain, or the RPC returns an error.

## Workflow endpoints

- `POST /transactions/propose` — atomically calculates today's persisted spend, reserves the proposed amount, evaluates policy/risk, and saves the proposal. Supports the optional `Idempotency-Key` header (8–128 characters). Reusing a key with the same proposal returns the original record; reusing it with a different proposal returns HTTP 409.
- `GET /transactions/{transaction_id}` — retrieves persisted state; owner token required.
- `POST /transactions/{transaction_id}/approve` — requires the owner token, the exact stored SHA-256 fingerprint, and body confirmation `APPROVE`.
- `POST /transactions/{transaction_id}/execute` — requires the owner token and re-evaluates policy and daily spending under the same SQLite write lock used for the simulated state transition.
- `POST /policy/evaluate` and `POST /risk/assess` — standalone evaluation endpoints; their caller-provided spend is not trusted by the transaction workflow.

The daily limit is calculated from persisted same-day transactions for the same chain and token. Ready, awaiting-approval, approved, and simulated-executed transactions count against the daily limit; blocked proposals do not. Pending transactions reserve budget to reduce overspending from concurrent proposals. This is a local prototype accounting model, not a substitute for confirmed on-chain balances/receipts in a production wallet.

## Safety boundaries and limitations

- **Execution is simulated. No private key is loaded, no transaction is signed, and no RPC request is sent.** The simulated reference is not a transaction hash.
- The demo token is Circle's published Base Sepolia USDC testnet contract. Demo recipient addresses remain placeholders and are not verified accounts.
- Bearer-token authentication is a development guard, not a full identity/authorization system. Keep the API bound to localhost and do not expose it publicly.
- The risk score is a transparent heuristic, not a validated fraud detector.
- Hard policy blocks cannot be overridden by approval.
- Idempotency protects retries that reuse the same key; it does not prove on-chain exactly-once execution. A real executor needs durable submission state, nonce management, receipt reconciliation, secure key custody, and chain-specific tests.
- Standalone policy/risk endpoints accept a supplied daily-spend figure for demonstration; never use those endpoint responses as execution authorization.
- SQLite state is persistent in `aegis-workflow.sqlite3` by default. Set `AEGIS_DB_PATH` to change the path. Existing prototype databases are migrated additively.

## Demo flow

1. Start the API with the two distinct tokens exported.
2. In `/docs`, create a proposal with the agent bearer token and no `daily_spent_base_units` field.
3. Use the owner token to retrieve, approve, or simulate execution.
4. A small payment to an allow-listed recipient may become `ready`. An unknown recipient or high-risk proposal needs approval. A policy violation is `blocked`.

This remains a local prototype with read-only chain inspection and simulation-only transaction execution. Do not load real keys or use real funds.
