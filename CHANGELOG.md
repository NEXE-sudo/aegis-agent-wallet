# Changelog

## 1.8.0

- Recompute the canonical fingerprint of the persisted proposal before approval and simulated execution.
- Fail closed with HTTP 409 and append an audit event if persisted proposal details no longer match the approval-bound fingerprint.
- Add regression tests for proposal tampering between creation and approval or execution.


## 1.7.0

- Aggregate global and recipient daily reservations with Python integers to avoid SQLite `SUM(INTEGER)` overflow when individually valid amounts total beyond the signed 64-bit range.
- Add a regression test proving oversized totals are hard-blocked by policy rather than causing a persistence error.


## 1.6.0

- Reject API amounts, spend values, and chain IDs outside supported positive SQLite integer bounds before persistence.
- Validate global and recipient-specific policy limits against SQLite's signed 64-bit integer range to avoid overflow failures.


## 1.5.0

- Validate global policy spending limits, approval thresholds, chain IDs, agent identifiers, and token symbols at construction time.
- Validate trusted token chain IDs, symbols, and decimals.
- Add regression tests for invalid configurations while preserving zero as a valid approval threshold.


## 1.4.0

- Add optional per-recipient single-transaction amount caps alongside the global transaction limit.
- Hard-block transactions exceeding a recipient cap and reject negative cap configuration.
- Normalize recipient addresses for case-insensitive cap lookup and add regression tests.


## 1.3.0

- Add configurable per-recipient daily spend caps, evaluated per chain and token contract.
- Calculate recipient spend from persisted same-day reservations during proposal creation and pre-execution revalidation.
- Hard-block transactions that exceed a recipient cap and reject negative cap configuration.
- Add regression coverage for proposal-time and execution-time recipient-limit enforcement.


## 1.2.0

- Add recipient-specific token allowlists; disallowed token/recipient combinations are hard-blocked before approval.
- Normalize recipient and token addresses for case-insensitive matching.
- Preserve the existing approval requirement for recipients without an explicit token mapping.
- Add regression coverage for allowed, blocked, normalized, and unmapped recipient behavior.


## 1.1.0

- Timestamp owner approvals and require fresh approval after a configurable expiry (default 300 seconds).
- Invalidate expired or timestamp-less legacy approvals before simulated execution and record expiry in the audit trail.
- Expose the active approval timestamp and add regression tests for expiry, re-approval, and configuration validation.


## 1.0.0

- Add a persistent append-only SQLite audit trail for proposal creation, idempotency replays/conflicts, approvals, execution-time policy blocks, risk escalation, and simulated execution.
- Record actor roles, state transitions, fingerprints, timestamps, and structured event details atomically with workflow state changes.
- Prevent audit event updates and deletes with SQLite triggers; expose audit history through an owner-authenticated endpoint.
- Add workflow audit and state-transition regression coverage while keeping execution simulation-only.


## 0.9.0

- Recalculate risk during the atomic pre-execution check using current persisted daily spend.
- Move ready transactions to human approval if their risk escalates to high; invalidate stale approvals when risk materially changes to high.
- Add workflow regression coverage for execution-time risk escalation.

## 0.8.0

- Reject malformed EVM recipient addresses as hard policy violations before approval or simulated execution.
- Add policy and workflow regression tests for malformed recipient addresses.

## 0.7.0

- Bind allow-listed token addresses to trusted chain ID, symbol, and decimals in policy configuration.
- Block proposals whose caller-supplied token metadata does not match trusted configuration.
- Add policy and API regression tests for metadata tampering.

## 0.6.0

- Configure the demo policy with Circle's published Base Sepolia USDC contract and six decimals.
- Reuse one canonical address constant across policy configuration and tests.
- Keep recipient addresses as placeholders and execution simulation-only.

## 0.5.0

- Add authenticated, read-only Base Sepolia chain ID and ERC-20 metadata inspection.
- Verify the RPC chain ID before reading token metadata; reject wrong-chain endpoints.
- Add tests for RPC failures, invalid addresses, missing contract code, and token metadata reads.
- Keep wallet signing and transaction submission disabled.


## 0.4.0

- Calculate daily spending from persisted transactions instead of trusting the proposal caller.
- Reserve pending transaction amounts and serialize budget checks with SQLite write locks.
- Re-evaluate policy and spending atomically immediately before simulated execution.
- Require separate bearer tokens for agent proposal creation and owner retrieval/approval/execution; protected routes fail closed when secrets are missing.
- Add idempotency keys for proposal retries and reject reuse with a different payload.
- Add additive SQLite migration for new accounting and idempotency columns.
- Isolate API tests in temporary databases and cover auth, daily limits, idempotency, and execution-time revalidation.
- Keep execution simulation-only; no wallet keys, signing, or blockchain RPC calls are added.
