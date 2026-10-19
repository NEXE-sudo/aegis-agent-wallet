# Copilot repository instructions

## Product and safety boundaries

- This is a testnet-only AI-agent wallet prototype for the FinTechathon 2026 Shenzhen International Track.
- Simulated execution is the default. Actual ERC-20 execution is limited to the unaudited AegisVault on Base Sepolia (chain ID 84532).
- Preserve owner approval, policy enforcement, fail-closed configuration checks, and the separation between agent and owner authority.
- Never enable mainnet or real-fund execution, claim the vault is audited or production-ready, or expose secrets, private keys, bearer tokens, provider URLs containing credentials, or signed transaction bytes.
- Native ETH transfers and ERC-4337 are separate scope decisions; do not add them implicitly.
- Treat on-chain RPC reads as point-in-time observations, not proof of authenticity, audit, mining, or finality.

## Development workflow

- Work on exactly one small, coherent roadmap task per pull request. Read docs/chatbot-handoff.md before choosing a task and inspect existing routes, service boundaries, frontend data flow, and tests before adding new surfaces.
- Add focused regression tests and update docs/chatbot-handoff.md in the same feature PR. Include the exact sentence "PR details will be recorded by automation." in the current-task bullet; the automation replaces it with the resulting PR number and URL.
- Run uv run --locked ruff check . and uv run --locked python -m pytest -q.
- Do not commit, push, create pull requests, or merge when running under the autonomous workflow; its trusted workflow steps perform those operations.
- Do not modify .github/**, pyproject.toml, uv.lock, contracts/**, deployment/security configuration, or unrelated user-owned/untracked files as part of routine roadmap tasks.
- If tests fail, fix the same branch. The automation allows one additional CI repair; after that, leave the PR open for human intervention.
- Use British English. PR titles must be plain English without conventional prefixes, and the PR description must be one sentence.
- Never claim tests passed unless the actual command or CI result confirms it.
