# Aegis Agent Wallet — Project Tracker and AI Handoff

> **Purpose:** This is the canonical project status and continuation guide for humans and AI assistants. Read this file before proposing or implementing the next change.
>
> **Last updated:** 2026-10-10  
> **Repository:** [NEXE-sudo/aegis-agent-wallet](https://github.com/NEXE-sudo/aegis-agent-wallet)  
> **Current package/API version:** 1.10.0  
> **Current phase:** Milestone 2 — Safe Transaction Flow  
> **Current status:** Milestones 1 complete; 2 in progress; 3–7 planned; Milestone 8 is optional and out of current scope.

## 1. Project mission and non-negotiable boundary

Aegis Agent Wallet is a testnet-focused AI-agent wallet prototype that demonstrates policy evaluation, explainable heuristic risk scoring, human approval, persisted workflow state, replay/idempotency protection, spending reservations, and an audit trail.

**Execution is simulation-only.** The project does not load private keys, sign transactions, or submit transactions. The Base Sepolia integration is read-only. Do not enable real signing or transaction submission as part of the current roadmap without an explicit, separate scope decision.

Important current limitations:
- The risk score is a heuristic, not a validated fraud detector.
- Bearer tokens are development authentication, not a production identity system.
- SQLite audit triggers provide application-level tamper resistance, not cryptographic proof against someone controlling the database file.
- Persisted reservations are a local accounting model, not confirmed on-chain balances or receipts.
- An EVM address's format does not establish recipient identity; readable token metadata does not alone prove token authenticity.

## 2. How we work (required continuation protocol)

These rules are part of the project, not optional suggestions. Follow them in every new chat or with a different AI.

1. **Use small, focused PRs.** Prefer one cohesive security, reliability, test, or documentation change per PR. Avoid bundling unrelated refactors.
2. **Work from current `main`.** Before creating a branch, inspect the current repository state and base the branch on the latest `main`.
3. **Do not merge on your own test claims.** The human runs local validation and explicitly reports that both `python -m pytest -q` and `ruff check .` passed.
4. **Only after that explicit confirmation:** fetch the PR again; verify it is open, not already merged, and mergeable; use squash merge with the latest PR head SHA as the expected SHA; fetch the PR again and verify that it is closed and merged.
5. **After merge:** give the human the commands to sync local `main` and rerun both checks. Wait for confirmation before proceeding to the next implementation PR.
6. **Never claim local tests ran** unless the human actually reports the result or an available execution environment genuinely ran them.
7. Keep execution simulation-only; fail closed on security-sensitive uncertainty; add regression tests for bug fixes and security controls.
8. When scope or an implementation detail is uncertain, inspect the code/tests and explain the tradeoff rather than inventing project state.
9. Update this tracker in a focused PR when milestone status, acceptance criteria, next action, or important project decisions change. Record completed work only after it is merged.

### Local validation commands

For a new remote branch:
```bash
git fetch origin
git switch -c <branch-name> --track origin/<branch-name>
python -m pytest -q
ruff check .
```

For an existing local branch:
```bash
git pull --ff-only origin <branch-name>
python -m pytest -q
ruff check .
```

After a PR is merged:
```bash
git switch main
git pull --ff-only origin main
python -m pytest -q
ruff check .
```

**Merge gate:** do not merge until the user explicitly confirms both test commands passed for the PR branch.

## 3. Milestone roadmap

Estimates below are planning ranges, not promises. Actual PR count depends on test findings and the scope chosen at each milestone.

### Milestone 1 — Foundation
**Status: COMPLETE**

Goal: establish the API, policy/risk model, workflow persistence, and the first simulation-only transaction path.

Completed foundations include:
- FastAPI service and protected agent/owner operations.
- Policy evaluation and explainable risk assessment.
- SQLite-backed workflow state and simulated execution.
- Read-only Base Sepolia JSON-RPC/token metadata inspection.

### Milestone 2 — Safe Transaction Flow
**Status: IN PROGRESS — current milestone**

Goal: ensure proposed transactions are bound to validated policy, explicit approvals, reliable state transitions, spending controls, replay protection, and an auditable history.

Completed controls include:
- Trusted-token chain/symbol/decimals validation and hard blocks for mismatches.
- EVM recipient format validation.
- Risk/policy revalidation before simulated execution; high-risk escalation requires fresh approval.
- Append-only audit rows protected from ordinary update/delete operations by SQLite triggers.
- Approval expiry and reapproval expiry handling.
- Recipient/token allowlists, recipient daily caps, and recipient per-transaction caps.
- Fail-fast policy/config validation and SQLite signed 64-bit integer bounds.
- Overflow-safe aggregation of persisted spending reservations.
- Persisted proposal fingerprint verification during approval, execution, and idempotent retries.
- Idempotency conflict handling and audit events.

Remaining candidate work (inspect current implementation and tests before selecting a task; do not assume every item is a confirmed defect):
- [ ] Review all workflow state transitions against a written state-transition table.
- [x] Add targeted negative tests for malformed/missing/future approval timestamps and expiry boundaries.
- [ ] Review idempotency-key uniqueness behavior and concurrent retries under SQLite write locking.
- [x] Reject whitespace-only idempotency keys at the API boundary.
- [ ] Review transaction reservation accounting across blocked, expired, simulated-executed, and same-day-boundary cases.
- [ ] Review API error responses and audit events for consistent fail-closed behavior without leaking secrets.
- [ ] Review configuration parsing for malformed environment values and invalid combinations.
- [ ] Document the threat model, trust boundaries, assumptions, and explicit non-goals.
- [ ] Close any additional gaps revealed by tests or review.

**Exit criteria:** the important state transitions and failure paths are documented and regression-tested; the test suite and Ruff pass; remaining limitations are explicit. This is a prototype milestone, not a claim of production security.

### Milestone 3 — Agent and Demo
**Status: PLANNED**

Goal: make the workflow easy to understand and demonstrate end to end.
- [ ] Define a reproducible demo scenario and expected outcomes.
- [ ] Add a minimal agent/client example that proposes transactions through the authenticated API.
- [ ] Demonstrate allowed, hard-blocked, approval-required, expired-approval, and replay/conflict paths.
- [ ] Keep the demo deterministic and simulation-only.
- [ ] Add a concise demo script or documented command sequence and expected output.
- [ ] Ensure secrets and provider credentials are supplied via environment variables and never committed.

**Exit criteria:** a new developer can run the app and reproduce the documented happy path and important rejection paths.

### Milestone 4 — Testing and Reliability
**Status: PLANNED**

Goal: test correctness under failures, concurrency, and persistence changes.
- [ ] Review test coverage by API endpoint, policy rule, and workflow transition.
- [ ] Add concurrency tests for competing proposals/reservations and idempotent retries where practical.
- [ ] Test database initialization, additive migrations, and compatibility with an existing database.
- [ ] Test recovery/error behavior for malformed or inconsistent persisted data.
- [ ] Add CI automation for tests and Ruff if not already configured.
- [ ] Keep tests deterministic; avoid live RPC dependence in the unit-test suite.

**Exit criteria:** automated checks run reproducibly and important failure modes have regression coverage.

### Milestone 5 — Observability and Operations
**Status: PLANNED**

Goal: make local operation and debugging safer and clearer.
- [ ] Review structured logging and request/error observability.
- [ ] Ensure secrets, bearer tokens, and provider API keys are never logged.
- [ ] Document environment variables, defaults, validation, and safe local binding.
- [ ] Add a practical health/readiness and troubleshooting guide without overstating what health checks prove.
- [ ] Document SQLite backup, restore, and local data-reset expectations.
- [ ] Review operational behavior when the database or RPC endpoint is unavailable.

**Exit criteria:** operators can configure, run, diagnose, and reset the local prototype safely.

### Milestone 6 — Security Review
**Status: PLANNED**

Goal: conduct a deliberate review of attack surfaces and documented guarantees.
- [ ] Maintain a concise threat model and assets/trust-boundary inventory.
- [ ] Review authentication/authorization on every endpoint.
- [ ] Review input validation, policy override resistance, replay behavior, and audit integrity assumptions.
- [ ] Add adversarial tests for tampering, invalid states, oversized inputs, and policy changes.
- [ ] Review dependency and secret-management practices.
- [ ] Document findings, mitigations, residual risks, and out-of-scope production controls.

**Exit criteria:** findings are tracked and addressed or explicitly accepted; no unsupported production-security claims are made.

### Milestone 7 — Documentation and Release
**Status: PLANNED**

Goal: package the prototype as a clear, reproducible portfolio/demo project.
- [ ] Keep README setup instructions aligned with actual code and configuration.
- [ ] Add architecture/workflow diagrams if they improve understanding.
- [ ] Document API examples, approval semantics, hard blocks, and simulation-only execution.
- [ ] Record known limitations and a release checklist.
- [ ] Verify a clean setup from a fresh checkout.
- [ ] Tag/version only after the release criteria and checks are satisfied.

**Exit criteria:** a reviewer can understand the architecture, run the demo, inspect tests, and distinguish implemented guarantees from limitations.

### Milestone 8 — Optional production-readiness investigation
**Status: OUT OF CURRENT SCOPE — separate decision required**

This would be a separate design/security project, not a simple continuation of the simulation prototype. It may involve secure key custody, transaction signing, nonce management, submission idempotency, receipt reconciliation, RPC trust, chain reorgs, monitoring, incident response, and independent security review. Do not implement real signing or submission unless the project owner explicitly authorizes this new scope after a dedicated design review.

## 4. Iteration estimate

A reasonable current planning range is **about 15–28 additional focused PRs** to reach a strong simulation-only showcase:
- Milestone 2: roughly 2–5 more PRs.
- Milestone 3: roughly 3–5 PRs.
- Milestone 4: roughly 3–5 PRs.
- Milestone 5: roughly 2–4 PRs.
- Milestone 6: roughly 3–6 PRs.
- Milestone 7: roughly 2–3 PRs.

Ranges overlap in practice: a PR may satisfy more than one acceptance criterion, and test findings can add work. Milestone 8 is excluded and would require a separate estimate.

## 5. Merged PR history

This is a compact summary of merged work. Use the actual PRs and source code as authoritative details.

| PR | Change | Result |
|---:|---|---|
| #1 | Read-only Base Sepolia JSON-RPC client and token metadata inspection; authenticated routes | Merged |
| #2 | Trusted-token metadata validation; hard-block chain/symbol/decimals mismatch and unknown tokens | Merged |
| #3 | Malformed EVM recipient addresses hard-blocked | Merged |
| #4 | Revalidate risk before simulation under SQLite write lock; require approval on high-risk escalation | Merged |
| #5 | Persistent append-only audit trail and owner-authenticated audit endpoint | Merged |
| #6 | Time-limited approvals with configurable expiry and expiry audit event | Merged |
| #7 | Recipient-specific token allowlist | Merged |
| #8 | Per-recipient daily spending caps | Merged |
| #9 | Per-recipient single-transaction amount caps | Merged |
| #10 | Fail-fast validation of policy/configuration values | Merged |
| #11 | Reject values outside SQLite signed 64-bit integer bounds | Merged |
| #12 | Python-integer aggregation avoids SQLite SUM overflow for persisted spend | Merged |
| #13 | Verify persisted proposal integrity before approval and execution | Merged |
| #14 | Verify persisted proposal integrity on idempotent retries | Merged |
| #15 | Expire stale approvals when reapproval is attempted | Merged |
| #16 | Align API version metadata with package version and add a regression test | Merged |

## 6. Current architecture and useful code locations

- `app/main.py`: FastAPI models, endpoints, authentication dependencies, API error mapping, demo policy, application version.
- `app/policy/models.py`: policy and trusted-token data/configuration validation.
- `app/policy/engine.py`: deterministic policy decisions and hard blocks.
- `app/risk/engine.py` and `app/risk/models.py`: heuristic risk scoring.
- `app/workflow/controller.py`: workflow orchestration, policy/risk evaluation, approval/execution control.
- `app/workflow/store.py`: SQLite persistence, locking, spend reservation, idempotency, fingerprints, audit trail, expiry.
- `app/chain/`: read-only Base Sepolia chain integration.
- `tests/test_api.py`: API, policy, workflow, and security regression tests.
- `pyproject.toml`: package version, dependencies, pytest and Ruff configuration.
- `README.md`: setup, API behavior, configuration, and safety boundaries.

Verify paths and current implementation before relying on this summary; code is the final authority.

## 7. AI/new-chat handoff checklist

At the start of a new chat or when switching AI assistants:

1. Read this file from the latest `main`.
2. Inspect the current GitHub PR list and repository state; do not assume the PR numbers or branch state in a past conversation are current.
3. Check for any open PR and ask whether its local tests and Ruff checks have passed before considering a merge.
4. Confirm the latest merged milestone/PR and current local branch status with the project owner if not evident.
5. Pick one small next task from the current milestone, inspect relevant code/tests, and explain its scope before editing.
6. Create a branch from current `main`; make one focused change with regression tests as appropriate.
7. Open a PR and provide exact local validation commands.
8. Follow the merge gate and post-merge sync protocol in Section 2.
9. Update this document in a later focused PR when status changes; do not mark planned work complete merely because it was proposed or coded on an unmerged branch.

**If conversation history conflicts with this file, inspect the current repository and merged PRs.** Update this document through a PR when the durable project state changes.
