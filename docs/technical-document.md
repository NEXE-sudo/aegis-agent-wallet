# Aegis Agent Wallet — Technical Document (Draft)

**Document status:** Draft for review. This describes the prototype as implemented;
it is not a production deployment specification or a security certification.

## 1. Purpose and scope

Aegis is a local-first, testnet-focused wallet-agent prototype. It accepts payment
proposals from an agent, evaluates them against deterministic policy and heuristic
risk rules, records workflow state in SQLite, and exposes owner-authenticated
approval and execution operations. Execution is simulated by default. The only mode that can sign and submit an
actual ERC-20 transfer is `base_sepolia_vault`, which sends proposal-bound calls
from a separate agent session key to an unaudited, testnet-only AegisVault on Base
Sepolia (chain ID `84532`). Direct EOA token transfers are disabled. The vault
independently enforces on-chain token caps, recipient/approval rules, and pause state.

The agent can propose transactions, retrieve market quotes, and read a limited
transaction status. It cannot approve or reject a transaction. It can request execution only for eligible proposals through the vault-only agent endpoint; owner approval requirements remain enforced.
No mainnet execution, exchange or broker order routing, or production key custody is
provided.

## 2. Architecture

```mermaid
flowchart LR
    Human[Owner / browser] -->|owner bearer token| API[FastAPI application]
    Agent[Agent package] -->|agent bearer token| API
    Agent <-->|task and tool results only| LLM[Optional OpenAI-compatible provider]
    API --> Auth[Separate auth dependencies]
    Auth --> Controller[Transaction controller]
    Controller --> Policy[Deterministic policy engine]
    Controller --> Risk[Heuristic risk engine]
    Controller --> Store[Workflow store]
    Store --> DB[(SQLite)]
    Controller --> Executor[Base Sepolia / vault executor]
    Executor --> RPC[Base Sepolia JSON-RPC]
    Executor -->|vault mode only| Vault[AegisVault enforcement boundary]
    Vault --> Token[Allowed ERC-20 token]
    API --> Market[Market-data providers]
```

### Components

- **`app/main.py`** defines the FastAPI routes, request/response schemas, demo policy,
  request logging, and dependency wiring. It serves the dependency-free frontend at
  `/`.
- **`app/auth.py`** provides separate agent and owner bearer-token dependencies.
  Protected workflow routes fail closed when required token configuration is
  missing or the configured tokens are identical. Token comparisons use
  `hmac.compare_digest`.
- **`app/policy/`** evaluates allowed chain, token, trusted token metadata, recipient,
  transaction amount, daily spend, and recipient-specific limits. Hard blocks
  cannot be overridden by owner approval. It also serializes the complete active
  policy in canonical `aegis-policy-v1` JSON and exposes its SHA-256 digest to
  owners for future contract-limit comparisons.
- **`app/risk/`** calculates a deterministic, explainable heuristic score. It is
  not a fraud detector and is distinct from policy enforcement.
- **`app/workflow/controller.py`** coordinates proposal evaluation, approval, and
  execution.
- **`app/workflow/store.py`** persists proposals and audit events in SQLite. It
  performs spending reservations and state changes under database transactions.
  Audit rows have SQLite guards against ordinary update and delete operations;
  these are not cryptographic tamper protection against someone controlling the
  database file.
- **`app/chain/`** contains read-only Base Sepolia inspection and the vault executor.
  In vault mode, `eth-account` signs locally; the executor sends signed bytes to
  the configured RPC provider, not the private key. It requires the signer to be
  the configured agent and distinct from `owner()`, checks deployed code, validates
  exact calldata, and simulates the transfer before signing.
- **`contracts/src/AegisVault.sol`** holds testnet tokens and independently
  enforces the configured agent, token caps, UTC-day spending bucket, allowed
  recipients or exact expiring owner approvals, and pause state. It is not
  upgradeable, audited, or suitable for real funds.
- **`agent/`** provides the agent-token-only API client, a constrained tool loop for
  an optional OpenAI-compatible LLM, a deterministic offline fallback, and
  `python -m agent.eval`.
- **`frontend/index.html`** is a same-origin local control centre; it has no
  frontend package dependency.
- **Market-data providers** supply informational quote reads. Quotes are not
  transaction authorization or investment advice.

### Demo policy

The checked-in demo policy allows Base Sepolia USDC (six decimals), with a maximum
single transaction of `50,000,000` base units and a daily limit of
`150,000,000` base units. For this token, these correspond to 50 USDC and 150 USDC.
The approval threshold is also 50 USDC. The policy names two placeholder recipients;
an unknown recipient requires approval. The code and frontend are configured for a
demonstration and must not be mistaken for a user-specific wallet policy.

## 3. Principal request flows

### Agent proposal

1. The agent receives task data containing a structured `authorized_payment`.
   Free-form invoice text is untrusted context and is not the source of payment
   parameters.
2. The offline agent proposes that structured payment directly. In LLM mode, each
   proposal tool call must match the structured payment before it can reach the
   API.
3. `POST /transactions/propose` authenticates with the agent token. The workflow
   checks the persisted same-day spend, evaluates policy and risk, creates a
   proposal fingerprint, reserves budget when applicable, and records the result.
4. The response is `ready`, `awaiting_approval`, or `blocked`. A `ready` response is
   only a proposal; it is not an executed transfer.

The agent also has agent-token access to
`GET /agent/market-data/{asset_class}/{symbol}` and
`GET /agent/transactions/{transaction_id}`. The latter returns limited status,
execution reference, and update time, not the full proposal or audit history.

### Owner review and execution

1. The owner uses the owner token to read the proposal and its audit trail.
2. If approval is required, `POST /transactions/{transaction_id}/approve` requires
   the exact proposal fingerprint and explicit `APPROVE` confirmation. Approval
   expires after `AEGIS_APPROVAL_EXPIRES_SECONDS` (default 300 seconds).
3. `POST /transactions/{transaction_id}/execute` is owner-only. It revalidates
   proposal integrity, policy, spend, risk, and approval state immediately before
   execution. A hard policy block cannot be approved through.
4. In default `simulated` mode, the workflow records simulated execution without
   signing or sending a transaction.
5. In `base_sepolia_vault` mode, the executor validates the deployed vault, signer
   identity, exact proposal-bound calldata, and simulated call before signing.
   The contract re-enforces its policy when the call is submitted; an uncertain
   submission retries the same signed bytes rather than choosing a new nonce.
6. `GET /transactions/{transaction_id}/receipt` is owner-only and reports
   `pending`, `success`, or `failed` for a submitted testnet transaction. Submission
   or a successful receipt response is not proof of finality.

### On-chain vault flow (`base_sepolia_vault`)

Owner-authenticated execution remains available, and eligible proposals may also be
submitted through the agent-only endpoint. The server's `AEGIS_TESTNET_PRIVATE_KEY`
is the agent session key only and must not be the owner key. Before signing, the executor verifies Base Sepolia chain ID, deployed
vault code, `owner()`/`agent()` identity separation, and the complete
`transferToken(token, recipient, amount, ref)` call. `ref` is the existing
proposal fingerprint encoded as `bytes32`. The executor simulates that exact
call from the agent before obtaining signing approval from the local key; a
contract revert prevents signing and broadcast. On-chain execution then repeats
the authoritative caps and recipient/approval checks in the contract, regardless
of the server's workflow decision.

For transfers requiring an on-chain owner approval,
`GET /transactions/{transaction_id}/vault-approval` returns exact approval
arguments, `approveTransfer` calldata, a suggested expiry, and an owner-run
`cast send` command. This helper never reads an owner key or signs. The
owner-only `GET /chain/base-sepolia/vault/status` is read-only and reports owner,
agent, pause state, token policies, current UTC-day spend, recipient flags, and
each difference from the Aegis demo policy. An `in_sync` result is only a
point-in-time comparison against one RPC response, not proof of provider honesty.

The contract's daily cap uses `block.timestamp / 1 days`, an epoch-aligned UTC
day bucket rather than a rolling 24-hour window. The owner can pause the agent
path or emergency-withdraw even while paused. A compromised agent/server cannot
change the on-chain policy or withdraw; a compromised owner key can change
limits and roles, create approvals, unpause, or move vault assets. See
[on-chain enforcement and known risks](onchain-enforcement.md). The contract is
unaudited and testnet-only.

The owner can also reject or cancel a pending proposal. Either action releases its
daily reservation. Idempotency keys on proposals prevent an identical retry from
creating a second reservation and reject reuse with a different proposal.

### Other API surfaces

| API surface | Access | Purpose and boundary |
| --- | --- | --- |
| `GET /health` | No bearer token | Process status and selected execution mode; not a database or RPC readiness check. |
| `POST /policy/evaluate`, `POST /risk/assess` | No bearer token | Demo evaluation using caller-supplied spend; does not create workflow state and is not execution authorization. |
| `GET /policy/hash` | Owner token | SHA-256 digest of the active policy and canonical format version. |
| `GET /transactions`, `/transactions/{id}`, `/transactions/{id}/audit` | Owner token | Full transaction and audit reads. |
| `POST /transactions/{id}/approve`, `/reject`, `/cancel`, `/execute` | Owner token | Owner workflow actions; execution is subject to current mode and revalidation. |
| `POST /agent/transactions/{id}/execute` | Agent token | Executes only ready or approved proposals, only in `base_sepolia_vault` mode, through the same policy and on-chain checks as owner execution. |
| `GET /chain/base-sepolia/status`, `/chain/base-sepolia/token/{address}` | Owner token | Read-only chain inspection. |
| `GET /chain/base-sepolia/vault/status` | Owner token | Read-only AegisVault identity, pause, token policy/spend, recipient, and policy-sync inspection. |
| `GET /transactions/{id}/vault-approval` | Owner token | Prepares exact owner approval calldata and a CLI command; never signs or accesses a key. |
| `GET /market-data/{asset_class}/{symbol}` | Owner token | Validated market quote read. |

The unauthenticated evaluation endpoints expose a policy oracle. They are intended
for controlled local demonstration and should not be exposed to untrusted callers.

### Canonical policy hash (`aegis-policy-v1`)

The policy hash is SHA-256 over the UTF-8 bytes of a compact JSON object containing
every active `AgentPolicy` field, including trusted-token metadata, recipient
allowlists, recipient-specific limits, enablement, and approval expiry. Object keys
are sorted lexicographically and JSON uses no insignificant whitespace with ASCII
escaping. Address values and address-map keys are lower-cased; all sets and nested
allowlists are sorted before serialization. Every integer is serialized as a
base-10 string (no sign or leading zeroes); booleans remain JSON booleans and text
remains JSON strings. The object shape is the `AgentPolicy` field names with
trusted-token values shaped as `chain_id`, `decimals`, and `symbol`. The owner-only
`GET /policy/hash` returns `policy_hash` and `format_version` (`aegis-policy-v1`).
Proposal-creation and execution-related audit-event details also carry the active
hash. No policy-hash database column is added.

## 4. Safety model and trust boundaries

- **Separation of authority:** the agent token is limited to proposals and limited
  reads. Owner-only operations use a distinct token. The agent does not load or
  disclose the owner token.
- **Human control:** the agent does not approve or execute. Owner-authenticated
  execution is always required, including for a proposal in `ready` state; explicit
  approval is additionally required when policy or risk demands it.
- **Policy before action:** policy hard blocks, bounded amounts, trusted token
  metadata, recipient rules, daily reservations, and execution-time reevaluation
  constrain what may execute. Risk is heuristic and cannot override policy.
- **Approval integrity:** approval is tied to a proposal fingerprint and expires.
  Fingerprinting detects inconsistent stored proposal data but is not a signature
  and does not protect against an attacker who controls the application or database.
- **Accounting and audit:** SQLite serializes relevant workflow updates and tracks
  active reservations. State-changing transitions append audit events in the same
  transaction. Direct database-file access is outside the protection offered by
  the audit triggers.
- **Testnet boundary:** simulated execution is the default. Testnet execution must
  be explicitly selected, requires a throwaway key and HTTPS RPC URL, and rejects
  any chain other than Base Sepolia `84532`. The private key is loaded from the
  process environment and is not returned or logged. The host and persisted signed
  transaction bytes remain sensitive.
- **LLM boundary:** the optional LLM provider receives task content and tool
  results, but not either Aegis bearer token. A structured-payment match check
  prevents a tool call from changing those fields; it does not establish that the
  structured payment was independently verified or that all prompt injection is
  defeated.
- **External services:** market and RPC provider output is untrusted. Provider
  availability, token authenticity, recipient identity, and transaction finality
  are not guaranteed by this prototype.

For additional security detail and residual risks, see the
[threat model](threat-model.md), [workflow transitions](workflow-state-transitions.md),
[runtime configuration](runtime-configuration.md), and [agent guide](agent.md).

## 5. Local deployment and operation

This is a local development deployment procedure, not production hardening guidance.
Keep the API bound to loopback. Do not expose it publicly or use real-fund keys.

1. Install Python 3.11 or later and `uv`. From the repository root, install the
   locked dependencies:

   ```bash
   uv sync --locked --extra dev
   ```

2. Generate distinct local bearer tokens and select a database path:

   ```bash
   export AEGIS_AGENT_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
   export AEGIS_APPROVAL_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
   export AEGIS_DB_PATH="./aegis-workflow.sqlite3"
   export AEGIS_APPROVAL_EXPIRES_SECONDS="300"
   ```

   Keep the tokens out of source control, screenshots, and logs. Protect the SQLite
   file and backups. The default mode is `simulated`; do not configure a private key
   for normal local simulation.

3. Run the automated checks and deterministic offline agent evaluation:

   ```bash
   uv run --locked ruff check .
   uv run --locked pytest
   uv run --locked python -m agent.eval
   ```

   The evaluation uses temporary databases and does not require an API key, live
   RPC, market-data provider, or LLM service.

4. Start the application on loopback:

   ```bash
   uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

   Open `http://127.0.0.1:8000/` for the frontend or
   `http://127.0.0.1:8000/docs` for the API schema. `/health` reports the process
   status and selected execution mode only.

5. To run the agent locally, set `AEGIS_API_URL` (default
   `http://127.0.0.1:8000`) and `AEGIS_AGENT_TOKEN`. Leave all three LLM settings
   unset for the offline fallback. To use a compatible provider, configure
   `AEGIS_LLM_BASE_URL`, `AEGIS_LLM_MODEL`, and `AEGIS_LLM_API_KEY` together. The
   provider URL must use HTTPS except for loopback development; do not include
   credentials in the URL.

### Optional Base Sepolia manual test

Only use throwaway keys funded with Base Sepolia faucet assets. Follow the
[manual test procedure](runtime-configuration.md#manual-base-sepolia-test-transfer)
and deployment/owner runbook in [on-chain enforcement](onchain-enforcement.md).
Direct EOA execution is disabled. The contract is unaudited and testnet-only. No live deployment or transfer is claimed by this
document.

- **Evidence (pending owner-run test; not yet verified):**
  Transaction hash: TODO (supplied by the owner after a real transfer)
  Verify with: `uv run python scripts/verify_testnet_evidence.py "<TRANSACTION_HASH>" --expected-sender "<AGENT_OR_SENDER_ADDRESS>" --audit-db "<AEGIS_DB_PATH>"`
- **TODO:** Record the date, RPC provider (without credentials), and reviewer for
  that manual validation.

### AegisVault evidence (pending owner-run test; not yet verified)

- Vault address: `TODO: supply the real deployed AegisVault address`
  - Verify against the deployment receipt:
    `cast receipt "<DEPLOYMENT_TX_HASH>" --rpc-url "$BASE_SEPOLIA_RPC_URL"`
- Deployment transaction hash: `TODO: supply the real Base Sepolia deployment hash`
  - Verify:
    `cast receipt "<DEPLOYMENT_TX_HASH>" --rpc-url "$BASE_SEPOLIA_RPC_URL"`
- Executed `transferToken` transaction hash: `TODO: supply the real successful transfer hash`
  - Verify the vault call, sender, events, and audit reference:
    `uv run python scripts/verify_testnet_evidence.py "<TRANSFER_TX_HASH>" --vault "<VAULT_ADDRESS>" --expected-sender "<AGENT_ADDRESS>" --audit-db "<AEGIS_DB_PATH>"`
- Blocked bypass transaction hash: `TODO: supply the real reverted bypass hash`
  - Verify the call and failed receipt (`status` must be `0x0`):
    `cast tx "<BLOCKED_BYPASS_TX_HASH>" --rpc-url "$BASE_SEPOLIA_RPC_URL" && cast receipt "<BLOCKED_BYPASS_TX_HASH>" --rpc-url "$BASE_SEPOLIA_RPC_URL"`

Foundry results: **TODO: run forge test**. Python checks do not establish Solidity
test results. Do not replace any `TODO` with values until a real run produces
them or the owner supplies them.

## 6. Known limitations

- The authentication scheme is static development bearer tokens, not an identity
  provider, production authorization system, or token rotation service.
- The configured demo policy and placeholder recipients are not a wallet setup
  workflow for real users.
- The risk score is a heuristic, not validated fraud detection.
- Structured payment data is not independently verified by the agent.
- SQLite and its append-only triggers do not provide cryptographic audit integrity
  against database-file control.
- There is no production key custody, hardware signer, multi-party approval, chain
  reorganization handling, finality tracking, balance reconciliation, or automated
  recovery process.
- AegisVault is an unaudited testnet prototype. The agent is a single on-chain
  address; UTC-day caps reset at a bucket boundary and are not rolling limits.
  A compromised owner key can change policy, create approvals, unpause, rotate
  roles, or withdraw. Token-specific behavior, such as an issuer blacklist or
  upgrade, remains outside the vault's control.
- The project is not a broker, exchange, or investment-advice service.
