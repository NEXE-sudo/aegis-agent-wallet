# Aegis Agent Wallet

A testnet-only AI-agent wallet prototype. This build includes deterministic policy checks, explainable heuristic risk scoring, persistent transaction state, atomic daily-spend reservations, time-limited fingerprint-bound approval with stale-approval expiry checks on execution and reapproval with persisted-proposal integrity verification, an append-only approval/execution audit trail, bearer-token separation for agent and owner operations, idempotent proposal retries with persisted-proposal integrity verification, and simulated execution by default with an explicitly gated AegisVault-only Base Sepolia testnet executor.

For the environment-variable reference, safe local binding, and operational troubleshooting, see the [runtime configuration guide](docs/runtime-configuration.md). For the complete contract-hardening and evidence-verification workflow, see the [Phase 5 hardening runbook](docs/phase-5-hardening.md).

## macOS setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if needed (on macOS, use `brew install uv`). From the repository root, install the exact dependencies recorded in `uv.lock`:

```bash
uv sync --locked --extra dev
```

Create local secrets and configure the environment before starting the API:

```bash
export AEGIS_AGENT_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AEGIS_APPROVAL_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AEGIS_DB_PATH="./aegis-workflow.sqlite3"
export AEGIS_APPROVAL_EXPIRES_SECONDS="300"
# Optional: set this for read-only chain inspection. Testnet execution additionally
# requires `AEGIS_EXECUTION_MODE=base_sepolia_vault`, a deployed vault, and a throwaway agent session key.
# export BASE_SEPOLIA_RPC_URL="https://YOUR_BASE_SEPOLIA_RPC_PROVIDER_URL"
uv run --locked playwright install chromium
uv run --locked python -m pytest -q
uv run --locked ruff check .
uv run --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Keep both tokens private. The agent token and owner/approval token must be different; protected endpoints fail closed with HTTP 503 if either token is blank or both configured tokens are identical. A wrong or missing request token returns HTTP 401. An empty or whitespace-only `AEGIS_DB_PATH` is rejected at startup rather than silently using a temporary SQLite database. API docs: http://127.0.0.1:8000/docs

For requests, send `Authorization: Bearer <AEGIS_AGENT_TOKEN>` to `POST /transactions/propose`. Use `Authorization: Bearer <AEGIS_APPROVAL_TOKEN>` for transaction retrieval, approval, and simulated execution. `/health`, `/policy/evaluate`, and `/risk/assess` are demo endpoints; the latter two accept caller-provided spend only for isolated evaluation and do not create transactions.

Agent-token calls include proposal creation, limited transaction-status and market-quote reads, and a vault-only execution endpoint for eligible proposals. The agent cannot approve, reject, pause, change policy, or use the owner token; proposals requiring approval remain blocked until the owner approves them. The autonomous execution endpoint is available only in `base_sepolia_vault` mode and relies on the vault's on-chain enforcement. See the [agent guide](docs/agent.md) for task structure, LLM configuration, and offline evaluation.

## Persisted governance and agent decision queue

The owner can save a single active mandate, register Base Sepolia payees, and queue structured payment tasks. These records are stored in SQLite with payload hashes and append-only hash-chained audit records. See the [mandate-driven agent guide](docs/mandate-driven-agent.md) for request schemas and limitations.

The owner-authenticated endpoints are `PUT /governance/mandate`, `PUT /governance/payees/{payee_id}`, and `POST /governance/tasks`; the corresponding GET endpoints read the saved mandate, payees, and tasks. The owner can verify governance integrity at `GET /governance/audit/verify`.

The agent processes only owner-created tasks using `POST /agent/governance/decide-pending` or runner mode `{"mode":"governance_queue"}`. In this mode the caller supplies no payment fields: the task amount comes from the owner-created structured task, and recipient/token/chain metadata comes from the owner-managed payee registry. A proposed transaction still goes through the existing policy/risk gateway and can be blocked or require owner approval. Tasks are bound to the mandate and payee versions present when queued, and governance-created proposals cannot be approved or executed after those records change or integrity verification fails. The agent does not approve or execute it.

Important limitations: mandates currently support one token per budget to avoid mixing incompatible token base units. The mandate's `available_balance_base_units` is an owner-maintained planning value, not a live chain balance. Proposed amounts are conservatively retained in governance budget history even if the downstream proposal is later blocked or cancelled. The governance queue is deterministic; the existing optional LLM tool loop has not yet been integrated as a reasoner over this persisted queue.

## Local frontend control centre

Start the API with the commands above, then open [http://127.0.0.1:8000/](http://127.0.0.1:8000/). The dependency-free frontend shares the API origin and includes an overview, read-only crypto/equity market quotes, proposal submission, owner approvals, mode-aware execution, workflow search, audit inspection, and local connection settings. Connect with distinct local agent and owner tokens. The browser keeps them in memory only; disconnect or reload to clear them. Market prices are informational (equity quotes are daily closes). See [the frontend guide](docs/frontend.md) for functionality, validation, and safety limitations.

The UI reflects the current USDC-on-Base-Sepolia demo workflow. Owner-authenticated market quotes are provided by the separate read-only market-data API; see [the market-data guide](docs/market-data.md) for supported symbols and provider freshness. No broker or exchange order routing is provided. On-chain transfers are restricted to explicitly enabled AegisVault execution on Base Sepolia; direct EOA transfers are disabled and simulated mode is the default.

## Base Sepolia integration (v0.7.0)

Set `BASE_SEPOLIA_RPC_URL` to an HTTPS JSON-RPC endpoint from your RPC provider. Do not commit provider URLs containing private API keys. The following endpoints require the owner bearer token:

- `GET /chain/base-sepolia/status` — reads `eth_chainId` and reports whether it is 84532.
- `GET /chain/base-sepolia/token/{address}` — checks the selected chain, reads contract bytecode, and calls ERC-20 `symbol()`, `decimals()`, and optional `name()` using `eth_call`.
- `GET /chain/base-sepolia/address/{address}/balances` — reads native ETH and the demo Base Sepolia USDC balance for a public address, returning exact base-unit integers and decimal strings without floating-point conversion.

These endpoints are read-only. They do not sign or submit transactions. The demo policy allowlists Circle's published Base Sepolia USDC contract (`0x036CbD53842c5426634e7929541eC2318f3dCF7e`; 6 decimals), cross-checked against [Circle's USDC on Base information](https://www.circle.com/multi-chain-usdc/base) and your live RPC metadata response. Bytecode and readable ERC-20 metadata alone do **not** establish token authenticity; check official sources before adding any other token. Requests fail closed if the RPC URL is missing, the endpoint is on the wrong chain, or the RPC returns an error.

## Policy configuration validation

API amounts, supplied evaluation spend, chain IDs, and configured global/recipient spending limits are bounded to SQLite's signed 64-bit integer range. Oversized API values are rejected with HTTP 422 before they can reach persistence; invalid policy bounds fail during policy construction. Persisted amounts remain individually bounded, while global and recipient spend totals are summed with Python integers so unusually large aggregate reservations are evaluated and hard-blocked without SQLite integer-overflow errors.

Policy construction fails fast when global transaction or daily limits are non-positive, the approval threshold is negative, the agent ID or configured token symbols are empty, or chain IDs are missing/non-positive. Trusted token metadata requires a positive chain ID, a non-empty symbol, and decimals between 0 and 36. An approval threshold of zero is valid and means every positive transaction meets the approval threshold. Invalid recipient-specific limits and approval-expiry settings are also rejected.

## Workflow endpoints

- `POST /transactions/propose` — atomically calculates today's persisted spend, reserves the proposed amount, evaluates policy/risk, and saves the proposal. Supports the optional `Idempotency-Key` header (8–128 characters). Reusing a key with the same proposal returns the original record; reusing it with a different proposal returns HTTP 409.
- `GET /transactions/{transaction_id}` — retrieves persisted state; owner token required.
- `GET /agent/transactions/{transaction_id}` — agent-token-only status view with no proposal or audit details.
- `GET /agent/market-data/{asset_class}/{symbol}` — agent-token-only read of validated market quotes.
- `GET /transactions/{transaction_id}/audit` — retrieves ordered audit events, including actor role, state transition, fingerprint, timestamp, and event details; owner token required.
- `POST /transactions/{transaction_id}/approve` — requires the owner token, the exact stored SHA-256 fingerprint, and body confirmation `APPROVE`. Approval expires after `AEGIS_APPROVAL_EXPIRES_SECONDS` (default 300 seconds); expired approvals must be granted again.
- `POST /transactions/{transaction_id}/cancel` — requires the owner token and cancels a `ready`, `awaiting_approval`, or `approved` proposal, releasing its daily spend reservation.
- `POST /transactions/{transaction_id}/execute` — requires the owner token and re-evaluates policy, daily spending, and heuristic risk under the same SQLite write lock used for the simulated state transition. If risk escalates to high, human approval is required before simulation.
- `POST /policy/evaluate` and `POST /risk/assess` — standalone evaluation endpoints; their caller-provided spend is not trusted by the transaction workflow.
- `GET /policy/config` — owner-authenticated, read-only view of the active demo policy, allowlists, transaction/daily limits, and approval settings.
- `GET /spending/summary` — owner-authenticated daily spend and remaining local workflow budget for each configured, policy-allowed token.

`GET /spending/summary` reports UTC-day local workflow accounting per configured, policy-allowed token. It includes active reservations, uncertain on-chain submissions, and executions recorded for the current UTC day; cancelling or rejecting a pending proposal releases its reservation. Values are returned in token base units and are not a substitute for on-chain spend or receipt/finality verification.

The daily limit is calculated from persisted same-day transactions for the same chain and token. Ready, awaiting-approval, approved, and simulated-executed transactions count against the daily limit; blocked and cancelled proposals do not. Pending transactions reserve budget to reduce overspending from concurrent proposals, and an owner can cancel pending proposals to release those reservations. This is a local prototype accounting model, not a substitute for confirmed on-chain balances/receipts in a production wallet.

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

- Recipient addresses must match the EVM format (`0x` followed by 40 hexadecimal characters); malformed recipients are blocked and cannot be approved. Address format validation does not prove that an address belongs to the intended person or contract.
- **Execution defaults to simulation.** Actual ERC-20 transfers require explicit `AEGIS_EXECUTION_MODE=base_sepolia_vault` configuration and are submitted only through the AegisVault on Base Sepolia (chain ID 84532). Direct EOA token transfers are disabled. Vault mode requires a deployed vault, a separate throwaway agent session key, and an HTTPS RPC URL; invalid configuration fails closed. It does not support mainnet or real-fund execution. An RPC-accepted transaction is not proof of mining or finality.
- The demo token is Circle's published Base Sepolia USDC testnet contract. Demo recipient addresses remain placeholders and are not verified accounts.
- Bearer-token authentication is a development guard, not a full identity/authorization system. Keep the API bound to localhost and do not expose it publicly.
- The risk score is a transparent heuristic, not a validated fraud detector.
- Hard policy blocks cannot be overridden by approval.
- Uncertain testnet submissions reuse the persisted signed transaction bytes rather than create a new nonce/transfer. Keep the SQLite database protected; this prototype does not provide production key custody or full receipt/finality reconciliation.
- Standalone policy/risk endpoints accept a supplied daily-spend figure for demonstration; never use those endpoint responses as execution authorization.
- SQLite state is persistent in `aegis-workflow.sqlite3` by default. Set `AEGIS_DB_PATH` to change the path. Existing prototype databases are migrated additively. Audit rows are protected against updates/deletes by SQLite triggers, but this is application-level tamper resistance, not cryptographic tamper-proofing against someone who controls the database file.

## Authenticated Python client

Run `python examples/propose_transaction.py` with `AEGIS_AGENT_TOKEN` set in your environment to submit a proposal through the authenticated API. The client prints the proposal response and never approves or executes it. See the [Python client walkthrough](docs/python-client.md) for configuration, optional idempotency, and safety boundaries.

## Demo flow

Follow the step-by-step [reproducible demo scenario](docs/demo-scenario.md) to exercise an allowed proposal, human approval, a hard block, idempotency replay/conflict, and audit retrieval with expected outcomes.

This remains a local prototype. Use simulated mode or the unaudited vault integration with throwaway Base Sepolia wallets funded by faucets; never use mainnet keys or real funds.

## Documentation

- [Technical document](docs/technical-document.md) — architecture, scope, and on-chain flow.
- [Chatbot handoff](docs/chatbot-handoff.md) — current project state, required PR workflow, and phased roadmap.
- [Security self-assessment](docs/security-self-assessment.md) — implementation-based security assessment and residual risks.
- [AegisVault on-chain enforcement](docs/onchain-enforcement.md) — vault design, runbook, and evidence placeholders.
- [Deployment verification](docs/deployment-verification.md) — read-only verification of a mined Base Sepolia AegisVault deployment receipt, owner, bytecode and confirmation depth.
- [Threat model](docs/threat-model.md) — security goals, assets, trust boundaries, and threats.
- [Runtime configuration](docs/runtime-configuration.md) — environment variables, local binding, and operational troubleshooting.
- [Agent and evaluation guide](docs/agent.md) — agent tools, trusted payment inputs, and evaluation.
- [Workflow state transitions](docs/workflow-state-transitions.md) — workflow statuses and state changes.
- [Reproducible demo scenario](docs/demo-scenario.md) — API walkthrough and expected outcomes.
- [Python client walkthrough](docs/python-client.md) — authenticated proposal-only API client.
- [Frontend guide](docs/frontend.md) — frontend functionality, validation, and safety limitations.
- [Market-data guide](docs/market-data.md) — read-only market-data endpoint and providers.
- [Version 1.10.3 release notes](docs/release-notes-v1.10.3.md) — testnet and agent-evaluation release notes (unreleased).


## Reproducible evidence bundle

Build a portable deterministic-evaluation report, with optional read-only Base Sepolia transfer and audit verification, using the [evidence bundle guide](docs/evidence-bundle.md).
