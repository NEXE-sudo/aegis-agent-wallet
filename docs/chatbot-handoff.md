# Aegis Agent Wallet — Chatbot Handoff

Last updated: 20 October 2026

## Current work in progress

- PR #226 — [Reconcile governance budget with workflow outcomes](https://github.com/NEXE-sudo/aegis-agent-wallet/pull/226) — was squash-merged as `3f4260e7b099425b3672117b608ed674fbe9c01f`. The user confirmed post-merge checks passed and deleted the feature branch.
- **Current PR in preparation** — branch `feat/reproducible-submission-manifest`. It adds a hash manifest for selected submission artifacts, offline verification of manifest and artifact hashes, regression tests, and a submission evidence guide. Do not mark it complete until local checks and CI are confirmed.
- PR #225 — [Expand adversarial evaluation and governance evidence](https://github.com/NEXE-sudo/aegis-agent-wallet/pull/225) — was squash-merged as `1e0b5f0b90020ffb2fb0d06c141275bef9e925fa`. The user confirmed 110/110 scenario observations passed across five repeated runs, post-merge checks passed, and the branch was deleted.
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
| WS2 — Persistent governance | Owner-managed mandate/payee/task persistence, payload hashes, hash-linked audit records, and stale-record fail-closed checks. | PR #223 added control-centre forms, decision review and JSON/CSV export; PR #226 completed local governance budget lifecycle reconciliation. Remaining work includes role separation/token rotation and export verification workflow. |
| WS3 — AegisVault v2 | Owner controls, exact approvals, global token caps, bounded agent sessions, optional per-recipient caps, Foundry unit/fuzz/invariant tests. | ABI/calldata/configuration drift checks and operator-run deployment/transfer evidence. |
| WS4 — Operations | FastAPI/SQLite, separate bearer-token roles, request metadata logging and CI. | PR #223 added request IDs, process-local mutating-request rate limit, readiness and request metrics. Remaining: signer adapter boundaries, packaging, backup/restore and deployment runbook as feasible. |
| WS5 — Frontend | Dependency-free local control centre and Playwright coverage for core workflow. | PR #223 added governance, persisted decision review and exports; verify browser accessibility, error states and responsive behaviour in tests. |
| WS6 — Demo/submission | Demo scenario, evidence bundles, deployment verification and contract-hardening runbook. | The local evaluation now exercises the persisted governance queue and reports failure reasons; still required are freshly generated reports, verified operator evidence, final threat model/security summary, demo video script, ten-minute presentation and WeBank business case. |

## Next priorities

1. **Submission evidence reproducibility:** generate evaluation outputs from the exact intended source revision, manifest the selected JSON/Markdown artifacts, and verify the manifest and evidence bundle before packaging.
2. **Live testnet evidence:** separately verify operator-run Base Sepolia deployment, successful transfer, and reverted-call evidence where available; never substitute fixtures or simulated results.
3. **Submission close-out:** finalise the threat model/security summary, architecture/data-flow diagrams, ten-minute talk track, video script, and one-page WeBank business case.
4. Keep the number of PRs low and the feature scope substantial, comparable in breadth to PR #221. Combine implementation, relevant tests, docs, changelog, version and handoff changes when they belong together.

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
