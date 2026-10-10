# Aegis Agent Wallet

A testnet-only AI-agent wallet prototype. This build includes deterministic policy checks, explainable heuristic risk scoring, persistent transaction state, atomic daily-spend reservations, time-limited fingerprint-bound approval, an append-only approval/execution audit trail, bearer-token separation for agent and owner operations, idempotent proposal retries, and a simulated executor.

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
export AEGIS_APPROVAL_EXPIRES_SECONDS="300"
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

## Policy configuration validation

Policy construction fails fast when global transaction or daily limits are non-positive, the approval threshold is negative, the agent ID or configured token symbols are empty, or chain IDs are missing/non-positive. Trusted token metadata requires a positive chain ID, a non-empty symbol, and decimals between 0 and 36. An approval threshold of zero is valid and means every positive transaction meets the approval threshold. Invalid recipient-specific limits and approval-expiry settings are also rejected.

## Workflow endpoints

- `POST /transactions/propose` — atomically calculates today's persisted spend, reserves the proposed amount, evaluates policy/risk, and saves the proposal. Supports the optional `Idempotency-Key` header (8–128 characters). Reusing a key with the same proposal returns the original record; reusing it with a different proposal returns HTTP 409.
- `GET /transactions/{transaction_id}` — retrieves persisted state; owner token required.
- `GET /transactions/{transaction_id}/audit` — retrieves ordered audit events, including actor role, state transition, fingerprint, timestamp, and event details; owner token required.
- `POST /transactions/{transaction_id}/approve` — requires the owner token, the exact stored SHA-256 fingerprint, and body confirmation `APPROVE`. Approval expires after `AEGIS_APPROVAL_EXPIRES_SECONDS` (default 300 seconds); expired approvals must be granted again.
- `POST /transactions/{transaction_id}/execute` — requires the owner token and re-evaluates policy, daily spending, and heuristic risk under the same SQLite write lock used for the simulated state transition. If risk escalates to high, human approval is required before simulation.
- `POST /policy/evaluate` and `POST /risk/assess` — standalone evaluation endpoints; their caller-provided spend is not trusted by the transaction workflow.

The daily limit is calculated from persisted same-day transactions for the same chain and token. Ready, awaiting-approval, approved, and simulated-executed transactions count against the daily limit; blocked proposals do not. Pending transactions reserve budget to reduce overspending from concurrent proposals. This is a local prototype accounting model, not a substitute for confirmed on-chain balances/receipts in a production wallet.

## Recipient daily spending limits

Policies may configure `recipient_daily_limits_base_units`, mapping a recipient address to a maximum daily amount. The limit is evaluated separately for each chain and token contract because base units are token-specific. The workflow computes persisted same-day spend for that recipient inside its SQLite write lock both when proposing a transaction and immediately before simulated execution. Ready, awaiting-approval, approved, and simulated-executed transactions reserve recipient budget, matching the global daily-limit accounting model. A transaction that would exceed the recipient cap is hard-blocked and cannot be approved. Omit a recipient from the mapping to leave it governed only by the global daily limit and other policy checks. Configure limits in base units (for the demo USDC token, 1 USDC = 1,000,000 base units).

## Recipient-specific transaction limits

Policies may configure `recipient_transaction_limits_base_units`, mapping a recipient address to a maximum amount for any single transaction. This cap is in token base units and is enforced alongside the global per-transaction limit. A configured recipient cap is a hard block when exceeded and cannot be overridden by owner approval; recipients without an entry continue to use the global limit. Address matching is case-insensitive. Negative recipient transaction caps are rejected during policy construction.

## Recipient-specific token policy

The policy can bind a recipient address to the exact token contract addresses that recipient may receive through `recipient_token_allowlist`. When a recipient has an entry in this mapping, a token contract absent from that recipient's set is a hard policy block and cannot be overridden by owner approval. Recipient and token addresses are normalized case-insensitively. Recipients without a mapping entry continue to follow the existing unknown-recipient rule (approval required by default); they are not implicitly granted a token-specific allowlist. The demo policy explicitly permits the configured Base Sepolia USDC contract for its two configured recipients.

This constrains the token/recipient combination, but does not establish the real-world identity of a recipient or guarantee a token's authenticity.

## Approval expiry

Owner approvals are timestamped in UTC and expire after `AEGIS_APPROVAL_EXPIRES_SECONDS` seconds (default `300`). Expiry is checked under the workflow write lock before simulated execution; an expired or timestamp-less legacy approval is invalidated, returned to `awaiting_approval`, and recorded as an `approval_expired` audit event. Set the value to a positive integer. The API exposes `approved_at` for approved transactions and retains it after simulated execution for history.

## Safety boundaries and limitations

- Recipient addresses must match the EVM format (`0x` followed by 40 hexadecimal characters); malformed recipients are blocked and cannot be approved. Format validation does not prove that an address belongs to the intended person or contract.
- **Execution is simulated. No private key is loaded, no transaction is signed, and no RPC request is sent.** The simulated reference is not a transaction hash.
- The demo token is Circle's published Base Sepolia USDC testnet contract. Demo recipient addresses remain placeholders and are not verified accounts.
- Bearer-token authentication is a development guard, not a full identity/authorization system. Keep the API bound to localhost and do not expose it publicly.
- The risk score is a transparent heuristic, not a validated fraud detector.
- Hard policy blocks cannot be overridden by approval.
- Idempotency protects retries that reuse the same key; it does not prove on-chain exactly-once execution. A real executor needs durable submission state, nonce management, receipt reconciliation, secure key custody, and chain-specific tests.
- Standalone policy/risk endpoints accept a supplied daily-spend figure for demonstration; never use those endpoint responses as execution authorization.
- SQLite state is persistent in `aegis-workflow.sqlite3` by default. Set `AEGIS_DB_PATH` to change the path. Existing prototype databases are migrated additively. Audit rows are protected against updates/deletes by SQLite triggers, but this is application-level tamper resistance, not cryptographic tamper-proofing against someone who controls the database file.

## Demo flow

1. Start the API with the two distinct tokens exported.
2. In `/docs`, create a proposal with the agent bearer token and no `daily_spent_base_units` field.
3. Use the owner token to retrieve, approve, or simulate execution.
4. A small payment to an allow-listed recipient may become `ready`. An unknown recipient or high-risk proposal needs approval. A policy violation is `blocked`.

This remains a local prototype with read-only chain inspection and simulation-only transaction execution. Do not load real keys or use real funds.
