# Aegis Agent Wallet — Project Tracker and AI Handoff

> **Purpose:** This is the canonical project status and continuation guide for humans and AI assistants. Read this file before proposing or implementing the next change.
>
> **Last updated:** 2026-10-15  
> **Repository:** [NEXE-sudo/aegis-agent-wallet](https://github.com/NEXE-sudo/aegis-agent-wallet)  
> **Current package/API version:** 1.10.0  
> **Current phase:** Milestone 7 — Documentation and Release
> **Current status:** Milestones 1–6 complete; Milestone 7 in progress; Milestone 8 is optional and out of current scope.

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
4. **After the user reports both checks passed:** automatically fetch the PR again; verify it is open, not already merged, and mergeable; squash-merge using the latest PR head SHA as the expected SHA; then fetch the PR again and verify it is closed and merged. Do not ask for a separate merge confirmation.
5. **After merge:** give the human the commands to sync local `main`, rerun both checks, and delete the merged feature branch. Once the human confirms this is complete, immediately select and start the next focused task from this tracker; do not ask whether to continue or what to work on next unless a genuine project-scope decision is required.
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

After both post-merge checks pass, remove that PR's feature branch (replace `<branch-name>` with the exact branch used for the PR):

```bash
git branch -D <branch-name>
if git ls-remote --exit-code --heads origin <branch-name> >/dev/null 2>&1; then
  git push origin --delete <branch-name>
else
  echo "Remote branch already deleted."
fi
```

Use this only after verifying the PR is merged and local `main` is synced. Never delete `main` or a branch with unmerged work.

**Merge gate:** wait for the user to explicitly report that both `python -m pytest -q` and `ruff check .` passed for the PR branch; then automatically squash-merge after verifying the current PR state and head SHA. No separate merge approval is needed.

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

**Status: COMPLETE**

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

- [x] Review all workflow state transitions against a written state-transition table (PR #23 documentation and regression tests).
- [x] Add targeted negative tests for malformed/missing/future approval timestamps and expiry boundaries.
- [x] Review idempotency-key uniqueness behavior and concurrent retries under SQLite write locking.
- [x] Reject whitespace-only idempotency keys at the API boundary.
- [x] Review transaction reservation accounting across blocked, expired, simulated-executed, and same-day-boundary cases (PR #21 regression coverage).
- [x] Review API error responses and audit events for consistent fail-closed behavior without leaking secrets (PRs #22, #25, #27–#29).
- [x] Review configuration parsing for malformed environment values and invalid combinations (blank database paths and identical agent/owner tokens are rejected).
- [x] Document the threat model, trust boundaries, assumptions, and explicit non-goals (`docs/threat-model.md`).
- [x] Close additional concrete gaps found in the final review: malformed JSON-RPC envelopes/results and malformed persisted workflow/audit JSON (PRs #27–#29).

**Exit criteria:** the important state transitions and failure paths are documented and regression-tested; the test suite and Ruff pass; remaining limitations are explicit. This is a prototype milestone, not a claim of production security.

### Milestone 3 — Agent and Demo

**Status: COMPLETE**

Goal: make the workflow easy to understand and demonstrate end to end.

- [x] Define a reproducible demo scenario and expected outcomes (`docs/demo-scenario.md`; added in PR #32).
- [x] Add a minimal agent/client example that proposes transactions through the authenticated API (PR #33).
- [x] Verify the documented allowed, hard-blocked, approval-required, expired-approval, and replay/conflict paths end to end (`docs/demo-scenario.md`; regression coverage in PRs #55–#60, with the expired-approval walkthrough added in PR #34).
- [x] Keep the demo deterministic and simulation-only.
- [x] Add a concise documented command sequence and expected outcomes.
- [x] Ensure secrets and provider credentials are supplied via environment variables and never committed.

**Exit criteria:** a new developer can run the app and reproduce the documented happy path and important rejection paths.

### Milestone 4 — Testing and Reliability

**Status: COMPLETE**

Goal: test correctness under failures, concurrency, and persistence changes.

- [x] Review test coverage by API endpoint, policy rule, and workflow transition (reviewed API routes in `app/main.py` and `app/chain/router.py` against `tests/test_api.py`, `tests/test_chain_api.py`, and `tests/test_workflow_transitions.py`; focused endpoint and policy-boundary gaps were covered in PRs #101–#110).
- [x] Add explicit regression coverage for risk-score amount and projected daily-spend thresholds (PR #89).
- [x] Add concurrency tests for competing proposals/reservations and idempotent retries where practical (PRs #48, #20, #66, and #84).
- [x] Test database initialization, additive migrations, and compatibility with an existing database (PRs #36, #67, #68, #78, and #86).
- [x] Test recovery/error behavior for malformed or inconsistent persisted data (PRs #49, #50, #68, and #70–#82).
- [x] Add CI automation for tests and Ruff (PR #35).
- [x] Cover successful read-only token inspection responses (PR #93), invalid token addresses before RPC initialization (PR #95), and missing/invalid owner authentication before RPC initialization (PR #96).
- [x] Test lower-bound validation for standalone evaluation inputs (PR #98) and policy-engine token decimals outside the supported range (PR #99).
- [x] Keep tests deterministic; avoid live RPC dependence in the unit-test suite (Base Sepolia RPC tests use `httpx.MockTransport`; chain/API tests substitute fake RPC clients, and no live RPC is required by the reviewed unit tests).

**Exit criteria:** automated checks run reproducibly and important failure modes have regression coverage. All listed Milestone 4 criteria have been reviewed and checked off; the focused regression coverage in PRs #101–#112 is merged, and the project owner confirmed both required checks after the latest merges.

### Milestone 5 — Observability and Operations

**Status: COMPLETE**

Goal: make local operation and debugging safer and clearer.

- [x] Review structured logging and request/error observability (PR #114 request metadata logging; PR #121 SQLite operational error handling; existing chain RPC error mappings and regression tests).
- [x] Ensure secrets, bearer tokens, and provider API keys are never logged (request logs use route templates and omit raw URLs/headers; regression tests assert bearer and query secrets are absent).
- [x] Document environment variables, defaults, validation, and safe local binding (`docs/runtime-configuration.md`, PR #119).
- [x] Add a practical health/readiness and troubleshooting guide without overstating what health checks prove (the guide explicitly describes `/health` as a lightweight process/API check, not a database or RPC readiness check).
- [x] Document SQLite backup, restore, and local data-reset expectations (`docs/sqlite-backup-restore.md`, PR #120).
- [x] Review operational behavior when the database or RPC endpoint is unavailable (SQLite `OperationalError` returns a sanitized HTTP 503; chain configuration failures return 503 and RPC request failures return 502; regression coverage exercises these paths).

**Exit criteria:** operators can configure, run, diagnose, and reset the local prototype safely. The listed criteria were reviewed against the current implementation, documentation, and regression tests; PRs #114 and #119–#121 complete the observability and operations work. This remains a local simulation-only prototype, not a production deployment claim.

### Milestone 6 — Security Review

**Status: COMPLETE**

Goal: conduct a deliberate review of attack surfaces and documented guarantees.

- [x] Maintain a concise threat model and assets/trust-boundary inventory (`docs/threat-model.md`; PR #26, with later review).
- [x] Review authentication/authorization on every endpoint; add a protected-endpoint missing/wrong-role token matrix (PR #123).
- [x] Review input validation, policy override resistance, replay behavior, and audit integrity assumptions against the implementation and existing regression coverage; add execution-time policy revalidation tests (PR #124).
- [x] Add/review adversarial regression tests for persisted-data tampering, invalid states, oversized inputs, authorization, and policy changes (including PRs #123–#124 and earlier workflow-hardening PRs).
- [x] Review dependency declarations and secret-management conventions (PR #125); configure weekly Python and GitHub Actions Dependabot updates (PRs #126 and #135).
- [x] Document findings, mitigations, residual risks, and out-of-scope production controls (`docs/threat-model.md`, `docs/security-dependency-review.md`).
- [x] Verify repository-level secret scanning and push protection settings; the repository owner confirmed both are enabled on 2026-10-12 (documented in `docs/security-dependency-review.md`).

**Exit criteria:** findings are tracked and addressed or explicitly accepted; no unsupported production-security claims are made.

**Completion record:** The security-review checklist is complete. Findings and residual risks are documented in `docs/threat-model.md` and `docs/security-dependency-review.md`. PRs #139–#142 completed the dependency audit, repository secret-protection documentation, locked dependency workflow, and security-review tracking updates. This completion applies to the simulation-only prototype; it is not an independent security audit or production-readiness certification.

### Milestone 7 — Documentation and Release

**Status: IN PROGRESS**

Goal: package the prototype as a clear, reproducible portfolio/demo project.
- [x] Keep README setup instructions aligned with actual code and configuration (PR #144 aligns setup and run commands with the locked `uv` workflow).
- [x] Add architecture/workflow diagrams if they improve understanding (`docs/workflow-state-transitions.md` state-transition diagram; PR #150).
- [x] Document API examples, approval semantics, hard blocks, and simulation-only execution (README workflow endpoint reference, `docs/demo-scenario.md`, `docs/python-client.md`, and `docs/workflow-state-transitions.md`).
- [x] Record known limitations and a release checklist (`docs/release-checklist.md`; PR adds the release gate and documents prototype limitations).
- [x] Verify a clean setup from a fresh checkout of the final release candidate. On 2026-10-15, a separate checkout at `59ac35e28237a988dbdde7a9f33a76857f0d2c83` completed `uv sync --locked --extra dev`, the full test suite (383 passed; one Starlette/httpx deprecation warning), `pip-audit --skip-editable` (no known vulnerabilities; the editable project distribution is intentionally skipped), and `uv run --locked ruff check .`. The audit was rerun with `VIRTUAL_ENV` unset and `PIPAPI_PYTHON_LOCATION` explicitly set to the disposable checkout interpreter. GitHub Actions `Python checks` passed for PR #165 head `c520a236deadb3fb49a1b8dfa5f0b750fc5f3702` (run #38742564410). The merge commit is documentation-only; run the full release gate against the exact final tagged commit before tagging.
- [x] Run Gitleaks against Git history and resolve the public demo contract false positive (PR #155; owner reported Gitleaks passed on the post-merge `main` checkout on 2026-10-12; release checklist records the precise scope and limitation).
- [x] Review the read-only market-data addition for scope and documentation (PR #159: owner-authenticated endpoint, fixed HTTPS providers, bounded timeouts, validated quote data, stale flags, sanitized upstream errors, and no wiring into transaction or policy decisions; equity data is documented as daily-close information). PR #161 adds malformed-response and upstream-failure regression tests; PR #163 explicitly disables redirects for both fixed-host provider clients.
- [ ] Tag/version only after the release criteria and checks are satisfied.

**Exit criteria:** a reviewer can understand the architecture, run the demo, inspect tests, and distinguish implemented guarantees from limitations.

### Milestone 8 — Optional production-readiness investigation

**Status: OUT OF CURRENT SCOPE — separate decision required**

This would be a separate design/security project, not a simple continuation of the simulation prototype. It may involve secure key custody, transaction signing, nonce management, submission idempotency, receipt reconciliation, RPC trust, chain reorgs, monitoring, incident response, and independent security review. Do not implement real signing or submission unless the project owner explicitly authorizes this new scope after a dedicated design review.

## 4. Iteration estimate

As of PR #163, Milestones 1–6 are complete and only the final release gates in Milestone 7 remain. A reasonable estimate is **about 2–4 additional focused PRs** to reach the tagged simulation-only demo/portfolio release, assuming the final clean-checkout and dependency-audit checks pass without finding issues that need fixes. This estimate excludes the verification work itself, which is required but does not necessarily need a PR.

Likely remaining work:

- Keep the tracker and release evidence aligned with the latest merged changes (one focused documentation PR where needed).
- Resolve any issues uncovered by the final fresh-checkout, test, lint, or dependency-audit run (zero to two PRs depending on findings).
- Prepare intentional release metadata/version and release notes, then tag only after every required release gate is satisfied.

This is a planning range, not a promise. Milestone 8 remains optional, out of scope, and would require a separate decision.

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
| #17 | Add canonical project tracker and AI handoff workflow | Merged |
| #18 | Cover malformed/missing/future approval timestamps and expiry boundaries | Merged |
| #19 | Reject whitespace-only idempotency keys | Merged |
| #20 | Test concurrent idempotency retries under SQLite write locking | Merged |
| #21 | Test transaction reservation accounting across workflow states and UTC day boundaries | Merged |
| #22 | Redact idempotency keys from replay audit events | Merged |
| #23 | Document and test workflow state transitions | Merged |
| #24 | Reject blank database paths and identical agent/owner authentication tokens | Merged |
| #25 | Reject malformed JSON-RPC response shapes and test sanitized 502 errors | Merged |
| #26 | Document threat model and trust boundaries | Merged |
| #27 | Reject malformed token metadata RPC results safely | Merged |
| #28 | Reject invalid JSON-RPC response envelopes | Merged |
| #29 | Sanitize errors from malformed persisted workflow data | Merged |
| #30 | Mark Milestone 2 complete in project tracker | Merged |
| #31 | Close repository review validation gaps | Merged |
| #32 | Document reproducible simulation demo scenario | Merged |
| #33 | Add authenticated Python proposal client example | Merged |
| #34 | Document expired-approval demo path | Merged |
| #35 | Add automated pytest and Ruff checks | Merged |
| #36 | Test additive migration of legacy workflow databases | Merged |
| #37 | Test owner-only transaction endpoint authentication | Merged |
| #38 | Test missing and invalid owner endpoint authentication | Merged |
| #39 | Test owner authentication on read-only chain endpoints | Merged |
| #40 | Validate token addresses before RPC client creation | Merged |
| #41 | Test token endpoint RPC error mapping | Merged |
| #42 | Test wrong-chain Base Sepolia status response | Merged |
| #43 | Validate Base Sepolia contract bytecode responses | Merged |
| #44 | Update project tracker through PR #43 | Merged |
| #45 | Correct simulated execution status in demo guide | Merged |
| #46 | Test 404 responses for missing transaction reads | Merged |
| #47 | Test validation of malformed approval requests | Merged |
| #48 | Test concurrent transaction spend reservations | Merged |
| #49 | Test malformed persisted reason data handling | Merged |
| #50 | Test persisted reason list item validation | Merged |
| #51 | Test malformed JSON-RPC response envelopes | Merged |
| #52 | Validate ERC-20 decimals ABI responses | Merged |
| #53 | Validate malformed ERC-20 symbol ABI responses | Merged |
| #54 | Test malformed ERC-20 ABI string offsets | Merged |
| #55 | Test documented demo token hard block | Merged |
| #56 | Test documented approval-required demo path | Merged |
| #57 | Test documented allowed demo execution path | Merged |
| #58 | Test documented demo hard-blocked path | Merged |
| #59 | Test documented expired-approval demo path | Merged |
| #60 | Test documented demo idempotency replay and conflict | Merged |
| #61 | Verify documented demo paths with regression tests | Merged |
| #62 | Test recipient daily spending limit boundaries | Merged |
| #63 | Test recipient transaction limit boundary | Merged |
| #64 | Test idempotency replay after simulated execution | Merged |
| #65 | Test simulated execution terminal state | Merged |
| #66 | Test concurrent duplicate approval requests | Merged |
| #67 | Test idempotent workflow database migration | Merged |
| #68 | Test migration with malformed legacy proposal data | Merged |
| #69 | Mark Milestone 3 complete and Milestone 4 in progress | Merged |
| #70 | Validate persisted proposal types before workflow use | Merged |
| #71 | Verify spending accounting fields against persisted proposals | Merged |
| #72 | Handle overflowing legacy values during SQLite migration | Merged |
| #73 | Update project tracker through PR #72 | Merged |
| #74 | Validate persisted workflow metadata | Merged |
| #75 | Validate legacy proposals before accounting backfill | Merged |
| #76 | Validate persisted workflow lifecycle field types | Merged |
| #77 | Test malformed lifecycle data on audit endpoint | Merged |
| #78 | Test partially applied SQLite schema migrations | Merged |
| #79 | Update project tracker through PR #78 | Merged |
| #80 | Test agent token rejection on audit endpoint | Merged |
| #81 | Reject malformed persisted audit metadata | Merged |
| #82 | Reject unknown persisted audit values | Merged |
| #83 | Update project tracker through PR #82 | Merged |
| #84 | Test concurrent idempotency key conflicts | Merged |
| #85 | Update project tracker through PR #84 | Merged |
| #86 | Test fresh workflow database initialization | Merged |
| #87 | Update project tracker through PR #86 | Merged |
| #88 | Test negative recipient daily spend rejection | Merged |
| #89 | Test risk score threshold boundaries | Merged |
| #90 | Update project tracker through PR #89 | Merged |
| #91 | Test standalone evaluation endpoint input bounds | Merged |
| #92 | Test evaluation endpoint projected-spend responses | Merged |
| #93 | Test successful read-only token API responses | Merged |
| #94 | Update project tracker through PR #93 | Merged |
| #95 | Test invalid token address rejection before RPC | Merged |
| #96 | Test owner authentication for token inspection | Merged |
| #97 | Update project tracker through PR #96 | Merged |
| #98 | Test lower bounds for evaluation inputs | Merged |
| #99 | Test policy token decimals range | Merged |
| #100 | Record merged validation coverage PRs 97–99 | Merged |
| #101 | Test API chain ID upper bounds | Merged |
| #102 | Test proposal chain and decimals bounds | Merged |
| #103 | Test proposal endpoint rejects zero amount | Merged |
| #104 | Test recipient-specific transaction limit boundary | Merged |
| #105 | Test global daily spending limit boundary | Merged |
| #106 | Test approval threshold boundary | Merged |
| #107 | Test case-insensitive recipient allowlist | Merged |
| #108 | Test recipient token allowlist normalization | Merged |
| #109 | Test successful transaction read endpoint | Merged |
| #110 | Test global token address normalization | Merged |
| #111 | Record Milestone 4 test coverage review | Merged |
| #112 | Test case-insensitive risk recipient matching | Merged |
| #113 | Mark Milestone 4 complete and Milestone 5 in progress | Merged |
| #114 | Add sanitised structured HTTP request logging | Merged |
| #115 | Harden authentication headers and proposal inputs | Merged |
| #116 | Count daily spend by simulated execution date | Merged |
| #117 | Optimize indexed daily spend accounting | Merged |
| #118 | Prevent audit event replacement bypass | Merged |
| #119 | Document runtime configuration and safe local startup | Merged |
| #120 | Document SQLite backup, restore, and safe reset | Merged |
| #121 | Return sanitized 503 responses for SQLite operational failures | Merged |
| #122 | Mark Milestone 5 complete and record merged operations work | Merged |
| #123 | Test authorization across protected API endpoints | Merged |
| #124 | Test policy changes before simulated execution | Merged |
| #125 | Document dependency and secret-management review | Merged |
| #126 | Configure weekly Python dependency updates | Merged |
| #127 | Update pytest development dependency range | Merged |
| #128 | Record security review progress through PR #126 | Merged |
| #129 | Test protected endpoints fail closed on invalid auth configuration | Merged |
| #130 | Test owner-only authentication on read-only chain endpoints | Merged |
| #131 | Scope accounting integrity checks to relevant spend | Merged |
| #132 | Normalize legacy recipient accounting keys consistently | Merged |
| #133 | Log unhandled request failures safely without exposing exception details | Merged |
| #134 | Record security review progress through PR #133 | Merged |
| #135 | Configure weekly GitHub Actions dependency updates | Merged |
| #136 | Update GitHub Actions `setup-python` to v7 | Merged |
| #137 | Update GitHub Actions `checkout` to v7 | Merged |
| #138 | Record security review progress through PR #135 | Merged |
| #139 | Audit installed Python dependencies in CI with `pip-audit` | Merged |
| #140 | Document repository secret scanning and push protection settings | Merged |
| #141 | Add reproducible Python dependency lockfile and locked CI installation | Merged |
| #142 | Record locked dependency workflow and close out security-review documentation | Merged |
| #143 | Mark Milestone 6 complete and begin Milestone 7 | Merged |
| #144 | Align README and runtime setup with the locked `uv` workflow | Merged |
| #145 | Record Milestone 7 progress and README setup alignment | Merged |
| #146 | Align demo walkthrough with the locked `uv` workflow | Merged |
| #147 | Add release checklist and document known limitations | Merged |
| #148 | Align Python client walkthrough with the locked `uv` workflow | Merged |
| #149 | Align tracker with current workflow and restore merged PR history | Merged |
| #150 | Add workflow state-transition diagram | Merged |
| #150 | Add workflow state-transition diagram | Merged |
| #151 | Record successful fresh-checkout verification for Milestone 7 | Merged |
| #152 | Record completed baseline release checks | Merged |
| #153 | Improve demo token setup and record walkthrough verification | Merged |
| #154 | Record release CI and threat model review | Merged |
| #155 | Suppress public demo contract false positive and clarify variable name | Merged |
| #156 | Record Gitleaks history review | Merged |
| #157 | Refresh final release validation evidence | Merged |
| #158 | Add local Aegis frontend control centre | Merged |
| #159 | Add shared read-only market-data providers for crypto and equities | Merged |
| #160 | Align release gate with market-data integration | Merged |
| #161 | Test malformed market-data provider responses and upstream failures | Merged |
| #162 | Add reproducible final-candidate verification steps | Merged |
| #163 | Reject redirects from market-data providers | Merged |
| #164 | Refresh tracker for final release stage | Merged |
| #165 | Record final checkout verification evidence | Merged |

## 6. Current architecture and useful code locations

- `app/main.py`: FastAPI models, endpoints, authentication dependencies, API error mapping, demo policy, application version.
- `app/policy/models.py`: policy and trusted-token data/configuration validation.
- `app/policy/engine.py`: deterministic policy decisions and hard blocks.
- `app/risk/engine.py` and `app/risk/models.py`: heuristic risk scoring.
- `app/workflow/controller.py`: workflow orchestration, policy/risk evaluation, approval/execution control.
- `app/workflow/store.py`: SQLite persistence, locking, spend reservation, idempotency, fingerprints, audit trail, expiry.
- `app/chain/`: read-only Base Sepolia chain integration.
- `app/market_data.py` and `docs/market-data.md`: shared read-only crypto/equity quote providers, endpoint behavior, freshness semantics, and limitations.
- `tests/test_api.py`: API, policy, workflow, and security regression tests.
- `pyproject.toml`: package version, dependencies, pytest and Ruff configuration.
- `README.md`: setup, API behavior, configuration, and safety boundaries.

Verify paths and current implementation before relying on this summary; code is the final authority.

## 7. AI/new-chat handoff checklist

At the start of a new chat or when switching AI assistants:

1. Read this file from the latest `main`.
2. Inspect the current GitHub PR list and repository state; do not assume the PR numbers or branch state in a past conversation are current.
3. Check for any open PR; once the project owner reports that both local checks passed, automatically follow the squash-merge protocol in Section 2 without asking for separate merge approval.
4. Confirm the latest merged milestone/PR and current local branch status with the project owner if not evident.
5. Pick one small next task from the current milestone, inspect relevant code/tests, and explain its scope before editing.
6. Create a branch from current `main`; make one focused change with regression tests as appropriate.
7. Open a PR and provide exact local validation commands.
8. Follow the merge gate and post-merge sync protocol in Section 2.
9. Update this document in a later focused PR when status changes; do not mark planned work complete merely because it was proposed or coded on an unmerged branch.

**If conversation history conflicts with this file, inspect the current repository and merged PRs.** Update this document through a PR when the durable project state changes.
