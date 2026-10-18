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
| `AEGIS_EXECUTION_MODE` | No | `simulated` | One of `simulated`, `base_sepolia`, or `base_sepolia_vault`. Any other value prevents startup. Testnet execution is never selected automatically. |
| `AEGIS_PREFLIGHT_ENABLED` | No | `true` in `base_sepolia` and `base_sepolia_vault`; `false` in `simulated` | In `base_sepolia`, controls read-only ERC-20 transfer simulation and sender token/ETH balance checks. In `base_sepolia_vault`, the vault transfer simulation is mandatory and cannot be disabled by this flag. Must be exactly `true` or `false` (case-insensitive); invalid values prevent startup. |
| `AEGIS_VAULT_ADDRESS` | For `base_sepolia_vault` mode and the owner-only vault helper/status routes | None | Validated EVM address of the deployed Base Sepolia AegisVault. Vault execution requires contract code and the configured key to be its agent, with a signer distinct from the vault owner. The read-only status and approval-helper routes use only this public address; they do not read a private key or sign. |
| `AEGIS_TESTNET_PRIVATE_KEY` | Only for `base_sepolia` or `base_sepolia_vault` mode | None | A throwaway Base Sepolia key, loaded only when testnet execution is explicitly enabled. In `base_sepolia_vault` mode this is the **agent session key only**, never the owner key. A missing or invalid key prevents startup. Keep it in the process environment; it is never accepted in an API request, returned, or logged. |
| `BASE_SEPOLIA_RPC_URL` | Only for chain inspection or either Base Sepolia execution mode | None | HTTP(S) JSON-RPC endpoint for read-only inspection. Testnet execution requires a valid HTTPS URL. Missing or invalid execution configuration prevents startup; RPC failures return sanitized HTTP errors. Never commit a provider URL containing credentials. |

The agent token authenticates proposal creation and the limited `GET /agent/transactions/{transaction_id}` and `GET /agent/market-data/{asset_class}/{symbol}` reads. The owner token remains required for full transaction records, approvals, rejection, execution, audit events, and owner market-data or chain-inspection operations. See the [agent guide](agent.md) for LLM configuration and the evaluation harness.

With the default `simulated` mode, the workflow does not load a private key or send transaction RPC calls. The optional chain inspection routes remain read-only. In `base_sepolia` mode, owner execution revalidates policy, spend, risk, and approvals, then signs and submits only allowlisted ERC-20 transfers on chain ID `84532`. In `base_sepolia_vault` mode, the server key is an agent session key only: the executor verifies the vault code, reads `owner()` and `agent()`, refuses owner/agent key overlap or an unconfigured signer, simulates the vault call, and only then signs `transferToken` on chain ID `84532`. Neither testnet mode has a silent fallback; an invalid chain ID is rejected.
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

By default, Base Sepolia execution runs a read-only `eth_call` for the proposed transfer from the configured sender, checks the sender's token balance, and checks that its ETH balance covers the gas limit multiplied by the capped gas price. A failed or malformed preflight returns HTTP 409 with a machine-readable code and does not sign, submit, change workflow status, or append an audit event. This refusal follows the existing no-op behavior for rejected workflow actions; audit events are reserved for committed state transitions and security-relevant persisted checks. Setting `AEGIS_PREFLIGHT_ENABLED=false` explicitly skips these checks; it does not change the policy, approval, or gas caps.

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

Use only a throwaway wallet and Base Sepolia faucet assets. The workflow's
configured token is Base Sepolia USDC. The demo's policy limits still apply, and
an unknown recipient or a proposal at the 50 USDC approval threshold requires
owner approval.

1. Create a throwaway EVM wallet using a wallet tool you trust. Keep its private
   key private; note only its public address. Fund it with Base Sepolia test ETH
   for gas and test USDC from a Base Sepolia faucet.
2. Set distinct `AEGIS_AGENT_TOKEN` and `AEGIS_APPROVAL_TOKEN` values in both
   local shells (API and client). In the API process environment, also set
   `AEGIS_EXECUTION_MODE="base_sepolia"`, an HTTPS `BASE_SEPOLIA_RPC_URL`, and
   `AEGIS_TESTNET_PRIVATE_KEY` from your local environment/secret manager. Do
   not paste the key into chat, source files, request bodies, shell history,
   screenshots, or logs. Do not enable shell tracing. Startup must fail if the
   key, RPC URL, or execution mode is invalid; it does not fall back to
   simulation.
3. Start the API on loopback using the documented `uvicorn` command. In another
   local shell, make the proposal as the agent. This example proposes 2.5 USDC
   to the demo allowlisted recipient; use a test amount and recipient covered by
   the active policy:

   ```bash
   API="http://127.0.0.1:8000"
   PROPOSAL_JSON=$(curl -fsS -X POST "$API/transactions/propose" \
     -H "Authorization: Bearer $AEGIS_AGENT_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"agent_id":"devops-01","chain_id":84532,"token_symbol":"USDC","token_address":"0x036cbd53842c5426634e7929541ec2318f3dcf7e","recipient":"0x2222222222222222222222222222222222222222","amount_base_units":2500000,"token_decimals":6}')
   TX_ID=$(printf '%s' "$PROPOSAL_JSON" | python -c 'import json,sys; print(json.load(sys.stdin)["transaction_id"])')
   FINGERPRINT=$(printf '%s' "$PROPOSAL_JSON" | python -c 'import json,sys; print(json.load(sys.stdin)["fingerprint"])')
   STATUS=$(printf '%s' "$PROPOSAL_JSON" | python -c 'import json,sys; print(json.load(sys.stdin)["status"])')
   ```

4. If the returned status is `awaiting_approval`, approve the exact proposal
   fingerprint as owner. Then execute as owner. This is safe to run for a
   `ready` proposal too; it simply skips the approval step:

   ```bash
   if [ "$STATUS" = "awaiting_approval" ]; then
     curl -fsS -X POST "$API/transactions/$TX_ID/approve" \
       -H "Authorization: Bearer $AEGIS_APPROVAL_TOKEN" \
       -H "Content-Type: application/json" \
       -d "{\"transaction_fingerprint\":\"$FINGERPRINT\",\"confirmation\":\"APPROVE\"}"
   fi
   EXECUTION_JSON=$(curl -fsS -X POST "$API/transactions/$TX_ID/execute" \
     -H "Authorization: Bearer $AEGIS_APPROVAL_TOKEN")
   printf '%s\n' "$EXECUTION_JSON"
   ```

   Confirm the response status is `submitted_onchain`. Read the transaction
   hash from `execution_reference` or the owner-only
   `onchain_transaction_submitted` audit event. A `ready` response is not an
   executed transfer.
5. Read the owner-only receipt and verify its returned hash/status. Poll if it
   reports `pending`; a successful RPC submission is not proof of finality:

   ```bash
   TX_HASH=$(printf '%s' "$EXECUTION_JSON" | python -c 'import json,sys; print(json.load(sys.stdin)["execution_reference"])')
   curl -fsS "$API/transactions/$TX_ID/receipt" \
     -H "Authorization: Bearer $AEGIS_APPROVAL_TOKEN"
   uv run python scripts/verify_testnet_evidence.py "$TX_HASH" \
     --expected-sender "<TODO: throwaway wallet address>" \
     --audit-db "<path to Aegis workflow SQLite database>"
   ```

   The verifier makes read-only RPC calls and confirms chain ID `84532`, a
   successful receipt, and standard ERC-20 `transfer(address,uint256)` calldata.
   It checks the transaction sender against the throwaway wallet's public
   address, then opens the Aegis database strictly read-only and verifies the
   matching submitted transaction and ordered audit events against the on-chain
   token contract, recipient, and amount. It reports these fields and a Base
   Sepolia explorer link. It exits unsuccessfully for a wrong chain, sender
   mismatch, missing transaction/receipt, non-transfer calldata, failed receipt,
   or inconsistent/missing audit record. To omit the sender check, explicitly use
   `--skip-sender-check`; the verifier prints a warning.

   For a vault-mode transfer, add `--vault "<AegisVault address>"`. The verifier
   then requires the transaction destination and `transferToken` calldata to
   match the vault, checks the vault `TokenTransferred` and ERC-20 `Transfer`
   receipt logs against the token, recipient, amount, and proposal reference,
   and, when `--audit-db` is supplied, compares that reference with the stored
   and independently recomputed proposal fingerprint. Keep
   `--expected-sender` set to the public agent address.
6. Stop the API and unset `AEGIS_TESTNET_PRIVATE_KEY` in its shell. Never use a
   mainnet key or endpoint. Tests use mocked RPC transports only and do not
   submit to a live network.

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
- Back up the database only while following a consistent SQLite backup procedure, and protect backup files as sensitive application data.
- Audit triggers guard against ordinary row updates/deletes through the application database connection. They are not cryptographic tamper protection against someone who controls the database file.
- This is a local prototype, not production key custody. The default simulation mode never loads a key. The optional testnet mode holds a throwaway private key in process memory and may keep already-signed transaction bytes in the SQLite database while safely retrying a submission. Protect the host and database; no mainnet execution is supported.
