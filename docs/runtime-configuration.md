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
| `AEGIS_EXECUTION_MODE` | No | `simulated` | Either `simulated` or `base_sepolia`. Any other value prevents startup. Base Sepolia execution is never selected automatically. |
| `AEGIS_TESTNET_PRIVATE_KEY` | Only for `base_sepolia` mode | None | A throwaway Base Sepolia account's private key, loaded only when testnet execution is explicitly enabled. A missing or invalid key prevents startup. Keep it in the process environment; it is never accepted in an API request, returned, or logged. |
| `BASE_SEPOLIA_RPC_URL` | Only for chain inspection or `base_sepolia` mode | None | HTTP(S) JSON-RPC endpoint for read-only inspection. Testnet execution requires a valid HTTPS URL. Missing or invalid execution configuration prevents startup; RPC failures return sanitized HTTP errors. Never commit a provider URL containing credentials. |

The agent token authenticates proposal creation and the limited `GET /agent/transactions/{transaction_id}` and `GET /agent/market-data/{asset_class}/{symbol}` reads. The owner token remains required for full transaction records, approvals, rejection, execution, audit events, and owner market-data or chain-inspection operations. See the [agent guide](agent.md) for LLM configuration and the evaluation harness.

With the default `simulated` mode, the workflow does not load a private key or send transaction RPC calls. The optional chain inspection routes remain read-only. In `base_sepolia` mode, owner execution revalidates policy, spend, risk, and approvals, then signs and submits only allowlisted ERC-20 transfers on chain ID `84532`. A different chain ID is rejected.

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

Use this only with a throwaway wallet funded by Base Sepolia faucets. The workflow's configured token is Base Sepolia USDC; fund the wallet with test USDC for the transfer and test ETH for gas. The demo's policy limits still apply, and a recipient outside the configured allowlist requires owner approval.

1. Configure distinct API agent and owner tokens as described above. Set the HTTPS RPC endpoint and select testnet mode:

   ```bash
   export AEGIS_EXECUTION_MODE="base_sepolia"
   export BASE_SEPOLIA_RPC_URL="https://YOUR_BASE_SEPOLIA_RPC_PROVIDER_URL"
   read -r -s -p "Throwaway Base Sepolia private key: " AEGIS_TESTNET_PRIVATE_KEY
   printf '\n'
   export AEGIS_TESTNET_PRIVATE_KEY
   ```

2. Start the API on loopback using the documented `uvicorn` command. Startup must fail if the key, RPC URL, or execution mode is invalid; it does not fall back to simulation.
3. In the frontend, create a small USDC proposal to a recipient you control, review it, and approve it with the owner token if required. Confirm the API reports `base_sepolia` before executing.
4. Execute as owner. Confirm the response status is `submitted_onchain`, then read the hash from `execution_reference` or the owner-only audit event `onchain_transaction_submitted`.
5. Read the receipt with `GET /transactions/{transaction_id}/receipt` using the owner token. It reports `pending`, `success`, or `failed`; poll while pending. A successful RPC submission is not proof of confirmation or finality.
6. Stop the API and remove the key from the shell with `unset AEGIS_TESTNET_PRIVATE_KEY`. Never put the key in source, a request body, shell history, screenshots, or logs.

Tests use mocked RPC transports only and never submit to a live network. Do not use a mainnet key or mainnet RPC endpoint.

## Workflow cancellation

`POST /transactions/{transaction_id}/cancel` requires the owner token. It cancels a `ready`, `awaiting_approval`, or `approved` proposal, clears any approval, records an audit event, and releases the daily spend reservation. Blocked, already-cancelled, and simulated-executed transactions cannot be cancelled.

`POST /transactions/{transaction_id}/reject` also requires the owner token and releases the reservation, recording an audit event. A submitted testnet transaction cannot be cancelled or rejected by this workflow.

## Common configuration symptoms

- **HTTP 503 from a protected workflow endpoint:** check that both `AEGIS_AGENT_TOKEN` and `AEGIS_APPROVAL_TOKEN` are present, non-empty, and different.
- **HTTP 401 from a protected endpoint:** check the endpoint's expected bearer token and ensure the request uses `Authorization: Bearer <token>`.
- **Application initialization fails around approval expiry:** set `AEGIS_APPROVAL_EXPIRES_SECONDS` to a positive integer, for example `300`.
- **Database-path validation error:** ensure `AEGIS_DB_PATH` is non-empty and points to a location the process can access.
- **HTTP 503 from a chain endpoint:** configure `BASE_SEPOLIA_RPC_URL` and verify the endpoint is reachable.
- **HTTP 502 from a chain endpoint:** inspect provider availability and configuration without sharing the full credential-bearing URL. The client intentionally returns sanitized request errors.
- **Chain status reports a different chain ID:** verify that the configured RPC endpoint is for Base Sepolia (chain ID `84532`).
- **Execution configuration prevents startup:** set `AEGIS_EXECUTION_MODE` to `simulated` or configure a valid throwaway testnet key and HTTPS RPC URL for `base_sepolia`.

## Operational cautions

- SQLite state persists in the configured database file. Do not delete it as a casual troubleshooting step; it contains workflow and audit history.
- For concrete backup, restore, integrity-check, and reversible reset steps, see the [SQLite backup and restore guide](sqlite-backup-restore.md).
- Back up the database only while following a consistent SQLite backup procedure, and protect backup files as sensitive application data.
- Audit triggers guard against ordinary row updates/deletes through the application database connection. They are not cryptographic tamper protection against someone who controls the database file.
- This is a local prototype, not production key custody. The default simulation mode never loads a key. The optional testnet mode holds a throwaway private key in process memory and may keep already-signed transaction bytes in the SQLite database while safely retrying a submission. Protect the host and database; no mainnet execution is supported.
