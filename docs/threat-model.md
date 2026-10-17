# Threat Model and Trust Boundaries

This document describes the intended security boundaries of Aegis Agent Wallet as implemented today. It is a threat-modeling aid for a local, testnet-focused prototype—not an independent security audit, certification, or guarantee that all attacks are prevented.

## 1. Security goals

The prototype aims to:

- Evaluate transaction proposals against deterministic policy rules and explainable heuristic risk signals.
- Prevent policy hard blocks from being overridden by ordinary approval.
- Bind approval to a specific proposal fingerprint and expire stale or invalid approvals.
- Revalidate policy, daily spend, and risk before execution.
- Account for local daily spending reservations and handle idempotent retries consistently.
- Preserve a useful workflow audit trail while avoiding unnecessary secret-like values in event details.
- Support an owner-only pause/resume kill switch and a pending-proposal cap to stop or slow harmful workflows during intervention.
- Default to simulated execution; permit actual signing/submission only in an explicitly configured Base Sepolia testnet mode.

## 2. Assets to protect

| Asset | Why it matters |
| --- | --- |
| Agent and owner bearer tokens | They gate proposal creation and owner operations respectively. |
| Proposal fields and canonical fingerprint | They define what is being evaluated, approved, and simulated. |
| Approval status, fingerprint, and timestamp | They determine whether the workflow may proceed. |
| Policy configuration and spending reservations | They constrain recipients, tokens, amounts, and daily exposure. |
| Transaction records and audit events | They support replay handling, review, and incident investigation. |
| Database file and environment configuration | They contain persisted workflow state, a pending signed testnet transaction when needed for safe retries, and runtime secrets/settings. |
| RPC endpoint URL and returned data | The URL may contain provider credentials; responses influence chain inspection, gas estimates, nonce selection, submission, and receipt reporting. |
| Testnet private key and signed transaction | The key authorizes testnet transfers in the explicitly enabled mode; signed pending bytes are replayable and must be protected with the database. |

## 3. Trust boundaries

### API callers

- `POST /transactions/propose` requires the agent bearer token.
- `GET /agent/transactions/{transaction_id}` and `GET /agent/market-data/{asset_class}/{symbol}` also require the agent bearer token. The status response exposes only status, execution reference, and update time; it does not return proposal details or audit records.
- Transaction retrieval, approval, execution, receipt retrieval, audit retrieval, and Base Sepolia inspection require the owner/approval bearer token.
- `/health`, `/policy/evaluate`, and `/risk/assess` are demo endpoints without bearer-token dependencies. Evaluation endpoints accept caller-supplied spend for isolated evaluation only; that value is not the workflow's persisted reservation ledger. They expose a policy oracle: callers can vary inputs to infer configured limits and allowlists. Keep these endpoints reachable only on the local prototype; deployments exposed to untrusted callers should require owner authentication or otherwise restrict access.
- Request validation and policy checks reduce invalid input, but authentication is a small development bearer-token scheme, not a production identity, role-management, or session system.
- The optional OpenAI-compatible provider receives task content and tool results. The agent never sends either Aegis bearer token to that provider. Proposal tool calls must match structured `authorized_payment`; this does not independently verify that those structured fields are authentic or safe.

### Application and SQLite database

The application process is trusted to enforce policy, update workflow state, and append audit events. SQLite transactions and write locking protect application-level state changes and spending checks. Database triggers reject ordinary audit-row updates/deletes, but someone who can directly modify or replace the database file is outside that protection boundary.

Proposal fingerprints detect inconsistency between a stored proposal and its recorded fingerprint when verification runs. A plain SHA-256 fingerprint is not a digital signature and does not protect against an attacker who can modify both the proposal and its fingerprint or control the application/database.

### External RPC provider

The Base Sepolia RPC endpoint is external and its responses must be treated as untrusted. The client checks response shape, RPC errors, chain ID, contract bytecode, expected metadata formats, and receipt hashes/statuses. In testnet execution mode, the endpoint also supplies nonce and gas data and receives signed transaction bytes. The executor signs locally, restricts the chain to ID `84532`, checks the configured token allowlist, caps gas price/limit, and never sends the private key to the RPC provider. Readable token metadata and bytecode do not establish token authenticity, recipient identity, provider honesty, inclusion, or finality. Provider URLs can contain credentials and must not be committed or exposed in responses/logs.

### Human approver

The owner token identifies a caller with approval privileges; it does not establish a separate person's identity or prove that an informed human reviewed the proposal. A valid approval is bound to the proposal fingerprint and has a configurable expiry, but approval quality depends on the operator's review process and protection of the owner token.

## 4. Main threats and current mitigations

| Threat | Current mitigation | Residual risk |
| --- | --- | --- |
| Unauthorized proposal or owner operation | Separate agent/owner bearer tokens; blank or identical configured tokens fail closed; constant-time token comparison | Tokens are static environment secrets; no user identity, rotation workflow, rate limiting, or production-grade access management is provided. |
| Proposal tampering after creation | Persisted proposal fingerprint verification during approval, execution, and idempotent retries | This is consistency checking, not cryptographic protection against an attacker controlling the database or application. |
| Stale or mismatched approval | Approval fingerprint matching, expiry checks, fail-closed handling of missing/malformed/future timestamps, and fresh approval when required | A compromised owner token can approve proposals; clock and operator trust remain relevant. |
| Policy bypass or risky recipient/token/amount | Hard-block rules, trusted-token metadata checks, recipient/token allowlists, global and recipient caps, and policy/risk revalidation before simulation | Policy values are a demo configuration; address format and token metadata do not prove real-world identity or authenticity. Risk scoring is heuristic. |
| Duplicate or conflicting proposal retries | Idempotency key handling, SQLite write locking, proposal fingerprints, and conflict responses | Idempotency does not provide distributed coordination beyond this local SQLite-backed application. |
| Spending-accounting overflow or races | SQLite write transactions, bounded persisted integer values, and Python-integer aggregation of reservations | Reservations are local accounting, not on-chain balances, pending transfers, or confirmed receipts. |
| Duplicate transfer after uncertain RPC outcome | Persist the signed transaction and its hash before broadcast; retry the same signed bytes rather than selecting a new nonce; account for unresolved submissions conservatively | An RPC provider can be unavailable or dishonest. Pending signed bytes in SQLite can be rebroadcast by anyone with database access, and receipt reads do not establish finality or protect against chain reorganizations. |
| Unauthorized or misdirected testnet transfer | Execution is opt-in, owner-only, policy/spend/risk revalidated, fingerprint-bound, restricted to Base Sepolia and trusted tokens, locally signed, and protected by gas caps | The process environment and throwaway wallet remain sensitive; a compromised host or owner token can execute a policy-permitted test transfer. The prototype has no hardware-backed custody or independent transaction simulation. |
| Prompt injection or altered payment details | The agent treats invoice text as untrusted and permits a proposal tool call only when its fields match the task's structured `authorized_payment`; the agent token cannot approve or execute | This guard does not prove the structured payment was verified correctly. An LLM may still produce misleading explanations, and test scenarios do not establish general resistance to prompt injection. |
| Audit data altered or sensitive values exposed | Audit rows are protected against ordinary update/delete operations; idempotency keys are not copied into replay event details; selected RPC failures use sanitized errors | Audit is not cryptographically tamper-evident against database-file control; not every rejected/no-op request creates an audit event. |
| Malformed or misleading RPC responses | Response-shape/error checks, chain-ID checks, and sanitized request failures | The provider may be unavailable or dishonest; no independent RPC quorum or on-chain verification is implemented. |
| Secrets exposed through source control or diagnostics | Configuration is supplied through environment variables; RPC errors avoid returning provider URLs | Operators must still protect environment variables, shell history, process access, logs, backups, and the host. |

## 5. Deployment assumptions

The current prototype assumes:

- It is run locally or in a controlled development environment with a trusted host and filesystem.
- Environment variables and bearer tokens are kept private and are not committed to source control.
- If the API is exposed beyond loopback or a trusted private network, transport security and network access controls are provided externally. Bearer tokens must not be sent over untrusted plaintext connections.
- The operator can access and protect the SQLite database and any backups.
- Unit tests use deterministic/mocked RPC responses rather than depending on a live provider.
- A human operator understands that `submitted_onchain` means the RPC accepted the signed testnet transaction, not that it was mined successfully or reached finality.

## 6. Explicit non-goals

The project does **not**:

- Support mainnet execution, real-fund transfers, seed phrases, production key custody, or hardware-backed signing. A throwaway private key may be loaded only when `AEGIS_EXECUTION_MODE=base_sepolia` is explicitly selected.
- Treat an RPC submission response or receipt as proof of finality; automated confirmation/reorganization handling is not implemented.
- Prove recipient identity, token authenticity, or provider honesty.
- Provide a production identity system, hardware-backed key custody, multi-party approval, or production secret rotation.
- Provide cryptographic audit-log integrity against an attacker with database-file control.
- Reconcile balances, mempool state, transaction receipts, chain reorganizations, or finality.
- Claim that heuristic risk scoring is a validated fraud detector or that passing tests proves production security.

Any move beyond the explicitly gated Base Sepolia testnet mode—including mainnet support or production key custody—requires a separate scope decision and dedicated design/security review.

## 7. Review checklist for future changes

When adding or changing a feature, ask:

1. Which asset and trust boundary does it affect?
2. Can an untrusted caller influence policy, approval, spending reservations, or persisted state?
3. Does failure stop safely, return a sanitized response, and preserve consistent state?
4. Is approval still bound to the exact proposal and current policy/risk requirements?
5. Are secrets and caller-supplied opaque values absent from logs, errors, and audit details unless strictly needed?
6. Are security-relevant state changes tested, and are documented guarantees narrower than or equal to the actual implementation?
7. Does the default remain simulated, and is any signing/submission explicitly restricted to Base Sepolia (84532), owner-authorized, policy-revalidated, and tested without live network access?
