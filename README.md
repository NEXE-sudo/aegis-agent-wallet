# Aegis Agent Wallet

Policy-engine foundation for an AI-agent wallet prototype. Testnet only.

## macOS setup

```bash
python3 --version
mkdir -p ~/Developer
cd ~/Developer
# Extract aegis-agent-wallet.zip here, then:
cd aegis-agent-wallet
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
pytest -q
uvicorn app.main:app --reload
```

API docs: http://127.0.0.1:8000/docs

## Test the policy endpoint

```bash
curl -s http://127.0.0.1:8000/policy/evaluate \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_id": "devops-01",
    "chain_id": 84532,
    "token_symbol": "USDC",
    "token_address": "0x1111111111111111111111111111111111111111",
    "recipient": "0x2222222222222222222222222222222222222222",
    "amount_base_units": 25000000,
    "token_decimals": 6,
    "daily_spent_base_units": 40000000
  }'
```

This simulates a 25 USDC transaction with 40 USDC already spent today. It does not sign or send a transaction. Addresses in the demo are placeholders, not official token or service addresses.

## Security
- Never put a real private key or seed phrase in this repo, frontend, prompt, or `.env`.
- The API currently uses an in-memory demo policy and has no authentication.
- Hard policy violations cannot be overridden by the approval flow.
- Do not treat the demo risk logic as a validated fraud detector.


## Milestone 2: Risk assessment

The deterministic risk engine is separate from policy enforcement. Run the API and open
`http://127.0.0.1:8000/docs`, then try `POST /risk/assess`. It returns a score from 0 to 100,
a low/medium/high level, and reasons for each score contribution. Current heuristic signals are
unknown recipient, transaction size relative to the per-transaction limit, projected daily spend,
and the approval threshold.

This is a transparent demo heuristic, not a fraud detector. Its result never overrides the policy
engine. The current demo addresses are placeholders, and no wallet signing or blockchain submission
is implemented.
