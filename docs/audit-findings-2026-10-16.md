# Aegis Agent Wallet: Audit Findings

## Current status of original findings

| # | Status | Evidence |
| --- | --- | --- |
| 1 | Resolved | Same-day Stooq dates are clamped to the current time in `app/market_data.py`; regression tests cover today's session date. |
| 2 | Resolved | Owner-only reject and cancel endpoints release reservations (`app/main.py`, `app/workflow/store.py`); see fix commit `c70ed29`. |
| 3 | Resolved | Public standalone evaluation routes are an accepted demo design choice documented in `docs/threat-model.md`, with explicit public-route tests. |
| 4 | Resolved | Token inspection uses `erc20_metadata_readable`, caps decoded metadata, and warns that readability does not establish authenticity (`app/chain/base_sepolia.py`). |
| 5 | Resolved | SQLite operations use `contextlib.closing` around connections throughout `app/workflow/store.py`. |
| 6 | Resolved | Confirmed as an intentional conservative rule, not a defect: changed risk while HIGH requires fresh approval, as described in `docs/workflow-state-transitions.md` and tested in `tests/test_api.py`. |
| 7 | Resolved | The served frontend has nonce-based CSP and response security headers in `app/main.py`. |
| 8 | Partially resolved | The redundant replay-time `build_record(0, 0)` check and repeated recipient normalization are absent, but approval-expiry validation remains duplicated in `WorkflowStore.approve` and `_execute_with_validation` (`app/workflow/store.py`). |
| 9 | Partially resolved | Version metadata and frontend label now say 1.10.3, and 1.10.2 notes exist; `CHANGELOG.md` still labels 1.10.2 unreleased, and release-formatting concerns remain. |

- The findings and baseline results below are retained from the original audit
  snapshot; the table above records their disposition against the current source.
- **Original audit snapshot:** NEXE-sudo/aegis-agent-wallet, `main` (version 1.10.2)
- **Date:** 2026-10-16
- **Scope:** shallow clone of `main` (single commit). Git history was not audited.
- **Method:** `ruff check`, pytest, `pip-audit`, `uv lock --check`, manual read of backend, frontend, docs and CI, plus two runtime probes (Stooq mock, reservation lock-up).
- **Not verified:** the 4 Playwright tests in `tests/test_frontend_browser.py` could not run (sandbox blocks the Chromium download).

## Baseline results

| Check | Result |
| --- | --- |
| `pytest` (excluding browser tests) | 391 passed |
| `ruff check .` | clean |
| `uv lock --check` | lockfile in sync |
| `pip-audit --skip-editable` | only the sandbox venv's bundled `pip` flagged; no project dependencies flagged |
| Secret-pattern scan | nothing found |
| Frontend XSS review | all `innerHTML` interpolations use `esc()`; class names come from fixed maps; tokens held in memory only |

## Summary

| # | Severity | Type | Finding |
| --- | --- | --- | --- |
| 1 | Medium | Bug (verified) | Equity quotes fail when the Stooq session date is today |
| 2 | Medium | Design gap (verified) | Agent can exhaust the daily budget; owner has no reject/cancel |
| 3 | Low | Design/security | `/policy/evaluate` and `/risk/assess` are unauthenticated |
| 4 | Low | Semantics | `verified: True` on any readable ERC-20; metadata strings uncapped |
| 5 | Low | Resource handling | SQLite connections never closed |
| 6 | Low | Design observation | Approval invalidated by any risk change while HIGH |
| 7 | Low | Hardening | No CSP or security headers on served page |
| 8 | Low | Code quality | Redundant/duplicated logic |
| 9 | Low | Release hygiene | Version, changelog and stray-file drift |

---

## 1. Equity quotes fail when the Stooq session date is today

**Severity:** Medium. **Status:** verified with a mocked Stooq response.

**Files:**
- `app/market_data.py:175` builds `as_of` as `datetime.combine(session_date, time.max, tzinfo=UTC)`, which is 23:59:59.999999 UTC of the session date.
- `app/market_data.py:80` (inside `_quote`, defined at line 68) rejects any `as_of` more than 5 minutes in the future with "Market-data provider returned a future timestamp".
- `app/main.py:~300` (`get_market_quote`) converts that `MarketDataError` into HTTP 502 "Market-data provider unavailable".
- `frontend/index.html` (market views) shows this as "unavailable".
- `tests/test_market_data.py:207` only covers the future-timestamp case for crypto; no equity test uses a same-day session date.

**Effect:** From market open until midnight UTC, every equity quote returns 502. A session date two days ago works correctly.

**Reproduction:** Mock Stooq returning `Date = <today>`, then `StooqProvider.get_quote("equity", "AAPL")` raises `MarketDataError`.

**Suggested direction:** Clamp same-day `as_of` to now, or derive it from the row's `Time` field with the correct timezone. Add a same-day regression test.

---

## 2. Agent can exhaust the daily budget; owner cannot release reservations

**Severity:** Medium. **Status:** verified via TestClient.

**Files:**
- `app/workflow/store.py:483` and `:516` define `reservation_statuses = ("ready", "awaiting_approval", "approved")`, so pending proposals count toward daily spend (`_daily_spend` at `:475`, `_recipient_daily_spend` near `:505`).
- `app/main.py:267` (`/transactions/propose`, agent-authenticated) creates reservations.
- `app/main.py:308` (`/approve`) and `:334` (`/execute`) are the only state-changing owner routes. No reject, cancel, void or revoke route exists.
- `app/workflow/store.py:684` (`approve`) and `:763` (`execute_simulated`) have no path to a terminal "rejected" state.

**Effect:** Three proposals of 50,000,000 base units (50 USDC) each all land in `awaiting_approval` and reserve the full 150,000,000 daily limit. The fourth proposal is blocked by "Transaction would exceed the daily spending limit". The owner cannot release the reservations until the UTC day rolls over.

**Suggested direction:** Add an owner reject/cancel action with a new status and audit event type that frees the reservation. Optionally expire stale `awaiting_approval` rows. This requires updates to the `AUDIT_EVENT_TYPES`/`AUDIT_STATUSES` sets (`app/workflow/store.py:~15-31`), `_decode` status validation (`:~935`), the triggers and queries listing statuses, and `docs/workflow-state-transitions.md`.

---

## 3. Unauthenticated policy and risk evaluation endpoints

**Severity:** Low. **Documented as intentional.**

**Files:**
- `app/main.py:242` (`POST /policy/evaluate`) and `app/main.py:254` (`POST /risk/assess`) have no auth dependency.
- `docs/threat-model.md:35` documents them as demo endpoints without bearer-token dependencies.

**Effect:** Any caller who can reach the API can bisect the exact per-transaction limit, daily limit and approval threshold, and learn the allowlisted tokens and recipients, by varying amounts and the caller-supplied `daily_spent_base_units`. This is acceptable for a loopback-only prototype but becomes a policy oracle if exposed.

**Suggested direction:** Gate behind `require_owner`, or keep open and note the oracle explicitly in the threat model.

---

## 4. `verified: True` is misleading; metadata strings are uncapped

**Severity:** Low.

**Files:**
- `app/chain/base_sepolia.py:161` returns `"verified": True` for any contract whose `symbol()` and `decimals()` calls succeed (method at `:114`).
- `app/chain/base_sepolia.py:32` (`_decode_abi_string`) places no upper bound on decoded string length beyond the RPC response size.
- `app/chain/router.py` returns the dict unchanged from `GET /chain/base-sepolia/token/{address}`.

**Effect:** The field name implies authenticity or trust, while the accompanying warning text says it establishes neither. A malicious contract can return very long `symbol`/`name` values in the API response.

**Suggested direction:** Rename to `metadata_readable` (or similar) and cap `symbol`/`name` length. Update `tests/test_base_sepolia.py` and `tests/test_chain_api.py` accordingly.

---

## 5. SQLite connections are never explicitly closed

**Severity:** Low.

**Files:** `app/workflow/store.py:67` (`_connect`) and every `with self._connect() as db:` site (`:78`, `:351`, `:548`, plus the `get`, `list_recent`, `approve` and `execute_simulated` methods).

**Effect:** `with sqlite3.Connection` commits or rolls back but does not close. CPython reference counting hides this on 3.11/3.12. From memory (not tested), Python 3.13+ emits `ResourceWarning` for unclosed connections. CI runs 3.11 and `pyproject.toml` allows `>=3.11`.

**Suggested direction:** Wrap with `contextlib.closing(...)` or add a small context-manager helper. Tests passed with `-W error::ResourceWarning` on Python 3.12 (`tests/test_api.py`).

---

## 6. Approval invalidated by any risk change while HIGH

**Severity:** Low. **Design observation, not a defect.**

**Files:** `app/workflow/store.py:859` (`risk_changed`) and `:866`.

**Effect:** An `approved` transaction at execution time reverts to `awaiting_approval` if the recomputed risk score, level or reasons differ at all while the level is HIGH. This includes a decrease from 70 to 60, or a rise caused by another proposal's new reservation. This is safe but can force repeated re-approval.

**Suggested direction:** Consider invalidating only when the risk worsens or when the level changes.

---

## 7. No CSP or security headers on the served frontend

**Severity:** Low. **Defense-in-depth.**

**Files:** `app/main.py:161` (`FastAPI(...)` app setup, no header middleware) and `app/main.py:378` (`frontend_index` returns `FileResponse` with no headers).

**Effect:** The page relies entirely on `esc()` for XSS safety. A CSP would be a second layer. The frontend uses one large inline `<script>`, so a strict CSP would need a nonce or hash.

---

## 8. Redundant or duplicated logic

**Severity:** Low (maintainability).

| Location | Issue |
| --- | --- |
| `app/workflow/store.py:576` | `build_record(0, 0)["fingerprint"]` runs both engines and generates a UUID just to compare a fingerprint that is derived only from the proposal. The two earlier checks already cover the case. |
| `app/workflow/store.py:723` and `:813` | Approval-expiry logic is duplicated in `approve` and `execute_simulated`, with different sources for the expiry value (`approval_expires_seconds` parameter vs `policy.approval_expires_seconds`). |
| `app/policy/engine.py:71` and `:112` | `recipient = _normalise_address(proposal.recipient)` is computed twice. |
| `app/main.py:219` | `_proposal()` re-strips and re-cases fields already canonicalized by the request-model validators. |

---

## 9. Release hygiene

**Severity:** Low.

| File | Issue |
| --- | --- |
| `CHANGELOG.md:3` | Top entry is `## 1.10.0`. No entries for 1.10.1 or 1.10.2, although `pyproject.toml:7`, `app/main.py:164` and `uv.lock` are at 1.10.2. |
| `frontend/index.html:252` | Hardcoded label "v1.10.1 API" while the backend reports 1.10.2. |
| `docs/` | Release notes exist for v1.10.0 and v1.10.1 only; none for 1.10.2. `docs/release-checklist.md` still describes 1.10.2 as upcoming. |
| `aegis-agent-wallet-security-fixes.patch` (43,469 bytes, repo root) | Already fully applied: a forward `git apply --check` fails and a reverse check is clean. Stale artifact. |
| `PROJECT_TRACKER.md` (34,081 bytes, repo root) | Large internal tracker in the repo root; confirm it is meant to ship. |
| `ruff format` | Would reformat 26 of 50 files. `.github/workflows/python-checks.yml` runs only `ruff check`, so formatting is unenforced. |
| `.github/workflows/python-checks.yml` | Runs Python 3.11 only while `pyproject.toml` allows `>=3.11`; newer versions are untested (relevant to finding 5). |

---

## Items reviewed with no issue found

- Auth: constant-time compare, fail-closed on missing or identical tokens (`app/auth.py`).
- Fingerprint verification on idempotent replay, approve and execute (`app/workflow/store.py`).
- Append-only audit triggers and integer-overflow handling (`app/workflow/store.py`, `app/policy/models.py`).
- Chain client response validation and sanitized errors (`app/chain/base_sepolia.py`, `app/chain/router.py`).
- Request logging omits URLs, headers and exception text (`app/main.py`).
- CI: `uv sync --locked`, pinned `uv`/`pip-audit`, action versions exist.
- Secrets: `.env.example` has placeholders only; `.gitignore` excludes `.env*` and `*.sqlite3`.
