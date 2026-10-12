# Release Checklist and Known Limitations

Aegis Agent Wallet is a local, simulation-only portfolio prototype. This checklist is a release gate for a tagged demo/portfolio version; completing it does not certify production readiness or independently audit security.

## Release gate

- [x] Start from a fresh checkout of the intended release commit (fresh clone of `main` at `c6a37a1`; see PR #151).
- [x] Install the locked dependencies with `uv sync --locked --extra dev` (completed in the fresh checkout; PR #151).
- [x] Run `python -m pytest -q` and confirm the full suite passes (349 passed; PR #151).
- [x] Run `ruff check .` and confirm lint passes (PR #151).
- [x] Follow [the reproducible demo scenario](demo-scenario.md), including allowed simulation, approval-required flow, hard block, idempotency replay/conflict, and audit retrieval (manually exercised on 2026-10-12; the approval audit showed `proposal_created → awaiting_approval`, `approval_granted → approved`, and `simulated_execution → executed_simulated`).
- [x] Confirm the API is bound to localhost for the local demo and that agent and owner bearer tokens are distinct and supplied through environment variables (verified during the 2026-10-12 walkthrough).
- [ ] Review the current CI results and dependency-audit results for the release commit; resolve or explicitly document actionable failures.
- [x] Review README setup commands, runtime configuration, and demo commands against the locked `uv` workflow (the walkthrough uses `uv sync --locked --extra dev`, `uv run --locked`, and loopback binding; the RPC URL is optional for simulation).
- [ ] Confirm no secrets, private keys, local database files, or provider credentials are committed.
- [x] Confirm all documentation describes execution as simulated and Base Sepolia inspection as read-only (reviewed README, runtime configuration, demo scenario, and threat model).
- [ ] Review the known limitations below and the [threat model](threat-model.md); do not describe this prototype as production-ready.
- [ ] Only after every required item is checked, choose an intentional version/tag and publish release notes that state the simulation-only scope.

Do not mark a checklist item complete based on intention alone. Record any accepted exception and its rationale in the release notes.

## Known limitations

- **No real transaction execution:** no private keys are loaded, transactions are not signed, and transactions are not submitted. The Base Sepolia integration is read-only.
- **Heuristic risk score:** risk scoring is explainable but not a validated fraud detector.
- **Development authentication:** bearer tokens are a local development guard, not a production identity or access-management system.
- **Local accounting model:** persisted spending reservations are not confirmed on-chain balances, submissions, or receipts.
- **Recipient identity:** EVM address format validation does not establish who controls an address or whether it belongs to the intended recipient.
- **Token authenticity:** readable token metadata and contract bytecode do not prove a token is authentic.
- **Audit integrity boundary:** SQLite triggers provide application-level protection against ordinary row updates/deletes, not cryptographic tamper-proofing against someone who controls the database file.
- **No production execution guarantees:** real execution would require a separate design for secure key custody, signing, nonce management, submission idempotency, receipt reconciliation, chain reorg handling, operational monitoring, incident response, and independent security review.

These limitations are consistent with the current scope. Any move toward signing or submission requires a separate explicit scope decision and dedicated design/security review.
