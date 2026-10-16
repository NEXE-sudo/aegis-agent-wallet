# Aegis Agent and Evaluation Harness

The `agent` package provides a task-driven client for the Aegis API. It has three
tools: submit a transaction proposal, retrieve a market quote, and read a limited
transaction status. Its API client accepts only the agent token; the owner token is
not loaded by the agent, included in an LLM prompt, or sent to the LLM provider.
Proposals do not approve or execute payments.

## Trusted payment inputs

Pass task data as a mapping containing an `authorized_payment` object with exactly
the proposal fields below. Populate this object from a trusted, separately verified
source. Keep OCR text, invoice notes, and other untrusted content in fields such as
`invoice_text`, outside `authorized_payment`.

```python
{
    "instruction": "Prepare the verified bill for owner review.",
    "authorized_payment": {
        "agent_id": "devops-01",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 1000000,
        "token_decimals": 6
    },
    "invoice_text": "Untrusted invoice text goes here."
}
```

Every LLM `propose_transaction` tool call is checked against the structured
`authorized_payment` before it reaches the API. A mismatch is rejected. The offline
agent ignores free-form invoice content and proposes only the supplied structured
payment. This is a prototype guard, not independent verification that the structured
payment itself is correct.

## Configuration and use

Set the Aegis API URL and agent token. When all three LLM settings are absent, the
agent uses its deterministic offline fallback. To enable an OpenAI-compatible
provider, configure all three LLM variables. A partial or invalid LLM configuration
fails explicitly instead of silently changing modes.

```bash
export AEGIS_API_URL="http://127.0.0.1:8000"
export AEGIS_AGENT_TOKEN="your-agent-token"

# Optional; omit all three for deterministic offline behavior.
export AEGIS_LLM_BASE_URL="https://your-openai-compatible-provider.example/v1"
export AEGIS_LLM_MODEL="your-model"
read -r -s -p "LLM API key: " AEGIS_LLM_API_KEY
printf '\n'
export AEGIS_LLM_API_KEY
```

```python
import asyncio

from agent.runner import AegisAgent

task = {
    "instruction": "Prepare the verified bill for owner review.",
    "authorized_payment": {
        "agent_id": "devops-01",
        "chain_id": 84532,
        "token_symbol": "USDC",
        "token_address": "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
        "recipient": "0x2222222222222222222222222222222222222222",
        "amount_base_units": 1000000,
        "token_decimals": 6,
    },
    "invoice_text": "Untrusted invoice content.",
}

agent = AegisAgent.from_environment()
result = asyncio.run(agent.run(task))
print(result)
```

`AEGIS_LLM_BASE_URL` must use HTTPS, except that HTTP is allowed for loopback
development endpoints. Do not put provider credentials in its URL. The configured
LLM provider receives the task content and tool results; do not send it secrets or
unnecessary personal or financial data. The provider receives its own
`AEGIS_LLM_API_KEY`; it does not receive either Aegis bearer token. The API key is
never printed by the agent.

The agent API client uses these scopes:

| Tool | API operation | Authorization | Effect |
| --- | --- | --- | --- |
| `propose_transaction` | `POST /transactions/propose` | Agent token | Creates a policy-checked proposal and spend reservation. |
| `get_market_quote` | `GET /agent/market-data/{asset_class}/{symbol}` | Agent token | Reads a validated quote; no workflow change. |
| `read_transaction_status` | `GET /agent/transactions/{transaction_id}` | Agent token | Reads status, execution reference, and update time only. |

Approval, rejection, execution, full transaction retrieval, and audit reads remain
owner-only. A `ready` proposal is not a completed payment. The owner must review
and explicitly act through the owner-authenticated API or frontend. Execution
behavior is controlled by `AEGIS_EXECUTION_MODE`; the agent itself never executes.

## Deterministic evaluation

Run the offline scenarios from the repository root:

```bash
uv run --locked python -m agent.eval
```

The command uses isolated temporary SQLite databases, generated in-process test
tokens, simulated execution, and no live RPC, market-data, or LLM requests. It writes
`reports/agent-eval.json` and `reports/agent-eval.md` and exits nonzero if a scenario
fails. CI runs the same evaluation and stores reports outside the checkout.

The scenarios check:

1. A legitimate bill produces an allowed proposal for owner review.
2. An amount above the transaction policy limit is blocked.
3. Prompt-injection text in an invoice cannot change the structured recipient.
4. An unknown token is blocked.
5. Three maximum-size reservations fill the daily limit; a further proposal is
   blocked; the owner rejects one reservation; a fresh agent proposal then succeeds
   as a reservation for owner review.

The final scenario rejects an existing pending transaction to release its reservation,
then submits a fresh proposal. It does not re-open a policy-blocked transaction.
These are deterministic policy/workflow tests, not evidence of an actual transfer,
LLM quality, real-world invoice verification, or production-grade prompt-injection
resistance.
