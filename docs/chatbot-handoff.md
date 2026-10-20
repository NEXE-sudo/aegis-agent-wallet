# Aegis Agent Wallet — Chatbot Handoff

Last updated: 20 October 2026

## Current work in progress

- PR #228 — [Prepare FinTechathon submission close-out materials](https://github.com/NEXE-sudo/aegis-agent-wallet/pull/228) — squash-merged as `bb3ed0583690496afdae383d89306f2521a98dfa`; the user confirmed post-merge checks passed and deleted the feature branch.
- PR #227 — [Prepare reproducible submission evidence](https://github.com/NEXE-sudo/aegis-agent-wallet/pull/227) — squash-merged as `ce5351d254ae1934055c2f4e3adec502fcd7ed60`; the user confirmed post-merge lint, tests, evaluation, repeated evaluation, and evidence-bundle verification passed, then deleted the feature branch.
- **Current PR in preparation** — branch `feat/submission-preflight-verifier`. It adds an offline submission preflight that validates manifest membership and hashes, current source revision, deterministic/repeated evaluation structure and pass results, and evidence-bundle integrity; live Base Sepolia evidence remains separately optional and explicitly reported.
- PR #226 — [Reconcile governance budget with workflow outcomes](https://github.com/NEXE-sudo/aegis-agent-wallet/pull/226) — squash-merged as `3f4260e7b099425b3672117b608ed674fbe9c01f`; post-merge checks passed and branch deleted.
- PR #225 — [Expand adversarial evaluation and governance evidence](https://github.com/NEXE-sudo/aegis-agent-wallet/pull/225) — squash-merged as `1e0b5f0b90020ffb2fb0d06c141275bef9e925fa`; user reported 110/110 scenario observations passed across five repeated runs, post-merge checks passed, and branch deleted.
- PR #224 added repeatable evaluation and a demo guide; PR #223 added governance controls and operational hardening; PR #222 added expiring agent sessions and recipient caps; PR #221 persisted governance and integrated deterministic queue decisions.
- Keep the number of PRs low and feature scope substantial, comparable in breadth to PR #221. Do not claim local checks passed unless the user reports the result.

## Live project tracker

Completion estimates are planning estimates, not CI results, audits, or production-readiness claims. Do not mark unmerged branch work as completed.

### Original product phases

| Phase | Status | Remaining boundary |
| --- | --- | --- |
| Phase 1 — Secure execution foundation | Complete | Simulated execution by default; eligible actual transfers only through the unaudited AegisVault on Base Sepolia. |
| Phase 2 — Wallet read/configuration | Complete | Read-only policy, spending, balances, network and vault inspection. |
| Phase 3 — Lifecycle/configuration verification | Complete | Persisted proposal integrity, approval expiry and vault/policy comparisons. |
| Phase 4 — Dashboard and visibility | Core complete | Overview, proposals, approvals, activity/audit, settings and read-only chain panels exist; governance UI is merged. |
| Phase 5 — Contract/integration hardening | Implementation complete; live evidence pending | Foundry tests and evidence verification exist. Actual deployment/transfer evidence must come from operator-run Base Sepolia activity. |

### Workstreams

| Workstream | Merged baseline | Remaining milestones |
| --- | --- | --- |
| WS1 — Agent autonomy | Deterministic mandate-bounded decisions and persisted task queue with policy-gateway proposals. | The expanded evaluation now covers 18 direct policy/task cases, one reservation-lifecycle case, three persisted-governance cases, and ten opt-in LLM injection cases; repeated-run aggregation rejects incomplete or inconsistent scenario inventories. Still outstanding: LLM reasoning over persisted tasks, stronger adversarial diversity, provider-run evidence, and explicit source provenance. |
| WS2 — Persistent governance | Owner-managed mandate/payee/task persistence, payload hashes, hash-linked audit records, local budget lifecycle reconciliation, and export verification. | Remaining work includes role separation/token rotation and chain-observed settlement accounting. |
| WS3 — AegisVault v2 | Owner controls, exact approvals, global token caps, bounded agent sessions, optional per-recipient caps, Foundry unit/fuzz/invariant tests. | ABI/calldata/configuration drift checks and operator-run deployment/transfer evidence; receipt status now includes point-in-time confirmation and canonical-block checks but is not finality proof. |
| WS4 — Operations | FastAPI/SQLite, separate bearer-token roles, request metadata logging and CI; consistent SQLite backup/restore and governance-export verification are merged in PR #230. | Remaining: signer adapter boundaries, token rotation, packaging, and deployment runbook as feasible. |
| WS5 — Frontend | Dependency-free local control centre and Playwright coverage for core workflow. | PR #223 added governance, persisted decision review and exports; verify browser accessibility, error states and responsive behaviour in tests. |
| WS6 — Demo/submission | Demo scenario, evidence bundles, deployment verification and contract-hardening runbook. | PR #227 added artifact-manifest build/verification; the submission close-out kit now consolidates the architecture, security summary, presentation/video scripts, business case and evidence checklist. Fresh operator-generated reports and genuine Base Sepolia evidence remain necessary if live execution is claimed. |

## Next priorities

1. **Receipt confirmation and reorganisation visibility:** current branch `feat/receipt-confirmation-reconciliation` adds owner-only confirmation counts and receipt-block canonicality checks, without claiming consensus finality or automatically changing workflow accounting.
2. **Fresh reproducible reports:** run deterministic evaluation, repeated evaluation, evidence-bundle verification, manifest verification, and the final preflight on the final intended revision; an offline pass is not proof of live-chain execution.
3. **Live testnet evidence:** separately verify operator-run Base Sepolia deployment, successful transfer, and reverted-call evidence where available; never substitute fixtures or simulated results.
4. **Remaining engineering gaps:** role separation/token rotation, signer adapter boundaries, and chain-observed settlement accounting remain future work; do not overstate the current implementation.
5. Keep the number of PRs low and the feature scope substantial. Combine implementation, tests, docs, changelog, and handoff changes when they belong together.

## PR workflow — mandatory

For each feature PR:
1. Work on a feature branch; never push directly to `main`.
2. Provide the user with commands to fetch/switch to the branch and run `uv run --locked ruff check .` and `uv run --locked python -m pytest -q`; add Foundry checks when Solidity changes.
3. Wait for the user to confirm the branch checks passed. Recheck the exact PR head and CI status before merging.
4. Squash-merge via GitHub after the user has confirmed all checks passed; do not ask the user to perform the merge.
5. Tell the user to sync `main` and rerun local Python checks. Only after they confirm post-merge checks passed, provide branch-deletion commands.
6. After the user says `Branch deleted`, refresh this handoff and present the next consolidated PR plan.

## Security and evidence boundaries

- No mainnet. Simulated execution remains the default. Actual ERC-20 transfers are only through the unaudited AegisVault on Base Sepolia (chain ID 84532), using throwaway testnet wallets.
- The agent cannot approve/reject, pause/resume, change owner policy, edit payees/mandates, or bypass the policy gateway or vault.
- Free-text descriptions are context only. Recipient, token, chain and amount must come from owner-authenticated structured task/payee records.
- Payee “verified” means owner-attested, not independently identity-verified.
- Governance audit hash chains are tamper-evident, not digital signatures, and do not prevent a database owner from rewriting the full database and recomputing hashes.
- `available_balance_base_units` is owner-maintained planning data, not a live chain balance. Governance budget history releases reservations for persisted blocked/cancelled/rejected workflow states while missing/unknown transaction evidence remains conservative.
- PR #223's rate limiter is process-local and per client IP; it is not distributed and is not a substitute for an authenticated gateway. `/metrics` counters are process-local.
- Never invent transaction hashes, deployment addresses, test results, CI outcomes or evidence. The operator must generate real Base Sepolia evidence.
- Never claim audit, production readiness, or live evidence without proof.

## Repository

- Repository: https://github.com/NEXE-sudo/aegis-agent-wallet
- Product: testnet-only AI-agent wallet prototype for FinTechathon 2026 Shenzhen International Track, theme “Agent Wallet on Chain”.
- Use British English. Keep user-facing instructions direct and compact.
