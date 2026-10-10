# Authenticated Python client example

This example submits a **proposal only** to the local Aegis API. It does not approve, sign, or execute transactions. Any later execution through the API remains simulated.

## 1. Start the API

From the repository root, activate your virtual environment and configure distinct local agent and owner tokens as described in the [README setup](../README.md#macos-setup). Start the API:

```bash
uvicorn app.main:app --reload
```

## 2. Configure and run the client

In another terminal using the same environment, set the agent token and run:

```bash
export AEGIS_AGENT_TOKEN="your-local-agent-token"
python examples/propose_transaction.py
```

The script sends `POST /transactions/propose` with the agent bearer token and prints the returned transaction record. Defaults match the repository's local demo policy: Base Sepolia (chain ID `84532`), the demo USDC testnet contract, the configured placeholder recipient, and `1_000_000` base units (1 USDC at 6 decimals).

You can override the example inputs with environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `AEGIS_API_URL` | `http://127.0.0.1:8000` | Local API base URL |
| `AEGIS_AGENT_TOKEN` | Required | Agent bearer token; never commit or print it |
| `AEGIS_AGENT_ID` | `devops-01` | Demo agent ID |
| `AEGIS_CHAIN_ID` | `84532` | Chain ID checked by demo policy |
| `AEGIS_TOKEN_SYMBOL` | `USDC` | Token symbol |
| `AEGIS_TOKEN_ADDRESS` | Demo USDC address | Token contract address |
| `AEGIS_RECIPIENT` | Configured placeholder | Recipient address |
| `AEGIS_AMOUNT_BASE_UNITS` | `1000000` | Positive amount in token base units |
| `AEGIS_TOKEN_DECIMALS` | `6` | Token decimals |
| `AEGIS_IDEMPOTENCY_KEY` | Not sent | Optional key to make retries replay-safe |

For example, set `AEGIS_IDEMPOTENCY_KEY` to the same 8–128 character value when retrying the same payload. Do not reuse that key for a different proposal; the API returns HTTP `409` on a payload conflict. Without this variable, each run is a new proposal.

The client requires the project's existing `httpx` dependency; no additional package is needed. A non-success API response or connection error is reported to stderr with a non-zero exit code.

## Safety boundary

Use only local/demo inputs. Keep bearer tokens in environment variables and keep the API bound to localhost. This example never calls approval or execution endpoints, loads no private keys, signs no transactions, and submits nothing to a blockchain. A successful response means only that the API recorded or replayed a proposal.
