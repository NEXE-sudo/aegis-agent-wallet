# Aegis Agent Wallet — Chatbot Handoff

Last updated: 20 October 2026

## Repository and product

- Repository: https://github.com/NEXE-sudo/aegis-agent-wallet
- Product: testnet-only AI-agent wallet prototype for the FinTechathon 2026 Shenzhen International Track, theme “Agent Wallet on Chain”.
- The AI agent is a wallet user, not the product pitch.
- Safety boundary: simulated execution by default; actual ERC-20 execution is only through the unaudited AegisVault on Base Sepolia (chain ID 84532). No mainnet or production-readiness claims.

## Current state

- PR #177 added vault-bounded agent execution while preserving owner approvals.
- PR #179 retired direct EOA token execution; actual Base Sepolia ERC-20 execution is routed through AegisVault.
- PR #180 added owner-authenticated read-only Base Sepolia ETH and demo USDC balance inspection.
- PR #181 added owner-authenticated read-only `GET /policy/config` for active policy settings and allowlists.
- PR #181 was squash-merged as `71b611700b0a3b113d9fa102789572c54c7ab77a`; the user confirmed checks passed on the PR and updated `main`, and the branch was deleted.
- PR #182 added owner-authenticated `GET /spending/summary` for UTC-day local workflow spend and remaining budget per allowed token; it is merged, the user confirmed checks passed on updated `main`, and the branch was deleted.
- PR #183 added the Overview dashboard's owner-authenticated daily spending summary with exact token-unit formatting, per-token limits and remaining budget, stale-session protection, and clear local-accounting semantics; it was squash-merged as `62bdf6d9b72b01d322929294bfcd4f33be346918`, all three CI jobs passed, and the user confirmed checks passed on updated `main` and deleted the branch.
- Current work is PR #184, Phase 2: expose the existing owner-authenticated `GET /policy/config` response in Settings as a read-only view of active chain/token/recipient allowlists, spending limits, and approval rules. Branch: `agent/dashboard-policy-visibility`.

## Phased roadmap

### Phase 1 — Secure execution foundation (complete)
- Vault-only execution for eligible agent proposals.
- Remove direct EOA token-transfer mode.
- Preserve owner approval requirements and simulated mode as the default.

### Phase 2 — Wallet read and configuration layer (in progress)
- Read wallet balances and inspect policy configuration.
- Report current spending and transaction status with explicit local-versus-on-chain semantics.
- Keep each feature in a small, independently testable PR.

### Phase 3 — Wallet lifecycle and configuration verification
- Verify network settings, wallet deployment state, and contract configuration.
- Fail closed on missing, malformed, or mismatched configuration.

### Phase 4 — Wallet dashboard and visibility
- Present identity, network, balances, limits, spending, and transaction status in the existing dependency-free frontend.
- Use real API responses; never fabricate wallet data or market movement.

### Phase 5 — Contract and integration hardening
- Strengthen contract and integration tests.
- Gather Foundry and testnet deployment/transfer/revert evidence, including transaction hashes where available.
- Document remaining security limitations and residual risks.

### Explicitly separate scope decisions
- Native ETH transfer support is separate from read-only ETH balance support.
- ERC-4337 account abstraction is a separate architectural decision.
- Do not enable mainnet execution or claim the unaudited vault is production-ready.

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

## Current PR and next steps

- PR #183 is complete; the user confirmed both checks passed on updated `main` and deleted its branch.
- Current PR #184 adds a read-only active-policy panel to Settings using `GET /policy/config`; it does not change backend policy, credentials, or execution behaviour.
- The panel is owner-authenticated, guards against stale-session responses, renders backend-provided values as escaped text, and displays policy errors without treating them as connection failures.
- Ask the user to pull `agent/dashboard-policy-visibility` and run `uv run --locked ruff check .` and `uv run --locked python -m pytest -q`.
- Once both pass on the PR branch, verify the current PR head and squash-merge PR #184.
- Then have the user pull updated `main` and rerun both checks. Verify the merge and only then provide combined local/remote cleanup commands.
- After cleanup, update this handoff inside the next feature PR.

## Standard accounting and safety semantics

- Daily workflow spend is keyed by UTC date, chain ID, and token address.
- Active `ready`, `awaiting_approval`, and `approved` proposals reserve budget; `submitting_onchain` remains reserved while an RPC outcome is uncertain.
- `executed_simulated` and `submitted_onchain` count for the UTC execution day recorded by the workflow store. A submitted transaction is not proof of mining or finality.
- `GET /spending/summary` is local workflow accounting, not an on-chain balance or vault spend counter.
- Protect the SQLite database and backups. The testnet vault is unaudited and must only use throwaway Base Sepolia wallets.
