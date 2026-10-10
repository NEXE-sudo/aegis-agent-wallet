# Threat Model and Trust Boundaries

This document describes the intended security boundaries of Aegis Agent Wallet as implemented today. It is a threat-modeling aid for a local, testnet-focused prototype—not an independent security audit, certification, or guarantee that all attacks are prevented.

## 1. Security goals

The prototype aims to:

- Evaluate transaction proposals against deterministic policy rules and explainable heuristic risk signals.
- Prevent policy hard blocks from being overridden by ordinary approval.
- Bind approval to a specific proposal fingerprint and expire stale or invalid approvals.
- Revalidate policy and risk before simulated execution.
- Account for local daily spending reservations and handle idempotent retries consistently.
- Preserve a useful workflow audit trail while avoiding unnecessary secret-like values in event details.
- Keep blockchain interaction read-only and all transaction execution simulated.

## 2. Assets to protect

| Asset | Why it matters |
| --- | --- |
| Agent and owner bearer tokens | They gate proposal creation and owner operations respectively. |
| Proposal fields and canonical fingerprint | They define what is being evaluated, approved, and simulated. |
| Approval status, fingerprint, and timestamp | They determine whether the workflow may proceed. |
| Policy configuration and spending reservations | They constrain recipients, tokens, amounts, and daily exposure. |
| Transaction records and audit events | They support replay handling, review, and incident investigation. |
| Database file and environment configuration | They contain persisted workflow state and runtime secrets/settings. |
| RPC endpoint URL and returned data | The URL may contain provider credentials; responses influence read-only chain inspection. |

## 3. Trust boundaries

### API callers

- `POST /transactions/propose` requires the agent bearer token.
- Transaction retrieval, approval, simulated execution, audit retrieval, and Base Sepolia inspection require the owner/approval bearer token.
- `/health`, `/policy/evaluate`, and `/risk/assess` are demo endpoints without bearer-token dependencies. Evaluation endpoints accept caller-supplied spend for isolated evaluation only; that value is not the workflow's persisted reservation ledger.
- Request validation and policy checks reduce invalid input, but authentication is a small development bearer-token scheme, not a production identity, role-management, or session system.

### Application and SQLite database

The application process is trusted to enforce policy, update workflow state, and append audit events. SQLite transactions and write locking protect application-level state changes and spending checks. Database triggers reject ordinary audit-row updates/deletes, but someone who can directly modify or replace the database file is outside that protection boundary.

Proposal fingerprints detect inconsistency between a stored proposal and its recorded fingerprint when verification runs. A plain SHA-256 fingerprint is not a digital signature and does not protect against an attacker who can modify both the proposal and its fingerprint or control the application/database.

### External RPC provider

The Base Sepolia RPC endpoint is external and its responses must be treated as untrusted. The client checks response shape, RPC errors, chain ID, contract bytecode, and expected metadata formats. Readable token metadata and bytecode do not establish token authenticity, recipient identity, provider honesty, or finality. Provider URLs can contain credentials and must not be committed or exposed in responses/logs.

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
- A human operator understands that approval and simulated execution are application workflow states, not proof of an on-chain transaction.

## 6. Explicit non-goals

The current project does **not**:

- Load, store, or manage private keys or seed phrases.
- Sign, broadcast, or submit blockchain transactions.
- Prove recipient identity, token authenticity, or provider honesty.
- Provide a production identity system, hardware-backed key custody, multi-party approval, or production secret rotation.
- Provide cryptographic audit-log integrity against an attacker with database-file control.
- Reconcile balances, mempool state, transaction receipts, chain reorganizations, or finality.
- Claim that heuristic risk scoring is a validated fraud detector or that passing tests proves production security.

Any move toward real signing or transaction submission requires a separate scope decision and dedicated design/security review.

## 7. Review checklist for future changes

When adding or changing a feature, ask:

1. Which asset and trust boundary does it affect?
2. Can an untrusted caller influence policy, approval, spending reservations, or persisted state?
3. Does failure stop safely, return a sanitized response, and preserve consistent state?
4. Is approval still bound to the exact proposal and current policy/risk requirements?
5. Are secrets and caller-supplied opaque values absent from logs, errors, and audit details unless strictly needed?
6. Are security-relevant state changes tested, and are documented guarantees narrower than or equal to the actual implementation?
7. Does the change preserve the simulation-only and read-only-chain boundary?
