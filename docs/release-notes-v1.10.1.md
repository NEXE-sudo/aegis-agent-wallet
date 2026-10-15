# Aegis Agent Wallet v1.10.1 — Corrective Patch Release (Draft)

**Release scope:** simulation-only AI-agent wallet workflow prototype.

## Why this patch release exists

The existing published `v1.10.0` tag resolves to `4c9c9431dca31d15137501cb99b026fcac829236`. The newer implementation candidate `154185cc0378448fa0fa4543762d85ead02bdff7` is nine commits ahead and includes the local frontend control centre and read-only crypto/equity market-data integration. The published tag will remain unchanged; this corrective patch release versions the newer implementation as `v1.10.1`.

## Highlights

- Policy evaluation, explainable heuristic risk scoring, explicit human approval, and simulated execution.
- Persistent workflow state, replay/idempotency protection, spending reservations, and audit events.
- Read-only Base Sepolia token/contract inspection; no private-key loading, transaction signing, or transaction submission.
- Local frontend control centre.
- Read-only crypto/equity market-data endpoint with fixed HTTPS providers, bounded timeouts, input/response validation, stale-data indicators, sanitized upstream errors, and redirect blocking.
- Locked Python dependencies, automated tests and linting, dependency auditing, threat model, and reproducible verification instructions.

## Verification

The candidate `154185cc0378448fa0fa4543762d85ead02bdff7` passed fresh-checkout verification on 2026-10-15:
- Locked dependency sync: passed.
- Tests: 383 passed; one Starlette/httpx deprecation warning.
- Ruff: passed.
- `pip-audit --skip-editable`: no known vulnerabilities found; the editable project distribution was skipped intentionally.

**Release gate still required:** after this version bump is merged, repeat the full fresh-checkout verification against the exact commit intended for the `v1.10.1` tag. Do not treat the evidence above as verification of that future commit.

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
