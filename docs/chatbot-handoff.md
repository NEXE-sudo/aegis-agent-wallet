# Aegis Agent Wallet — Chatbot Handoff

Last updated: 19 October 2026

## Repository and product

- Repository: https://github.com/NEXE-sudo/aegis-agent-wallet
- Product: testnet-only AI-agent wallet prototype for the FinTechathon 2026 Shenzhen International Track, theme “Agent Wallet on Chain”.
- The AI agent is a wallet user, not the product pitch.
- Current safety boundary: simulated execution by default; actual ERC-20 execution is only through the unaudited AegisVault on Base Sepolia (chain ID 84532). No mainnet or production-readiness claims.

## Current state

- PR #177 is merged: vault-bounded agent execution for eligible proposals while preserving owner approvals.
- PR #179 is merged: removed direct EOA execution and made AegisVault the only route for actual Base Sepolia transfers; merge commit `c092d9b953cc6eecfefb224cf33a5d3001331a0a`.
- PR #180 is merged: added owner-authenticated `GET /chain/base-sepolia/address/{address}/balances` for read-only native ETH and demo USDC balances, exact integer base units, decimal strings, strict Base Sepolia verification, tests, and documentation; merge commit `9ff090e94b86e3a466505256d3f040ca8a90271e`.
- The user confirmed Ruff and pytest passed on updated `main` after PR #180 and reported that the `agent/base-sepolia-wallet-balances` branch was deleted.
- Current work is PR #181, Phase 2 second slice: add owner-authenticated read-only `GET /policy/config` for the active demo policy, allowlists, spending limits, and approval settings, with tests and documentation. Branch: `agent/read-only-policy-config`.

## Phased roadmap

### Phase 1 — Secure execution foundation (complete)
- Vault-only execution for eligible agent proposals.
- Remove direct EOA token-transfer mode.
- Preserve owner approval requirements and simulated mode as the default.

### Phase 2 — Wallet read and configuration layer (in progress)
- Add wallet address/network inspection and native ETH plus configured token balance reads.
- Expose agent/owner identity and policy/limits in safe, read-only form.
- Surface current spending and transaction status with clear semantics.
- Keep each feature in a small focused PR; PR #180 delivered the balance-read slice and PR #181 adds the policy-configuration read.

### Phase 3 — Wallet lifecycle and configuration verification
- Improve setup and verification of network settings, wallet deployment state, and contract configuration.
- Fail closed on missing, malformed, or mismatched configuration.
- Keep operational setup and verification independently testable.

### Phase 4 — Wallet dashboard and visibility
- Present wallet identity, network, balances, limits, spending, and transaction status in the existing dependency-free frontend.
- Use real API responses; do not fabricate wallet data or market movement.
- Maintain owner/agent permission boundaries.

### Phase 5 — Contract and integration hardening
- Strengthen contract and integration tests.
- Gather Foundry evidence and testnet deployment/transfer/revert evidence, including transaction hashes where available.
- Document remaining security limitations and residual risks.

### Explicitly separate scope decisions
- Native ETH transfer support is a separate decision from read-only ETH balance support.
- ERC-4337 account abstraction is a separate architectural decision.
- Do not enable mainnet execution or claim the unaudited vault is production-ready.

## Required PR workflow

The user owns local validation and merging. Do not merge PRs on the user's behalf.

1. Create one focused branch from current `main`, implement the change, add tests, and open a PR.
2. PR title should be plain English. The PR description must be one sentence explaining the change; do not use prefixes such as `feat:` or `milestone:`.
3. The user pulls the PR branch and runs these checks locally:
   ```bash
   uv run --locked ruff check .
   uv run --locked python -m pytest -q
   ```
4. Only after both checks pass, the user squash-merges the PR into `main`, pulls updated `main`, and reruns both checks.
5. Do not provide or run branch deletion steps until the user confirms both checks passed on updated `main` and GitHub confirms the PR is merged.
6. Cleanup commands for the local branch and remote GitHub branch must be in the same code block.
7. After cleanup, update this handoff document **inside the next PR**. Every future change to this or another project document must be made on a branch and included in a PR; do not make out-of-band handoff edits.
8. Before each next PR, inspect current GitHub state and existing code. Never assume a PR merged just because local tests passed. Confirm merge status and branch state through GitHub.
9. Use British English. Keep guidance compact and practical. Do not address the user by name.

## Standard commands

PR branch validation:
```bash
uv run --locked ruff check .
uv run --locked python -m pytest -q
```

After the user confirms merge and successful checks on main:
```bash
git switch main
git pull --ff-only origin main
uv run --locked ruff check .
uv run --locked python -m pytest -q
```

After successful main checks, clean up both branches together:
```bash
git switch main
git branch -d <branch-name>
git push origin --delete <branch-name>
```

## Next steps

- PR #180 is complete: merge verified, the user confirmed Ruff and pytest passed on updated `main`, and the user reported branch cleanup complete.
- Review PR #181 for `GET /policy/config`; wait for the user to run Ruff and pytest on its branch.
- After passing branch checks, have the user squash-merge PR #181, pull updated `main`, and rerun both checks.
- Only after successful main checks and confirmed merge, provide combined local/remote branch cleanup commands.
- Keep this handoff document current through a future PR, never by editing `main` directly.
