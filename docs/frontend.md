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

- **Overview:** counts and recent records from the latest 50 records returned by the API, pending approvals, recent audit events, API status, a small market snapshot, daily workflow spending, and on-demand read-only Base Sepolia ETH/demo-USDC balances for a public address. Counts are not presented as lifetime totals.
- **Network status:** the owner-authenticated Overview panel checks `GET /chain/base-sepolia/status` on demand and displays the RPC-reported chain ID against expected Base Sepolia ID 84532. This check does not prove vault deployment, token authenticity, or transaction finality.
- **Vault configuration:** the owner-authenticated Overview panel checks `GET /chain/base-sepolia/vault/status` on demand and shows the configured vault address, reported owner/agent, pause state, and differences between the on-chain token/recipient policy and the current demo policy. The endpoint requires a configured vault address and Base Sepolia RPC, and rejects an address with no contract code. This point-in-time RPC read is not an independent contract audit or proof of source-code authenticity.
- **Wallet balances:** enter a public EVM address to request its native ETH and demo USDC balances from the owner-authenticated `GET /chain/base-sepolia/address/{address}/balances` endpoint. The view requires the configured Base Sepolia RPC URL, checks chain ID 84532, displays exact API-formatted units, and never asks for signing material or initiates a transaction.
- **Markets:** owner-authenticated read-only quotes for BTC, ETH, SOL and a small US-equity watchlist (AAPL, MSFT, NVDA). Quote values, provider, timestamps, and stale status come from the API. Equities are identified as daily-close data. Quotes are informational, not executable prices, holdings, or portfolio valuations.
- **Proposals:** submit the current demo policy's Base Sepolia USDC proposal. The amount is entered in USDC and converted to six-decimal base units. The recipient is checked for EVM address shape in the browser; backend policy and schema validation remain authoritative. The resulting policy decision, heuristic risk score, reasons, workflow ID, and status are shown from the API response.
- **Approvals:** view persisted proposals in `awaiting_approval`, approve only by sending the exact returned fingerprint with the API's required `APPROVE` confirmation, or cancel a pending reservation. Hard-blocked proposals are not offered for approval. For approved transactions, the API returns `approval_expires_at`, derived from the persisted `approved_at` timestamp and configured approval lifetime; Activity displays that deadline and flags it as elapsed when appropriate. The backend remains authoritative and rejects expired approvals even if the UI has not refreshed.
- **Activity & Audit:** search the latest 50 workflow records, cancel `ready`, `awaiting_approval`, or `approved` proposals to release their daily reservations, execute eligible proposals as owner under the API's reported mode, retry an uncertain testnet submission using the same signed bytes, read a Base Sepolia receipt status, and inspect persisted audit events and state transitions. Approved activity rows show the API-derived approval deadline. Receipt checks show the reported transaction hash, chain, status, and block inline in the activity row for the current page session; the result is a point-in-time RPC observation and does not prove finality. Simulated execution is explicitly labelled as not broadcast; testnet submission shows its transaction hash and does not imply confirmation or finality.
- **Settings:** inspect API and owner-authentication state, reconnect, or clear the in-memory credentials. The active policy panel reads `GET /policy/config` with the owner token and displays allowed chains/tokens/recipients, spending thresholds, and approval rules without offering policy mutation controls.

The proposal form is intentionally bounded to the current demo policy: chain ID 84532, Circle's configured Base Sepolia USDC contract, six decimals, and a maximum amount of 50 USDC. These defaults do not configure live-trading controls. The API may apply other policy constraints, such as recipient and daily limits.

## Validation

There is no separate frontend build or package command. From the repository root, run:

~~~bash
uv sync --locked --extra dev
uv run --locked playwright install chromium
uv run --locked python -m pytest -q
uv run --locked ruff check .
~~~

The frontend tests check the served page structure, supported API operations, credential handling, and mode-aware execution wording. Focused browser regressions use Playwright with locally mocked API responses to exercise the connection dialog, responsive layout, and escaping of API-provided content. They do not replace a full security assessment.

## Safety and limitations

- The UI never accepts private keys or seed phrases. In default simulated mode it does not sign or broadcast; testnet signing is performed locally by the backend only after explicit configuration and owner execution.
- Approval and execution outcomes are shown only after API confirmation. Backend validation, policy blocks, expiry, and state transitions remain authoritative.
- The UI supports simulated execution and `base_sepolia_vault` only. Vault mode submits through the AegisVault on Base Sepolia; direct EOA execution is disabled. A successful RPC submission and receipt status do not prove finality; the owner-only receipt API reads the reported pending/success/failed state.
- The risk score is an explainable heuristic, not a validated fraud detector.
- Market data is disconnected from policy decisions and execution. Crypto quotes may be delayed or stale; equity prices are daily closes. Provider availability and rate limits apply.
- The workflow list is limited to the latest 50 API results. Audit summaries request events for only the latest workflows and do not imply complete lifetime history.
- The audit view displays application audit records. SQLite audit controls provide application-level tamper resistance, not cryptographic proof against someone controlling the database file.
- Use only with the local prototype and keep the API bound to `127.0.0.1`; browser-held credentials are still exposed to the local browser session.
