# Release Checklist and Known Limitations

> **Release correction (2026-10-15):** The existing published `v1.10.0` tag resolves to `4c9c9431dca31d15137501cb99b026fcac829236`, not the newer candidate `154185cc0378448fa0fa4543762d85ead02bdff7` that passed the latest fresh-checkout checks. Leave the published `v1.10.0` tag untouched. The corrective `v1.10.1` release must be based on the merged version bump and verified from a fresh checkout of its exact final commit before tagging.

Aegis Agent Wallet is a local, simulation-only portfolio prototype. This checklist is a release gate for a tagged demo/portfolio version; completing it does not certify production readiness or independently audit security.

## Repeatable final-candidate verification

Run this from a separate directory so the result cannot depend on an existing virtual environment, untracked files, local database, or developer-only configuration. Replace `<FINAL_RELEASE_COMMIT_SHA>` with the exact commit intended for the release; do not use a moving branch name as the evidence identifier.

```bash
git clone https://github.com/NEXE-sudo/aegis-agent-wallet.git aegis-agent-wallet-release-check
cd aegis-agent-wallet-release-check
git checkout --detach <FINAL_RELEASE_COMMIT_SHA>

uv sync --locked --extra dev
uv run --locked playwright install --with-deps chromium
uv run --locked python -m pytest -q

# Match the dependency-audit tooling and invocation used by CI.
uv pip install --python .venv/bin/python "setuptools>=83.0.0" pip-audit
.venv/bin/python -m pip_audit --skip-editable

uv run --locked ruff check .
git rev-parse HEAD
```

Record the resolved commit SHA and the actual output/result of each command in the release evidence. If any command fails, stop and resolve it before considering a tag. The dependency-audit tool is installed into the disposable verification environment after the locked project sync, matching CI; it is not added to the application dependency set. Remove the temporary checkout after recording the results.

## Release gate

- [x] Start from a fresh checkout of the final intended release commit. Verified in a separate clone at `59ac35e28237a988dbdde7a9f33a76857f0d2c83` on 2026-10-15.
- [x] Install the locked dependencies with `uv sync --locked --extra dev` from the final release-candidate checkout (passed on `59ac35e28237a988dbdde7a9f33a76857f0d2c83` on 2026-10-15).
- [x] Run the full test suite from the final release-candidate checkout (`uv run --locked python -m pytest -q`): **383 passed**, with one Starlette/httpx deprecation warning, on `59ac35e28237a988dbdde7a9f33a76857f0d2c83` (2026-10-15).
- [x] Run Ruff from the final release-candidate checkout (`uv run --locked ruff check .`): passed on `59ac35e28237a988dbdde7a9f33a76857f0d2c83` (2026-10-15).
- [x] Follow [the reproducible demo scenario](demo-scenario.md), including allowed simulation, approval-required flow, hard block, idempotency replay/conflict, and audit retrieval (manually exercised on 2026-10-12; the approval audit showed `proposal_created → awaiting_approval`, `approval_granted → approved`, and `simulated_execution → executed_simulated`).
- [x] Confirm the API is bound to localhost for the local demo and that agent and owner bearer tokens are distinct and supplied through environment variables (verified during the 2026-10-12 walkthrough).
- [x] Review dependency-audit results for the final release-candidate checkout: `pip-audit --skip-editable` reported **No known vulnerabilities found** on `59ac35e28237a988dbdde7a9f33a76857f0d2c83` (2026-10-15). The audit was rerun with `VIRTUAL_ENV` unset and `PIPAPI_PYTHON_LOCATION` pointing to the disposable checkout's `.venv/bin/python`; the project distribution is skipped because it is editable. GitHub Actions `Python checks` passed on PR #165 head `c520a236deadb3fb49a1b8dfa5f0b750fc5f3702` (run #38742564410); this covers the documentation-only release-evidence change. [ ] Re-run the complete release gate against the exact final tagged commit before tagging.
- [x] Review README setup commands, runtime configuration, and demo commands against the locked `uv` workflow (the walkthrough uses `uv sync --locked --extra dev`, `uv run --locked`, and loopback binding; the RPC URL is optional for simulation).
- [x] Review the read-only market-data API and documentation for scope and safety (PR #159; owner-authenticated, fixed HTTPS provider endpoints, five-second timeouts, validated symbols/prices/timestamps, stale flags, sanitized provider errors, and no integration with policy or transaction execution; Stooq equity quotes are daily-close data).
- [x] Review tracked paths, representative secret/token patterns, and Git history for accidentally committed secrets, private keys, local databases, and provider credentials (2026-10-12: current-tree/path and selected-pattern checks found no suspicious matches; `gitleaks git --redact --verbose .` passed after PR #155 added a narrowly scoped allowlist for the public Base Sepolia USDC contract address used in `docs/demo-scenario.md`. The three reported historical findings were the same public contract address, not a credential. This is evidence from these checks, not proof that no secret has ever existed in repository history or outside the scanned patterns).
- [x] Confirm all documentation describes execution as simulated and Base Sepolia inspection as read-only (reviewed README, runtime configuration, demo scenario, and threat model).
- [x] Review the known limitations below and the [threat model](threat-model.md); confirm they accurately describe simulation-only execution, development bearer-token authentication, heuristic risk scoring, local accounting, and the audit-integrity boundary without claiming production readiness (reviewed 2026-10-12).
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
