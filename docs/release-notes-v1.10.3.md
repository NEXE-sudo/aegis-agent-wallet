# Aegis Agent Wallet v1.10.3 — Testnet and Agent Evaluation (Draft)

**Release scope:** local-first wallet-agent prototype with simulated execution by
default and explicitly gated Base Sepolia testnet execution (chain ID `84532`).

## Highlights

- Add opt-in, locally signed ERC-20 execution on Base Sepolia only. Execution
  requires explicit configuration, is owner-authorized, revalidates policy, risk,
  spending, and approval immediately before signing, and safely retries the same
  persisted signed transaction after an uncertain submission. Simulation remains
  the default; no mainnet execution is supported.
- Add owner-only receipt status reads and mode-aware execution status in the
  frontend. A submitted transaction is not presented as proof of finality.
- Retain owner-authenticated rejection of pending proposals, with audit recording,
  idempotent handling, and release of the reserved daily budget.
- Add the `agent` package with agent-token-only proposal, market-quote, and limited
  transaction-status tools; a deterministic offline fallback; and an optional
  OpenAI-compatible LLM provider. The agent has no approval or execution tools.
- Add deterministic offline evaluation scenarios to the CI workflow, plus an
  opt-in LLM evaluation mode for five narrow invoice-text injection scenarios.
  LLM reports are generated from actual runs and record the model, timestamp,
  repetitions, observed outcomes, and pass count per scenario.
- Carry forward the release-correction safeguards: same-day Stooq equity-date handling,
  explicit SQLite connection closing, bounded ERC-20 metadata with the
  `erc20_metadata_readable` field, nonce-based Content Security Policy and browser
  security headers, and documentation of the public demo policy/risk evaluation
  endpoints.
- Add technical and security self-assessments, a Base Sepolia manual test runbook
  and read-only evidence verifier, and an archived audit findings document with
  current statuses.

## Verification

- `python -m pytest -q`: **456 passed**, with one existing Starlette/httpx
  deprecation warning, on source commit
  `26a871f658ac6bfdc6543c6b4525c1817230ac1a` with the 1.10.3 release documents
  in the candidate working tree.
- `ruff check .`: passed on the same candidate working tree.
- The package metadata, API version, lockfile, and frontend version label are
  consistently `1.10.3`.
- The optional LLM evaluation was not run against a real provider for this report;
  no LLM result is asserted. No live Base Sepolia transfer or transaction hash is
  asserted.
- Rerun the full fresh-checkout release gate against the final exact commit before
  tagging or publishing.

## Safety boundary

The default execution mode is simulated. The optional execution mode supports
Base Sepolia only and is not production key custody. Use a throwaway testnet wallet
and protect its private key. The agent cannot approve or execute; owner-only,
fingerprint-bound approval and execution controls remain in force. This prototype
does not support mainnet or real-fund transfers and does not claim transaction
finality.

---

**Draft status:** Keep 1.10.3 unreleased until the final release checklist passes
and the owner intentionally tags the release.
