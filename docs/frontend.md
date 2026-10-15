# Local Frontend Control Centre

The dependency-free frontend is served by the existing FastAPI application at `/`. It uses the same origin as the API and adds no JavaScript package manager, build step, or frontend dependency.

## Start

Follow the [runtime configuration guide](runtime-configuration.md), including generating distinct local agent and owner bearer tokens. Start the API from the repository root:

~~~bash
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000
~~~

Open http://127.0.0.1:8000/. The UI and API share an origin; no CORS configuration is needed. Keep the server bound to `127.0.0.1`.

## Connect

Choose **Connect to local API** and enter the separate development agent and owner tokens. The UI checks the public health endpoint and verifies the owner token by requesting the latest workflow records. There is no agent-token verification endpoint; the agent token is tested when a proposal is submitted. The UI communicates this distinction.

Credentials are held in JavaScript memory only. They are not written to local storage, session storage, cookies, URLs, or logs, and are cleared by **Disconnect** or a page reload. This is a local development authentication model, not production identity or session management.

## Views and supported workflow

- **Overview:** counts and recent records from the latest 50 records returned by the API, pending approvals, recent audit events, API status, and a small market snapshot. Counts are not presented as lifetime totals.
- **Markets:** owner-authenticated read-only quotes for BTC, ETH, SOL and a small US-equity watchlist (AAPL, MSFT, NVDA). Quote values, provider, timestamps, and stale status come from the API. Equities are identified as daily-close data. Quotes are informational, not executable prices, holdings, or portfolio valuations.
- **Proposals:** submit the current demo policy's Base Sepolia USDC proposal. The amount is entered in USDC and converted to six-decimal base units. The recipient is checked for EVM address shape in the browser; backend policy and schema validation remain authoritative. The resulting policy decision, heuristic risk score, reasons, workflow ID, and status are shown from the API response.
- **Approvals:** view persisted proposals in `awaiting_approval` and approve only by sending the exact returned fingerprint with the API's required `APPROVE` confirmation. Hard-blocked proposals are not offered for approval. The current transaction response does not expose an absolute approval-expiry timestamp; the UI does not estimate one, and it displays the backend's known expiry/conflict response when an action is rejected.
- **Activity & Audit:** search the latest 50 workflow records, request owner-authorized simulated execution only for `ready` or `approved` states, and inspect persisted audit events and state transitions. A successful execution result is labelled `Simulated`; it is not a transaction hash, chain receipt, broadcast, or on-chain confirmation.
- **Settings:** inspect API and owner-authentication state, reconnect, or clear the in-memory credentials.

The proposal form is intentionally bounded to the current demo policy: chain ID 84532, Circle's configured Base Sepolia USDC contract, six decimals, and a maximum amount of 50 USDC. These defaults do not configure live-trading controls. The API may apply other policy constraints, such as recipient and daily limits.

## Validation

There is no separate frontend build or package command. From the repository root, run:

~~~bash
python -m pytest -q
ruff check .
~~~

The frontend tests check the served page structure, supported API operations, credential-handling indicators, and simulation-only wording. When Node.js is available, focused regression tests also execute selected async frontend functions against mocked API and DOM dependencies to exercise stale connections, disconnects, and failure handling. These tests are not a full browser or end-to-end security assessment.

## Safety and limitations

- The UI does not accept private keys or seed phrases and has no signing, transaction-submission, broker, or exchange-order code.
- Approval and execution outcomes are shown only after API confirmation. Backend validation, policy blocks, expiry, and state transitions remain authoritative.
- The risk score is an explainable heuristic, not a validated fraud detector.
- Market data is disconnected from policy decisions and execution. Crypto quotes may be delayed or stale; equity prices are daily closes. Provider availability and rate limits apply.
- The workflow list is limited to the latest 50 API results. Audit summaries request events for only the latest workflows and do not imply complete lifetime history.
- The audit view displays application audit records. SQLite audit controls provide application-level tamper resistance, not cryptographic proof against someone controlling the database file.
- Use only with the local prototype and keep the API bound to `127.0.0.1`; browser-held credentials are still exposed to the local browser session.
