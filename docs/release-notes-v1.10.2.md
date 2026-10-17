# Aegis Agent Wallet v1.10.2 — Corrective Patch Release

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

The repository contains annotated tag `v1.10.2` at commit
`1a3095e0fcd95b2dfb200aeef29c1bb395a88593`, whose package version is `1.10.2`.
The historical verification entries in the release checklist refer to their
recorded commit SHAs; they should not be interpreted as verification of this tag
unless the SHA matches.

## Safety boundary

This release does **not** load private keys, sign transactions, or submit transactions. Base Sepolia integration remains read-only and transaction execution remains simulated. Real execution is out of scope and requires a separate explicit design and security review.

---

**Repository status:** Tagged as `v1.10.2`. This repository evidence does not
establish whether a corresponding GitHub Release was published.
