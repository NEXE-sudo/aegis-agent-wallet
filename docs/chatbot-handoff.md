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

PR #209 added the dedicated Foundry GitHub Actions workflow with Solidity unit, fuzz, and invariant tests. PR #210 added owner-authenticated read-only inspection of a Base Sepolia AegisVault's bytecode, owner/agent, pause state, token policy, and optional comparison with the locally configured vault address. PRs #203–#208 added portable JSON/Markdown evidence bundles, optional read-only transfer verification, audit cross-checking, provenance, confirmation-depth checks, and canonical receipt-block hash comparisons. These are point-in-time RPC observations, not finality guarantees.

PRs #205–#208 added evaluation metrics and failure diagnostics, source/runtime provenance, and canonical receipt-block hash comparisons. PRs #212–#218 added reverted-call evidence, offline bundle validation, live re-verification, vault configuration snapshots, deployment verification, and approval-expiry visibility. The Phase 5 close-out adds a cross-artifact consistency verifier and operator runbook. Offline scenarios do not prove actual transfers or general LLM safety.

Continue to favour coherent implementation-plus-tests-plus-docs PRs that improve the end-to-end story (policy decision, owner approval, vault-bounded execution, on-chain receipt, and matching audit record). Keep simulated mode as the default; evidence generation must not sign or broadcast transactions. Never claim finality, independent audit, LLM safety, or production readiness.

## Phased roadmap

### Phase 1 — Secure execution foundation (complete)
- Vault-only execution for eligible agent proposals.
- Direct EOA token-transfer mode retired.
- Owner approval requirements preserved and simulated mode remains the default.

### Phase 2 — Wallet read and configuration layer (complete)
- Owner-authenticated read-only Base Sepolia ETH/demo-USDC balances for a public address, active policy configuration and allowlists, UTC-day local workflow spending summaries, and read-only network/vault status comparison are implemented.
- The dependency-free frontend presents balances, policy settings, per-token spend/reserved amount, daily limits and remaining budget, RPC-reported chain ID, and on-chain vault policy differences.
- Activity exposes receipt details for the loaded page session and clearly labels receipt state as a point-in-time RPC observation, not finality.
- Regression coverage checks authentication, wrong-chain refusal before balance queries, sanitised provider failures, configuration mismatch states, and owner-only access. Local database accounting is labelled separately from on-chain state; secrets and private keys are never exposed by these read surfaces.

### Phase 3 — Wallet lifecycle and configuration verification (complete)
- Read-only vault inspection reports chain identity, bytecode presence, owner/agent separation, pause state, token/recipient policy and spending caps, and optional comparisons with configured vault/owner and the public address derived from the agent key.
- Missing expected identity values are reported as unverified rather than treated as matches; wrong-chain RPCs, absent bytecode, malformed ABI responses and structurally invalid configuration fail closed.
- Regression coverage exercises authentication, invalid addresses, wrong-chain RPCs, missing bytecode, malformed responses, policy differences, and configured identity comparisons. RPC observations remain point-in-time evidence, not independent contract authenticity or finality proofs.

### Phase 4 — Wallet dashboard and visibility (complete)
- The Overview and supporting views present workflow identity/status, network, public-address balances, active policy, spending and remaining limits, vault configuration comparison, and transaction receipt details using actual API responses.
- Browser credentials remain in memory only, asynchronous requests are guarded against stale sessions, and unavailable data is shown as unavailable rather than fabricated.
- Activity displays the backend-derived approval expiry timestamp for approved transactions and flags elapsed deadlines; the browser display is advisory, while the backend remains authoritative for approval validity.
- An end-to-end browser regression covers the wallet read/configuration surfaces together, including spending summary, network and vault status, balances, policy settings, and expired-approval visibility.

### Phase 5 — Contract and integration hardening (implementation complete; live evidence is operator-generated)

- GitHub Actions runs the Solidity unit, fuzz, and invariant suite; Python tests and Ruff remain separate required checks.
- `scripts/verify_vault_deployment.py` verifies an already-mined Base Sepolia deployment receipt, created vault address, constructor/current owner, canonical receipt block hash, observed confirmation depth, and runtime bytecode fingerprint without signing or broadcasting.
- `scripts/build_evidence_bundle.py` captures offline evaluation plus optional successful-transfer or expected-revert evidence, sender and confirmation checks, SQLite audit cross-checks for successful transfers, provenance, and a schema-v6 bundle digest.
- `scripts/verify_evidence_bundle.py` validates saved bundle integrity and internal consistency offline; `scripts/reverify_testnet_bundle.py` re-queries the configured RPC and compares stable transaction/receipt fields and the saved vault configuration snapshot.
- `scripts/verify_phase5_evidence.py` cross-checks a deployment-verification JSON artefact against a passing schema-v6 transaction bundle, rejecting mismatched chain, vault, owner, bytecode fingerprint, or transaction identities.
- The end-to-end procedure and residual-risk matrix are documented in `docs/phase-5-hardening.md`. Implementation scope is complete; a real deployment/transfer report still must be generated from actual Base Sepolia activity and cannot be inferred from CI or fixtures.
- Residual risks remain explicit: RPC observations are not independent consensus proofs, observed confirmations are not finality, bytecode hashes do not prove source reproducibility or safety, and evidence digests do not prove authorship. The vault is unaudited and testnet-only.

### Explicitly separate scope decisions
- Native ETH transfer support is separate from read-only ETH balance support.
- ERC-4337 account abstraction is a separate architectural decision.
- Do not enable mainnet execution or claim the unaudited vault is production-ready.

## Recently completed transaction-history and contract-hardening batch

- The receipt visibility and error-state work, history scope/limit validation, offset pagination, Activity Previous/Next controls, exact approval-expiry boundary, and fail-closed balance RPC regression tests were incorporated before PR #202.
- PRs #203–#218 are merged, covering portable evidence bundles, confirmation-depth checks, evaluation metrics/diagnostics, provenance, canonical receipt-block hash checks, reverted-call evidence, standalone bundle validation, current-state re-verification, vault configuration snapshots, deployment verification, and dashboard visibility. The current Phase 5 close-out links deployment and transfer artefacts and documents the operational workflow; it does not invent or substitute for real deployment evidence. Always query GitHub before assuming there are no open pull requests.

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
- PR #210 (`feat/onchain-vault-configuration-inspection`) is merged; the owner-authenticated read-only AegisVault status endpoint is in `main`, and the user confirmed post-merge Ruff/pytest checks and deleted the branch.
- PR #211 (`feat/approval-expiry-visibility`) is merged; it exposes the backend-derived absolute approval deadline and displays expired deadlines in Activity. The user confirmed post-merge Ruff/pytest checks and deleted the branch.
- PR #216 is merged and post-merge Ruff/pytest checks passed; the user confirmed deletion of `feat/deployment-aware-testnet-evidence`.
- PR #218 completed the dashboard visibility slice. The current Phase 5 close-out adds cross-artifact deployment-to-transfer evidence verification, regression coverage, and a single operational runbook; it does not generate fabricated live-chain evidence.
- The user must run both local Python checks on the PR branch before merge. Then verify the PR head SHA and all CI jobs (including Foundry when relevant), squash-merge, ask for post-merge checks on updated `main`, and only then provide branch deletion commands.
- Continue bundling the broadest coherent milestone scope practical, including implementation, regression/integration tests, docs and handoff updates; do not impose artificial PR size limits. Never update `main` directly.

## Standard accounting and safety semantics

- Daily workflow spend is keyed by UTC date, chain ID, and token address.
- Active `ready`, `awaiting_approval`, and `approved` proposals reserve budget; `submitting_onchain` remains reserved while an RPC outcome is uncertain.
- `executed_simulated` and `submitted_onchain` count for the UTC execution day recorded by the workflow store. A submitted transaction is not proof of mining or finality.
- `GET /spending/summary` is local workflow accounting, not an on-chain balance or vault spend counter.
- Protect the SQLite database and backups. The testnet vault is unaudited and must only use throwaway Base Sepolia wallets.


## Development workflow status

- Continue with the manual PR workflow documented above: one coherent, milestone-sized branch and PR at a time, with scope driven by completion rather than an arbitrary size limit; required checks before squash merge, user-confirmed local checks on the PR and updated `main`, and branch cleanup only after post-merge verification.
- The dedicated Foundry contract workflow is active alongside the existing Python workflow; do not treat Python tests as evidence that Solidity tests passed. Prefer phase-completion PRs that bundle implementation, tests, and related documentation.
