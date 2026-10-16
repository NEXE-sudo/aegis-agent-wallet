# Aegis Agent Wallet v1.10.2 — Corrective Patch Release (Draft)

**Release scope:** simulation-only AI-agent wallet workflow prototype.

## Highlights

- Add owner-authenticated cancellation of pending proposals, releasing reserved daily budget and recording the cancellation in the audit trail.
- Add owner-authenticated rejection of pending proposals, releasing reserved daily budget and recording rejection in the audit trail.
- Accept Stooq quotes for the current session date without treating the end-of-day timestamp as a future quote; retain staleness checks for older data and rejection of genuinely future dates.
- Correct same-day equity quote handling for Stooq session dates.
- Clarify that readable ERC-20 metadata is not token verification and cap metadata string size.
- Explicitly close SQLite connections and consolidate duplicated approval-expiry logic.
- Add a nonce-based Content Security Policy and browser security headers to the local frontend.
- Document the policy-oracle exposure of the intentionally unauthenticated standalone evaluation endpoints.
- Test the Python 3.11 minimum and Python 3.14 in CI.

## Verification

Focused tests for the changes above passed. The complete test suite and exact-commit fresh-checkout release gate must pass before publishing or tagging this draft.

## Safety boundary

This release does **not** load private keys, sign transactions, or submit transactions. Base Sepolia integration remains read-only and transaction execution remains simulated. Real execution is out of scope and requires a separate explicit design and security review.

---

**Draft status:** Do not publish or tag until the complete release checklist passes against the exact final commit.
