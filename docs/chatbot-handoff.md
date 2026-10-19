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

## Phased roadmap

### Phase 1 — Secure execution foundation (complete)
- Vault-only execution for eligible agent proposals.
- Remove direct EOA token-transfer mode.
- Preserve owner approval requirements and simulated mode as the default.

### Phase 2 — Wallet read and configuration layer (in progress)
- Backend read surfaces now include public-address Base Sepolia ETH/demo-USDC balances, active policy configuration, and UTC-day local workflow spending summaries.
- Frontend surfaces now include read-only wallet balances, active policy settings/allowlists, daily spending/remaining budget, the owner-authenticated RPC-reported network status panel, and the configured vault status comparison.
- Next: review transaction receipt/history visibility and its existing endpoint support, then select the smallest verified slice.
- Clearly distinguish local configuration/database values from state verified on-chain; fail clearly when values are unavailable or configuration is missing/mismatched.
- Inspect existing routes, service boundaries, frontend data flow, and tests before choosing the next smallest coherent slice. Avoid duplicate endpoints and never expose secrets/private keys.

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

## Current status and next action

- PR #183 (daily spending Overview panel): squash-merged as `62bdf6d9b72b01d322929294bfcd4f33be346918`; CI passed and the user confirmed checks on updated `main`.
- PR #184 (read-only active policy configuration in Settings): squash-merged as `f548deeb5506b0ce121aa059c3ab2c344154c14b`; CI passed and the user confirmed checks on updated `main`.
- PR #185 (read-only wallet balances on Overview): squash-merged as `fbe22f13dd59a6572eccd07544aa3016a22b1cbc`; CI passed on Python 3.11/3.12/3.14 and the user confirmed checks on updated `main`.
- The wallet balance panel uses owner-authenticated `GET /chain/base-sepolia/address/{address}/balances`, accepts a public address, checks chain ID 84532, guards against stale-session responses, and escapes API-provided values. It does not request private keys or sign/broadcast transactions.
- The user has confirmed post-merge Ruff and pytest checks for PR #185. Local `.forge/` and `contracts/lib/` are intentionally untracked while the user investigates them; do not delete, stage, or modify them without instruction.
- PR #187 (network status on Overview): squash-merged as `f220ee98fc75ee03910feee39b8c5f2220b7634c`; CI passed on Python 3.11/3.12/3.14, and the user confirmed Ruff and pytest passed on updated `main` and deleted the branch. The panel reports only the configured RPC's chain ID; it does not prove vault deployment, token authenticity, or transaction finality.
- PR #188 (configured vault status on Overview): squash-merged as `7f829059c4400f1300a338c6b32692f7acce8118`; GitHub Actions passed on Python 3.11/3.12/3.14, the user confirmed post-merge Ruff and pytest passed on updated `main`, and the branch was deleted. The panel reports vault roles, pause state, and policy differences from the configured RPC; this is not an independent contract audit.
- The user has completed post-merge verification for PR #188 and deleted its branch. The next product slice is transaction receipt/history visibility, after inspecting existing endpoint support.
- The autonomous development setup is being proposed in its own PR. It must use GitHub cloud execution, one focused task at a time, bounded Copilot credit/continuation/time limits, one same-branch CI repair, required checks before squash auto-merge, and the safety rules in docs/autonomous-development.md. Never update `main` directly.
- Keep every future handoff update inside the feature PR that motivates it. Never update `main` directly.

## Standard accounting and safety semantics

- Daily workflow spend is keyed by UTC date, chain ID, and token address.
- Active `ready`, `awaiting_approval`, and `approved` proposals reserve budget; `submitting_onchain` remains reserved while an RPC outcome is uncertain.
- `executed_simulated` and `submitted_onchain` count for the UTC execution day recorded by the workflow store. A submitted transaction is not proof of mining or finality.
- `GET /spending/summary` is local workflow accounting, not an on-chain balance or vault spend counter.
- Protect the SQLite database and backups. The testnet vault is unaudited and must only use throwaway Base Sepolia wallets.


## Autonomous development workflow

- PR #189 (Set up bounded autonomous development) is open on branch agent/autonomous-development-workflow. It adds the GitHub Actions/Copilot CLI workflow, repository instructions, and setup documentation; configure the repository secrets described in docs/autonomous-development.md before enabling the loop. Wait for all CI checks and the user's branch validation before merging.
- The loop starts from Actions → Autonomous development → Run workflow, then continues after an autonomous task PR is merged. It creates no more than one task PR at a time and has a rolling cap of three autonomous task PR merges per 24 hours.
- Each Copilot run is capped at 100 AI credits, three autopilot continuations, and 60 minutes. One failed CI run may be repaired on the same branch; after a second failure the PR remains open for human intervention.
- Generated PR descriptions remain one sentence; a hidden marker identifies autonomous task PRs for continuation. Required branch protections and review rules remain authoritative.
- The automation must never touch user-owned untracked .forge/, contracts/lib/, or aegis-agent-wallet-handoff.md, nor expand beyond simulated-by-default Base Sepolia testnet execution through the unaudited AegisVault.
