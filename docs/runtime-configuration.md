# Runtime Configuration and Safe Local Startup

This guide describes the environment variables read by the current simulation-only prototype. Keep the API on loopback for local development; the bearer tokens are development controls, not production identity.

## Configuration reference

| Variable | Required? | Default | Purpose and validation |
| --- | --- | --- | --- |
| `AEGIS_AGENT_TOKEN` | Yes for proposal creation | None | Bearer token for `POST /transactions/propose`. Must be non-empty. Protected workflow endpoints fail closed with HTTP 503 if required token configuration is missing. |
| `AEGIS_APPROVAL_TOKEN` | Yes for owner operations | None | Bearer token for transaction reads, approval, simulated execution, audit reads, and read-only chain inspection. Must be non-empty. For protected workflow endpoints, it must differ from the agent token. |
| `AEGIS_DB_PATH` | No | `aegis-workflow.sqlite3` | SQLite database path. An explicitly empty or whitespace-only value is rejected; choose a writable path and protect the database and its backups. Relative paths are resolved from the process working directory. |
| `AEGIS_APPROVAL_EXPIRES_SECONDS` | No | `300` | Approval lifetime in seconds. Must parse as an integer greater than zero; invalid values prevent the application from initializing correctly. Expired approvals must be granted again. |
| `BASE_SEPOLIA_RPC_URL` | Only for chain-inspection endpoints | None | HTTP(S) JSON-RPC endpoint used by the read-only Base Sepolia inspection routes. Prefer HTTPS. Missing configuration causes those endpoints to return HTTP 503; provider/request failures may return HTTP 502. Never commit a provider URL containing credentials. |

The application does not require an RPC URL to perform its simulation-only transaction workflow. Chain inspection is optional and read-only. It checks Base Sepolia chain ID and token metadata; it does not sign or submit transactions.

## Start locally

From the repository root on macOS or another Unix-like shell:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"

export AEGIS_AGENT_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AEGIS_APPROVAL_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AEGIS_DB_PATH="./aegis-workflow.sqlite3"
export AEGIS_APPROVAL_EXPIRES_SECONDS="300"

# Optional: set this only if you intend to use the read-only chain endpoints.
# export BASE_SEPOLIA_RPC_URL="https://YOUR_BASE_SEPOLIA_RPC_PROVIDER_URL"

python -m pytest -q
ruff check .
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Generate two distinct tokens. Do not paste real tokens or provider credentials into source files, issue reports, screenshots, or logs. The checked-in `.env.example` contains placeholders only; it is a reference, not a secret store. The commands above explicitly export the variables into the current shell.

Use `http://127.0.0.1:8000/docs` for the interactive API documentation. Keep the `--host 127.0.0.1` binding for local use. Do not change it to `0.0.0.0` or expose this development API to an untrusted network: the project does not provide production identity, TLS termination, rate limiting, or deployment hardening.

## Health endpoint: what it does and does not prove

`GET /health` returns the application status and the simulation-only mode. It is a lightweight process/API check, **not** a readiness check for SQLite or the external RPC provider. It does not prove that the database is writable, that chain inspection is available, or that a transaction occurred on-chain.

The chain inspection routes are separate:

- `GET /chain/base-sepolia/status` checks the configured RPC endpoint's reported chain ID.
- `GET /chain/base-sepolia/token/{address}` reads contract bytecode and ERC-20 metadata.

Both chain routes require the owner/approval bearer token and are read-only. A readable token response does not prove that a token is official or safe.

## Common configuration symptoms

- **HTTP 503 from a protected workflow endpoint:** check that both `AEGIS_AGENT_TOKEN` and `AEGIS_APPROVAL_TOKEN` are present, non-empty, and different.
- **HTTP 401 from a protected endpoint:** check the endpoint's expected bearer token and ensure the request uses `Authorization: Bearer <token>`.
- **Application initialization fails around approval expiry:** set `AEGIS_APPROVAL_EXPIRES_SECONDS` to a positive integer, for example `300`.
- **Database-path validation error:** ensure `AEGIS_DB_PATH` is non-empty and points to a location the process can access.
- **HTTP 503 from a chain endpoint:** configure `BASE_SEPOLIA_RPC_URL` and verify the endpoint is reachable.
- **HTTP 502 from a chain endpoint:** inspect provider availability and configuration without sharing the full credential-bearing URL. The client intentionally returns sanitized request errors.
- **Chain status reports a different chain ID:** verify that the configured RPC endpoint is for Base Sepolia (chain ID `84532`).

## Operational cautions

- SQLite state persists in the configured database file. Do not delete it as a casual troubleshooting step; it contains workflow and audit history.
- Back up the database only while following a consistent SQLite backup procedure, and protect backup files as sensitive application data.
- Audit triggers guard against ordinary row updates/deletes through the application database connection. They are not cryptographic tamper protection against someone who controls the database file.
- This is a local prototype: transaction execution remains simulated, and no private key is loaded, no transaction is signed, and no transaction is submitted.
