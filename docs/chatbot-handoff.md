# Aegis Agent Wallet — Chatbot Handoff

Last updated: 20 October 2026

## Repository and product

- Repository: https://github.com/NEXE-sudo/aegis-agent-wallet
- Product: testnet-only AI-agent wallet prototype for the FinTechathon 2026 Shenzhen International Track, theme “Agent Wallet on Chain”.
- The AI agent is a wallet user, not the product pitch.
- Use British English and do not address the user by name.
- Safety boundary: simulated execution by default; actual ERC-20 execution is only through the unaudited AegisVault on Base Sepolia (chain ID 84532). No mainnet or production-readiness claims.

## Current state

- PR #177 added vault-bounded agent execution while preserving owner approvals.
- PR #179 retired direct EOA token execution; actual Base Sepolia ERC-20 execution is routed through AegisVault.
- PR #180 added owner-authenticated read-only Base Sepolia ETH and demo USDC balance inspection.
- PR #181 added owner-authenticated read-only `GET /policy/config` for active policy settings and allowlists.
- PR #181 was squash-merged as `71b611700b0a3b113d9fa102789572c54c7ab77a`; the user confirmed checks passed on the PR and updated `main`, and the branch was deleted.
- PR #182 added owner-authenticated `GET /spending/summary` for UTC-day local workflow spend and remaining budget per allowed token; it is merged, the user confirmed checks passed on updated `main`, and the branch was deleted.
- PR #183 added the Overview dashboard's owner-authenticated daily spending summary with exact token-unit formatting, per-token limits and remaining budget, stale-session protection, and clear local-accounting semantics; it was squash-merged as `62bdf6d9b72b01d322929294bfcd4f33be346918`, all three CI jobs passed, and the user confirmed checks passed on updated `main` and deleted the branch.
- PR #184 exposed the owner-authenticated `GET /policy/config` response in Settings as a read-only view of active chain/token/recipient allowlists, spending limits, and approval rules; it was squash-merged as `f548deeb5506b0ce121aa059c3ab2c344154c14b`, CI passed on Python 3.11/3.12/3.14, the user confirmed checks on updated `main`, and the branch was deleted.
- PR #185 added an Overview panel for on-demand owner-authenticated, read-only Base Sepolia ETH and demo-USDC balances for a user-entered public address. It was squash-merged as `fbe22f13dd59a6572eccd07544aa3016a22b1cbc`; CI passed on Python 3.11/3.12/3.14, and the user confirmed both Ruff and pytest passed on updated `main`.
- PR #186 refreshed this handoff and was squash-merged as `fde988a7c0309d67e73b5492a607283b42cf6fe9`; CI passed on Python 3.11/3.12/3.14, and the user confirmed post-merge checks passed.
- PR #187 added the owner-authenticated, on-demand Overview network status panel using `GET /chain/base-sepolia/status`; it was squash-merged as `f220ee98fc75ee03910feee39b8c5f2220b7634c`, CI passed on Python 3.11/3.12/3.14, and the user confirmed post-merge checks passed and deleted the branch.

- PR #193 adds browser regression coverage showing receipt RPC failures as unavailable status rather than fabricating a receipt.
- PR #202 expanded deterministic agent evaluation from five to eleven scenarios, including chain/token metadata mismatches, unapproved recipients, and approval-threshold boundaries; it was squash-merged as `95240d1f9ff9e2573449d9b9e8db448736d813b0`, all three Python CI jobs passed, and the user confirmed post-merge Ruff and pytest checks and deleted the branch.

## Current implementation priority

PR #209 added a dedicated Foundry GitHub Actions workflow with pinned dependencies and automated Solidity unit, fuzz, and invariant tests. The current increment adds owner-authenticated read-only inspection of a Base Sepolia AegisVault's bytecode, owner/agent, pause state, token policy, and optional comparison with the locally configured vault address. PR #203 delivered portable JSON/Markdown evidence bundles, optional read-only Base Sepolia transfer verification, audit cross-checking, and a canonical-payload SHA-256 digest. PR #204 added receipt/latest-block heights, observed confirmation depth, and configurable minimum-confirmation checks. These are point-in-time RPC observations, not finality guarantees.

PR #205 delivered aggregate pass/failure counts and pass rates for deterministic and opt-in LLM evaluations. PR #206 added machine-readable failure reasons to individual deterministic and LLM runs and aggregates LLM failure types in reports. PR #207 added source revision and Python runtime provenance to the portable evidence bundle, upgraded its schema to v3, and included provenance in the bundle digest; it is merged and the user confirmed post-merge checks and deleted the branch. PR #208 is merged: evidence bundles compare the receipt block hash with the configured RPC's block hash at the receipt height, record both hashes, and use schema v4. The current increment adds read-only vault configuration inspection as part of Phase 3. Keep the narrow scope explicit: offline scenarios do not prove actual transfers or general LLM safety.

Continue to favour coherent implementation-plus-tests-plus-docs PRs that improve the end-to-end story (policy decision, owner approval, vault-bounded execution, on-chain receipt, and matching audit record). Keep simulated mode as the default; evidence generation must not sign or broadcast transactions. Never claim finality, independent audit, LLM safety, or production readiness.

## Phased roadmap

### Phase 1 — Secure execution foundation (complete)
- Vault-only execution for eligible agent proposals.
- Direct EOA token-transfer mode retired.
- Owner approval requirements preserved and simulated mode remains the default.

### Phase 2 — Wallet read and configuration layer
- Backend read surfaces include public-address Base Sepolia ETH/demo-USDC balances, active policy configuration, and UTC-day local workflow spending summaries.
- Frontend surfaces include read-only wallet balances, active policy settings/allowlists, daily spending/remaining budget, RPC-reported network status, and configured vault status comparison.
- Activity displays receipt details for the loaded page session; receipt checks are point-in-time observations, not proof of finality.
- Distinguish local configuration/database values from on-chain state, fail clearly on missing/mismatched configuration, and never expose secrets/private keys.

### Phase 3 — Wallet lifecycle and configuration verification
- Additional regression coverage checks that public-address balance reads refuse to query balances when the configured RPC reports a non-Base-Sepolia chain, and sanitise RPC read errors rather than exposing provider internals.
- The current increment adds read-only vault inspection for chain identity, bytecode presence, owner/agent separation, pause state, token allowlisting, spending caps, and optional comparisons against `AEGIS_VAULT_ADDRESS`, `AEGIS_VAULT_OWNER_ADDRESS`, and the public address derived from `AEGIS_TESTNET_PRIVATE_KEY`.
- Phase 3's read-only verification surface is covered by regression tests for authentication, invalid addresses, wrong-chain RPCs, missing bytecode, malformed ABI responses, structural misconfiguration, and local identity matches. Missing expected values are explicitly reported as unverified rather than treated as matches.
- Fail closed on wrong-chain RPCs and malformed configuration responses; inspection data from one RPC is not proof of contract authenticity or finality.

### Phase 4 — Wallet dashboard and visibility
- Present identity, network, balances, limits, spending, and transaction status in the existing dependency-free frontend.
- Use real API responses; never fabricate wallet data or market movement.

### Phase 5 — Contract and integration hardening
- Run Solidity unit, fuzz, and invariant tests automatically in GitHub Actions using the pinned compiler and dependency versions.
- Gather Foundry and testnet deployment/transfer/revert evidence, including transaction hashes where available.
- Document remaining security limitations and residual risks.

### Explicitly separate scope decisions
- Native ETH transfer support is separate from read-only ETH balance support.
- ERC-4337 account abstraction is a separate architectural decision.
- Do not enable mainnet execution or claim the unaudited vault is production-ready.

## Recently completed transaction-history and contract-hardening batch

- The receipt visibility and error-state work, history scope/limit validation, offset pagination, Activity Previous/Next controls, exact approval-expiry boundary, and fail-closed balance RPC regression tests were incorporated before PR #202.
- PRs #203–#208 are merged, including portable evidence bundles, confirmation-depth checks, evaluation metrics/diagnostics, provenance, and canonical receipt-block hash checks. The current branch adds automated Foundry contract testing. Always query GitHub before assuming there are no open pull requests.

## Required PR workflow

1. Create one focused branch from the current `main`, implement the change, add tests, update relevant documentation, and open a PR.
2. Use a plain-English PR title and a **single-sentence description** explaining the change; do not use prefixes such as `feat:` or `milestone:`.
3. The user pulls the PR branch and runs:
   ```bash
   uv run --locked ruff check .
   uv run --locked python -m pytest -q
   ```
4. When the user confirms both checks pass on the PR branch, verify that the PR is open, mergeable, and its head SHA is unchanged; then squash-merge it through GitHub without asking the user to merge it.
5. The user pulls updated `main` and reruns both checks:
   ```bash
   git switch main
   git pull --ff-only origin main
   uv run --locked ruff check .
   uv run --locked python -m pytest -q
   ```
6. Only after the user confirms both checks pass on updated `main` and GitHub confirms the merge, provide local and remote branch deletion together in one code block:
   ```bash
   git switch main
   git branch -d <branch-name>
   git push origin --delete <branch-name>
   ```
7. Keep every handoff or other documentation update inside a PR; never update `main` directly or create a separate documentation-only PR when the update belongs with the current feature.
8. Inspect GitHub state before each PR and before cleanup. Do not infer that tests passed or a merge occurred without confirmation.
9. Use British English, keep instructions compact, and do not address the user by name.

## Current status and next action

- PR #203 added portable JSON/Markdown evidence bundles, optional read-only Base Sepolia transfer verification, audit cross-checking, and a canonical-payload SHA-256 digest.
- PR #204 added receipt/latest-block heights and configurable minimum-confirmation checks; these remain point-in-time RPC observations, not finality guarantees.
- PR #205 added aggregate evaluation metrics. PR #206 added machine-readable failure reasons and aggregate LLM failure diagnostics. Both are merged; the user confirmed post-merge checks and deleted the branch.
- PR #207 (branch `feat/evidence-provenance`) is merged; it records source revision and Python runtime provenance, uses schema v3, and covers provenance in the canonical digest. The user confirmed post-merge checks and deleted the branch.
- PR #208 is merged and post-merge local checks passed. Matching receipt/canonical block hashes remain a point-in-time observation from one RPC, not proof of finality.
- PR #209 added automated Foundry contract checks and was merged; the user confirmed post-merge checks and deleted the branch.
- Current work is on `feat/onchain-vault-configuration-inspection`: add an owner-authenticated, read-only AegisVault status endpoint with regression tests and documentation. It must not sign or submit transactions, and it must not present one-RPC inspection as proof of authenticity.
- The user must run both local Python checks on the PR branch before merge. Then verify the PR head SHA and all CI jobs (including Foundry), squash-merge, ask for post-merge checks on updated `main`, and only then provide branch deletion commands.
- Continue bundling implementation, tests, and related docs in one coherent PR. Never update `main` directly.

## Standard accounting and safety semantics

- Daily workflow spend is keyed by UTC date, chain ID, and token address.
- Active `ready`, `awaiting_approval`, and `approved` proposals reserve budget; `submitting_onchain` remains reserved while an RPC outcome is uncertain.
- `executed_simulated` and `submitted_onchain` count for the UTC execution day recorded by the workflow store. A submitted transaction is not proof of mining or finality.
- `GET /spending/summary` is local workflow accounting, not an on-chain balance or vault spend counter.
- Protect the SQLite database and backups. The testnet vault is unaudited and must only use throwaway Base Sepolia wallets.


## Development workflow status

- Continue with the manual PR workflow documented above: one focused branch and PR at a time, required checks before squash merge, user-confirmed local checks on the PR and updated `main`, and branch cleanup only after post-merge verification.
- The dedicated Foundry contract workflow is active alongside the existing Python workflow; do not treat Python tests as evidence that Solidity tests passed. Prefer phase-completion PRs that bundle implementation, tests, and related documentation.
