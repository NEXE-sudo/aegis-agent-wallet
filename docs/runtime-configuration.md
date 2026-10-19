# Runtime Configuration and Safe Local Startup

This guide describes the environment variables read by the prototype. Execution is simulated by default; Base Sepolia testnet signing and submission require explicit configuration. Keep the API on loopback for local development; the bearer tokens are development controls, not production identity.

The package declares Python `>=3.11`. CI tests Python 3.11, 3.12, and 3.14; other Python releases satisfying the declared constraint are not individually tested in CI.

## Configuration reference

| Variable | Required? | Default | Purpose and validation |
| --- | --- | --- | --- |
| `AEGIS_AGENT_TOKEN` | Yes for proposal creation | None | Bearer token for `POST /transactions/propose`. Must be non-empty. Protected workflow endpoints fail closed with HTTP 503 if required token configuration is missing. |
| `AEGIS_APPROVAL_TOKEN` | Yes for owner operations | None | Bearer token for transaction reads, approval, simulated execution, audit reads, and read-only chain inspection. Must be non-empty. For protected workflow endpoints, it must differ from the agent token. |
| `AEGIS_API_URL` | No | `http://127.0.0.1:8000` | Base URL used by `agent.AegisApiClient`. Requires HTTPS except for loopback HTTP; embedded credentials, query strings, and fragments are rejected. |
| `AEGIS_LLM_BASE_URL` | Optional LLM mode | None | Base URL for an OpenAI-compatible chat-completions API. Configure together with `AEGIS_LLM_MODEL` and `AEGIS_LLM_API_KEY`; requires HTTPS except for loopback HTTP. |
| `AEGIS_LLM_MODEL` | Optional LLM mode | None | Model name sent to the configured OpenAI-compatible provider. |
| `AEGIS_LLM_API_KEY` | Optional LLM mode | None | Key sent only to the configured LLM provider. It is not logged or included in API requests to Aegis. |
| `AEGIS_DB_PATH` | No | `aegis-workflow.sqlite3` | SQLite database path. An explicitly empty or whitespace-only value is rejected; choose a writable path and protect the database and its backups. Relative paths are resolved from the process working directory. |
| `AEGIS_APPROVAL_EXPIRES_SECONDS` | No | `300` | Approval lifetime in seconds. Must parse as an integer greater than zero; invalid values prevent the application from initializing correctly. Expired approvals must be granted again. |
| `AEGIS_MAX_PENDING_PROPOSALS` | No | `10` | Maximum number of active `ready`, `awaiting_approval`, and `approved` proposals. The value must be a positive integer; invalid values prevent startup. When the cap is reached, new proposals fail with HTTP 429 and no row is created, while idempotent replays still return the stored record. |
| `AEGIS_EXECUTION_MODE` | No | `simulated` | One of `simulated` or `base_sepolia_vault`. The retired `base_sepolia` direct EOA mode and any other value prevent startup. Vault execution is never selected automatically. |
| `AEGIS_VAULT_ADDRESS` | For `base_sepolia_vault` mode and the owner-only vault helper/status routes | None | Validated EVM address of the deployed Base Sepolia AegisVault. Vault execution requires contract code and the configured key to be its agent, with a signer distinct from the vault owner. The read-only status and approval-helper routes use only this public address; they do not read a private key or sign. |
| `AEGIS_TESTNET_PRIVATE_KEY` | Only for `base_sepolia_vault` mode | None | A throwaway Base Sepolia **agent session key**, loaded only when vault execution is explicitly enabled; never use the vault owner key here. A missing or invalid key prevents startup. Keep it in the process environment; it is never accepted in an API request, returned, or logged. |
| `BASE_SEPOLIA_RPC_URL` | Only for chain inspection or `base_sepolia_vault` mode | None | HTTP(S) JSON-RPC endpoint for read-only inspection. Vault execution requires a valid HTTPS URL. Missing or invalid execution configuration prevents startup; RPC failures return sanitized HTTP errors. Never commit a provider URL containing credentials. |

The agent token authenticates proposal creation and the limited `GET /agent/transactions/{transaction_id}` and `GET /agent/market-data/{asset_class}/{symbol}` reads. The owner token remains required for full transaction records, approvals, rejection, execution, audit events, and owner market-data or chain-inspection operations. See the [agent guide](agent.md) for LLM configuration and the evaluation harness.

With the default `simulated` mode, the workflow does not load a private key or send transaction RPC calls. The optional chain inspection routes remain read-only. Actual transfers are available only in `base_sepolia_vault` mode: owner execution and eligible agent execution submit calls through the AegisVault on Base Sepolia (chain ID `84532`). The executor verifies vault code and `owner()`/`agent()` identity separation, validates the exact proposal-bound calldata, and simulates the vault call before signing. The vault independently enforces its caps, recipient/approval rules, and pause state. The retired direct EOA mode is rejected at startup; there is no fallback from vault execution to direct token transfers or simulation.
When `AEGIS_VAULT_ADDRESS` and `BASE_SEPOLIA_RPC_URL` are configured, the
owner-only `GET /chain/base-sepolia/vault/status` reads the vault owner, agent,
pause state, token policies, today's spend, and allowlisted-recipient flags, then
reports every difference from the Aegis demo policy. The owner-only
`GET /transactions/{transaction_id}/vault-approval` prepares exact
`approveTransfer` calldata for eligible proposals; it supplies a suggested
one-hour expiry and a `cast send` command using the operator-provided
`OWNER_PRIVATE_KEY` environment variable. The API never obtains that key and
never signs the approval.
`base_sepolia_vault` is an unaudited, testnet-only integration. It uses one
agent address and a UTC-day-bucket cap, not a rolling 24-hour limit. The separate
vault owner key can change token limits, recipients, approvals, and roles, or
withdraw assets; it must never be configured in or supplied to the Aegis server.
The on-chain status comparison is a point-in-time read from the configured RPC,
not a policy migration or an independent provider verification. Read
[on-chain enforcement](onchain-enforcement.md) for contract trust assumptions,
operational commands, bypass demonstration, and evidence placeholders.

The owner-only `POST /pause` and `POST /resume` controls persist a pause flag and append-only control log in SQLite. While paused, new proposal attempts are recorded as `blocked` with a clear reason, and owner approval/execution requests fail with HTTP 409. Reads, cancellation, rejection, and receipt lookups continue to work; an existing idempotent replay still resolves to its stored record.

Vault execution always simulates the exact proposed `transferToken` call before signing. A failed or malformed simulation returns HTTP 409 with a machine-readable code and does not sign or broadcast. This simulation cannot be disabled by environment configuration; on-chain vault checks remain authoritative when the transaction is submitted.

## Start locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if needed, then run these commands from the repository root on macOS or another Unix-like shell. `uv sync --locked --extra dev` creates the project environment and installs the exact locked dependencies; it fails if `uv.lock` is out of sync with the project metadata.

```bash
uv sync --locked --extra dev

export AEGIS_AGENT_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AEGIS_APPROVAL_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AEGIS_DB_PATH="./aegis-workflow.sqlite3"
export AEGIS_APPROVAL_EXPIRES_SECONDS="300"

# Optional: set this only if you intend to use the read-only chain endpoints.
# export BASE_SEPOLIA_RPC_URL="https://YOUR_BASE_SEPOLIA_RPC_PROVIDER_URL"

uv run --locked python -m pytest -q
uv run --locked ruff check .
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Generate two distinct tokens. Do not paste real tokens or provider credentials into source files, issue reports, screenshots, or logs. The checked-in `.env.example` contains placeholders only; it is a reference, not a secret store. The commands above explicitly export the variables into the current shell.

Open `http://127.0.0.1:8000/` for the local frontend control centre, or use `http://127.0.0.1:8000/docs` for the interactive API documentation. The frontend uses the same origin and requires no CORS configuration. Keep the `--host 127.0.0.1` binding for local use. Do not change it to `0.0.0.0` or expose this development API to an untrusted network: the project does not provide production identity, TLS termination, rate limiting, or deployment hardening.

## Health endpoint: what it does and does not prove

`GET /health` returns the application status and selected execution mode. It is a lightweight process/API check, **not** a readiness check for SQLite or the external RPC provider. It does not prove that the database is writable, that chain inspection is available, or that a transaction occurred on-chain.

The chain inspection routes are separate:

- `GET /chain/base-sepolia/status` checks the configured RPC endpoint's reported chain ID.
- `GET /chain/base-sepolia/token/{address}` reads contract bytecode and ERC-20 metadata.

Both chain routes require the owner/approval bearer token and are read-only. A readable token response does not prove that a token is official or safe.

## Manual Base Sepolia test transfer

Actual transfers must go through the AegisVault; the direct EOA `base_sepolia`
execution mode has been retired and is rejected at startup.

1. Follow the deployment and owner setup instructions in
   [on-chain enforcement](onchain-enforcement.md), using Base Sepolia testnet
   assets only. Keep the vault owner key separate from the API server.
2. Configure the API process with `AEGIS_EXECUTION_MODE="base_sepolia_vault"`,
   `AEGIS_VAULT_ADDRESS`, an HTTPS `BASE_SEPOLIA_RPC_URL`, and
   `AEGIS_TESTNET_PRIVATE_KEY` containing only the separate agent session key.
   Never paste keys or provider credentials into source files, chat, screenshots,
   or logs.
3. Keep `AEGIS_AGENT_TOKEN` and `AEGIS_APPROVAL_TOKEN` distinct. Propose a
   policy-eligible transfer through the API. If approval is required, use the
   owner-only vault approval helper and the separate owner key as described in
   the runbook. The server never receives the owner key.
4. Execute only after checking the proposal and vault status. Verify the returned
   transaction hash and receipt independently; an RPC submission is not proof
   of finality. Do not use mainnet or real funds.

`base_sepolia_vault` is an unaudited testnet integration. A deployment and
successful transfer must not be described as production readiness.

## Vault bypass demonstration

The bypass demonstration uses `AEGIS_TESTNET_PRIVATE_KEY` as the agent key only
and `BASE_SEPOLIA_RPC_URL` as an HTTPS Base Sepolia endpoint. It first verifies
the chain ID, vault code, and configured agent, then simulates an over-cap
transfer, an unlisted-recipient transfer, and a disallowed-token transfer. It
prints the recognized contract revert for each and does not broadcast by
default:

```bash
uv run python scripts/vault_bypass_demo.py \
  --vault "<AEGISVAULT_ADDRESS>" \
  --token "<ALLOWED_TEST_TOKEN_ADDRESS>" \
  --unlisted-recipient "<UNLISTED_TEST_RECIPIENT>"
```

Only if each simulation confirms the expected rejection, the explicit
`--broadcast` flag submits the same reverting calls with a fixed gas limit so
that the rejections can be recorded on Base Sepolia:

```bash
uv run python scripts/vault_bypass_demo.py \
  --vault "<AEGISVAULT_ADDRESS>" \
  --token "<ALLOWED_TEST_TOKEN_ADDRESS>" \
  --unlisted-recipient "<UNLISTED_TEST_RECIPIENT>" \
  --broadcast
```

Do not put the agent key on the command line, in shell history, or in a file.
The script reads it only from the process environment and never prints it. The
disallowed-token case uses the vault's own address as the token argument so
that it is known not to be an allowlisted token; it reverts before any token
contract call. The script refuses to broadcast if a simulation unexpectedly
succeeds.

## Workflow cancellation

`POST /transactions/{transaction_id}/cancel` requires the owner token. It cancels a `ready`, `awaiting_approval`, or `approved` proposal, clears any approval, records an audit event, and releases the daily spend reservation. Blocked, already-cancelled, and simulated-executed transactions cannot be cancelled.

`POST /transactions/{transaction_id}/reject` also requires the owner token and releases the reservation, recording an audit event. A submitted testnet transaction cannot be cancelled or rejected by this workflow.

## Read-only wallet balances

The owner-authenticated `GET /chain/base-sepolia/address/{address}/balances` endpoint reads the public address's native ETH balance and the demo USDC balance. It validates the address and confirms the RPC endpoint reports Base Sepolia (chain ID `84532`) before reading balances. The response includes exact integer base units and human-readable decimal strings; it does not use floating-point arithmetic, load a private key, sign, or submit transactions. Only the configured demo USDC token is included in this initial endpoint. A malformed address returns HTTP 422, a wrong-chain or missing-RPC configuration returns HTTP 503, and unavailable or malformed RPC data returns HTTP 502.

## Common configuration symptoms

- **HTTP 503 from a protected workflow endpoint:** check that both `AEGIS_AGENT_TOKEN` and `AEGIS_APPROVAL_TOKEN` are present, non-empty, and different.
- **HTTP 401 from a protected endpoint:** check the endpoint's expected bearer token and ensure the request uses `Authorization: Bearer <token>`.
- **Application initialization fails around approval expiry:** set `AEGIS_APPROVAL_EXPIRES_SECONDS` to a positive integer, for example `300`.
- **Database-path validation error:** ensure `AEGIS_DB_PATH` is non-empty and points to a location the process can access.
- **HTTP 503 from a chain endpoint:** configure `BASE_SEPOLIA_RPC_URL` and verify the endpoint is reachable.
- **HTTP 502 from a chain endpoint:** inspect provider availability and configuration without sharing the full credential-bearing URL. The client intentionally returns sanitized request errors.
- **Chain status reports a different chain ID:** verify that the configured RPC endpoint is for Base Sepolia (chain ID `84532`).
- **Execution configuration prevents startup:** set `AEGIS_EXECUTION_MODE` to `simulated`, or configure `base_sepolia_vault`, a valid vault address, a throwaway agent session key, and an HTTPS RPC URL.

## Operational cautions

- SQLite state persists in the configured database file. Do not delete it as a casual troubleshooting step; it contains workflow and audit history.
- Back up the database only while following a consistent SQLite backup procedure, and protect backup files as sensitive application data.
- Audit triggers guard against ordinary row updates/deletes through the application database connection. They are not cryptographic tamper protection against someone who controls the database file.
- This is a local prototype, not production key custody. The default simulation mode never loads a key. The optional testnet mode holds a throwaway private key in process memory and may keep already-signed transaction bytes in the SQLite database while safely retrying a submission. Protect the host and database; no mainnet execution is supported.


## Read-only policy configuration

The owner-authenticated `GET /policy/config` endpoint returns the active demo agent ID, enabled state, allowed chain IDs, configured trusted tokens, recipient allowlists, transaction and daily limits, recipient-specific limits, and approval settings. Spending limits are returned in token base units; for the demo USDC token, 1 USDC equals 1,000,000 base units. This endpoint is read-only and does not expose bearer tokens, private keys, or mutation controls. The policy hash remains available separately through `GET /policy/hash`.
