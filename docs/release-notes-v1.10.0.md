# Aegis Agent Wallet v1.10.0 — Release Notes (Draft)

**Release scope:** simulation-only AI-agent wallet workflow prototype.

## Highlights

- Policy evaluation and explainable heuristic risk scoring before simulated execution.
- Explicit human approval paths, approval expiry, and revalidation before execution.
- Persisted workflow state, replay/idempotency protection, spending reservations, and an audit trail.
- Read-only Base Sepolia inspection; no private-key loading, transaction signing, or transaction submission.
- Local frontend control centre and documented demo walkthroughs.
- Shared read-only crypto/equity market-data endpoint with fixed HTTPS providers, bounded timeouts, input/response validation, stale-data indicators, sanitized upstream errors, and redirect blocking.
- Locked Python dependency workflow, automated test/lint checks, dependency audit workflow, threat model, and reproducible local verification instructions.

## Verification evidence

The fresh-checkout verification run on commit `59ac35e28237a988dbdde7a9f33a76857f0d2c83` completed:
- Locked dependency sync: passed.
- Tests: 383 passed; one Starlette/httpx deprecation warning.
- Ruff: passed.
- `pip-audit --skip-editable`: no known vulnerabilities reported; the editable project distribution was skipped.

GitHub Actions `Python checks` also passed on PR #165 head `c520a236deadb3fb49a1b8dfa5f0b750fc5f3702` (run #38742564410). These are pre-release evidence, not a substitute for rerunning the full gate against the exact final release commit.

## Known limitations

- Risk scoring is heuristic and is not a validated fraud detector.
- Bearer-token authentication is for local development, not production identity management.
- Persisted spending reservations are a local accounting model, not confirmed on-chain balances or receipts.
- Token metadata and address format do not prove token authenticity or recipient identity.
- SQLite audit protections are not cryptographic tamper-proofing against an operator controlling the database.
- The project is not certified production-ready and has not undergone an independent security audit.

## Safety boundary

This release does **not** load private keys, sign transactions, or submit transactions. Base Sepolia integration is read-only and transaction execution remains simulated. Real execution is out of scope and requires a separate explicit design and security review.

---

**Draft status:** Do not publish or tag until the complete release checklist passes against the exact final commit.
