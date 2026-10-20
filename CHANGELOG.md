# Changelog

## 1.12.0 (unreleased)

- Persist owner-managed mandates, verified payees, and structured payment tasks in SQLite with payload hashes and append-only hash-chained governance and decision records.
- Integrate a deterministic agent queue mode that evaluates owner-created tasks and creates proposals only through the existing wallet policy gateway; owner approval and AegisVault enforcement remain authoritative.
- Add governance APIs, audit verification, regression coverage, and cumulative treasury-buffer accounting across batches.


## 1.11.0 (unreleased)

- Add a deterministic mandate-bounded payment decision engine with registry-ID payee selection, duplicate invoice checks, budget and treasury-buffer controls, approval escalation, and possible split-payment structuring detection.
- Add focused unit tests and document the decision engine trust boundary; existing API integration and persisted owner-managed governance remain follow-up work.

## 1.10.3 (unreleased)

- Add an unaudited, testnet-only AegisVault with owner-managed token caps, exact transfer approvals, recipient allowlisting and pause controls, plus Foundry unit/fuzz/invariant tests; provide a Base Sepolia deployment script and owner runbook using Foundry keystore signing without repository-held keys.
- Add the `base_sepolia_vault` agent-session-key executor with vault identity checks, exact proposal-bound transfer calldata, mandatory read-only simulation before signing, owner-only approval calldata and read-only vault status/policy synchronization endpoints.
- Extend read-only transaction-evidence verification to check the expected sender, vault call, transfer logs and audit-stored proposal reference; add a throwaway-wallet runbook and simulation-first bypass demo with opt-in fixed-gas broadcasts for verified reverts. Real transfer evidence remains pending an owner-run transfer.
- Retire direct EOA ERC-20 execution so actual Base Sepolia transfers can be submitted only through the AegisVault; keep simulation as the default and expose vault-only execution mode in the frontend and configuration docs.
- Add opt-out pre-sign transfer simulation and sender token/ETH balance checks with safe HTTP 409 refusals; expose an owner-only canonical SHA-256 policy hash in proposal and execution audit details.
- Add an owner-only pause/resume kill switch and pending-proposal cap; while paused, new proposals are blocked and approve/execute requests fail with a clear 409 code, while reads, reject/cancel and receipts remain available.
- Add an agent-token-only proposal, market-quote and status tool set with a configurable OpenAI-compatible LLM and deterministic offline fallback; add five scripted offline evaluation scenarios with JSON/Markdown evidence in CI and an opt-in repeated LLM prompt-injection evaluation with per-scenario pass counts and separate reports.
- Add technical and security assessments, vault architecture, permission tiers, trust boundaries and known risks; keep testnet evidence as `TODO` placeholders with read-only verification commands and document the manual faucet test.
- Test Python 3.12 in CI in addition to Python 3.11 and 3.14.

## 1.10.2

- Add owner-authenticated proposal cancellation and rejection, releasing reserved daily budget and recording audit events.
- Correct same-day Stooq equity quote timestamps; clarify readable ERC-20 metadata is not token verification and cap metadata strings at 256 bytes.
- Explicitly close SQLite connections, consolidate approval-expiry checks, and remove redundant idempotency and normalization work.
- Add nonce-based Content Security Policy and standard browser security headers; document the policy-oracle exposure of unauthenticated evaluation endpoints.
- Test the declared Python minimum (3.11) and newer supported runtime (3.14) in CI.

## 1.10.1

- Version the newer implementation as a corrective patch without changing the existing published `v1.10.0` tag.
- Add a dependency-free local frontend control centre for proposals, approvals, simulation, workflow search, audit inspection and connection settings.
- Add read-only crypto and equity market-data quotes using fixed HTTPS providers, response validation, stale-data indicators and sanitized failures.
- Add locked dependency, test, lint and dependency-audit workflows; document the simulation-only security boundary and prototype limitations.

## 1.10.0

- Add deterministic policy evaluation, explainable heuristic risk scoring, persisted workflow state, idempotency protection, daily-spend reservations, and an append-only audit trail.
- Require fingerprint-bound owner approval with expiry; clear stale approval state on retry and return an explicit conflict requiring fresh approval, with regression coverage.
- Add read-only Base Sepolia contract/token inspection and read-only crypto/equity market quotes with fixed HTTPS providers, validation, stale-data indicators, and sanitized failures.
- Add a dependency-free local frontend control centre and a reproducible demo walkthrough.
- Add locked dependency, test, lint, and dependency-audit workflows with documented local verification and threat-model guidance.
- Keep this release simulation-only: it does not load private keys, sign transactions, or submit transactions; risk scoring is heuristic and bearer-token authentication is for local development.

## 1.9.0

- Verify the stored proposal fingerprint before returning an idempotent replay, and fail closed if persisted proposal details have changed.
- Audit replay integrity failures and add a regression test for tampering before retry.


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
