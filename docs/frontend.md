# Local Frontend Control Centre

The frontend is a dependency-free browser UI served by the existing FastAPI app. It is designed for the current Aegis simulation-only workflow; it does not implement market feeds, stock/crypto trading, or real blockchain execution.

## Start

Follow the [runtime configuration guide](runtime-configuration.md), including generating distinct agent and owner bearer tokens. Start the API from the repository root:

~~~bash
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000
~~~

Open http://127.0.0.1:8000/. The frontend and API share the same origin, so no CORS configuration is needed.

## Connect

Choose **Connect to local API** and enter the agent and owner bearer tokens for that running process. The agent token authorizes proposal creation. The owner token authorizes listing and reading transactions, approvals, simulated execution, and audit retrieval. Tokens remain in JavaScript memory only; they are not written to local storage or cookies and are cleared on reload. This is a local development workflow, not production authentication.

## Supported workflow

- View persisted transaction counts and recent records.
- Create a proposal for the current demo policy's Base Sepolia USDC asset.
- Review policy decisions, risk scores, and risk explanations.
- Approve a proposal using its exact server-returned fingerprint.
- Request simulated execution, which revalidates policy/risk on the backend.
- Read owner-authenticated audit events for a transaction.
- Inspect a hard block and verify that the UI does not offer an approval override.

The transaction form is intentionally bounded to the current demo policy: chain ID 84532, the configured Base Sepolia USDC contract, six decimals, and a maximum amount of 50 USDC. These are prototype defaults, not configurable live-trading controls.

## Safety and limitations

- The UI never accepts private keys and has no signing or submission code.
- A successful execution response means executed_simulated, not an on-chain receipt or transaction hash.
- The displayed risk score is the backend's explainable heuristic, not a validated fraud model.
- The approval queue reflects persisted workflow state; it does not imply a real financial balance or brokerage position.
- Local bearer tokens are exposed to the browser session because the browser sends authenticated API requests. Use only with the local prototype, keep the server bound to 127.0.0.1, and never paste production credentials.
- Market data, equity brokers, crypto exchange order routing, and paper-trading portfolio accounting are future, separate scope.
