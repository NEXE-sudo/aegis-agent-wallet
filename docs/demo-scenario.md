# Reproducible Demo Scenario

This walkthrough exercises the Aegis Agent Wallet API against a fresh local SQLite database. It uses placeholder recipients and testnet USDC metadata; **all transaction execution remains simulated**. No private keys, signatures, or transaction submissions are involved.

## 1. Start from a clean local demo environment

Run from the repository root in an activated virtual environment with development dependencies installed.

```bash
export AEGIS_AGENT_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AEGIS_APPROVAL_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AEGIS_DB_PATH="./aegis-demo.sqlite3"
export AEGIS_APPROVAL_EXPIRES_SECONDS="300"
export BASE_SEPOLIA_RPC_URL="https://YOUR_BASE_SEPOLIA_RPC_PROVIDER_URL"
```

Keep the tokens distinct and private. Start the API in one terminal:

```bash
uvicorn app.main:app --reload
```

In another terminal, set the same token values. If using a fresh shell, copy the values securely rather than generating new ones. The API should respond to `GET /health` with `{"status":"ok","mode":"approval-workflow-simulation-only"}`.

The examples below use:

- Chain ID: `84532` (Base Sepolia)
- Token: the demo policy's USDC contract, `0x036CbD53842c5426634e7929541eC2318f3dCF7e`
- Token decimals: `6`
- Allowed placeholder recipient: `0x2222222222222222222222222222222222222222`
- Unknown placeholder recipient: `0x4444444444444444444444444444444444444444`

The addresses are demo inputs, not verified identities or a recommendation to transfer funds.

For convenience, set these in the client terminal:

```bash
export API="http://127.0.0.1:8000"
export TOKEN="0x036CbD53842c5426634e7929541eC2318f3dCF7e"
export AGENT_TOKEN="paste-the-same-agent-token-here"
export OWNER_TOKEN="paste-the-same-owner-token-here"
export ALLOWED_RECIPIENT="0x2222222222222222222222222222222222222222"
export UNKNOWN_RECIPIENT="0x4444444444444444444444444444444444444444"
```

## 2. Allowed proposal and simulated execution

Submit a 1 USDC proposal to the configured recipient (1 USDC = 1,000,000 base units):

```bash
curl -sS -X POST "$API/transactions/propose" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-allowed-001" \
  -d "{\"chain_id\":84532,\"token_symbol\":\"USDC\",\"token_address\":\"$TOKEN\",\"recipient\":\"$ALLOWED_RECIPIENT\",\"amount_base_units\":1000000,\"token_decimals\":6}"
```

Expected: HTTP `201` and a transaction record whose `status` is `ready` for this low-risk, allowed demo proposal. Record the returned `transaction_id` as `TX_ID`. Then simulate execution:

```bash
export TX_ID="paste-the-returned-transaction-id-here"
curl -sS -X POST "$API/transactions/$TX_ID/execute" \
  -H "Authorization: Bearer $OWNER_TOKEN"
```

Expected: a successful response with `status` `simulated_executed` and an `execution_reference` explicitly representing a simulation, not an on-chain transaction hash.

## 3. Human-approval path

Submit a proposal to the unknown placeholder recipient:

```bash
curl -sS -X POST "$API/transactions/propose" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-approval-001" \
  -d "{\"chain_id\":84532,\"token_symbol\":\"USDC\",\"token_address\":\"$TOKEN\",\"recipient\":\"$UNKNOWN_RECIPIENT\",\"amount_base_units\":1000000,\"token_decimals\":6}"
```

Expected: HTTP `201` with `status` `awaiting_approval`. Attempting execution before approval should return HTTP `409` because human approval is required.

Use the response's `transaction_id` and `fingerprint` values:

```bash
export TX_ID="paste-the-approval-transaction-id-here"
export TX_FINGERPRINT="paste-the-exact-64-character-fingerprint-here"
curl -sS -X POST "$API/transactions/$TX_ID/approve" \
  -H "Authorization: Bearer $OWNER_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"transaction_fingerprint\":\"$TX_FINGERPRINT\",\"confirmation\":\"APPROVE\"}"
```

Expected: approval succeeds only when the exact fingerprint is supplied and the transaction is awaiting approval. Executing afterward returns `simulated_executed`; it still does not sign or submit a transaction.

## 4. Hard-blocked proposal

Try the otherwise valid 1 USDC proposal with an unsupported token symbol (the address and decimals intentionally remain the demo token's values):

```bash
curl -sS -X POST "$API/transactions/propose" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-blocked-001" \
  -d "{\"chain_id\":84532,\"token_symbol\":\"FAKE\",\"token_address\":\"$TOKEN\",\"recipient\":\"$ALLOWED_RECIPIENT\",\"amount_base_units\":1000000,\"token_decimals\":6}"
```

Expected: HTTP `201` containing a transaction whose policy decision/status indicates a hard block. A blocked proposal cannot be approved or executed; the execute endpoint returns HTTP `403`. This demonstrates that approval cannot override a policy hard block.

## 5. Idempotency replay and conflict

Repeat the exact request from section 2 with the same `Idempotency-Key: demo-allowed-001` and the same payload. Expected: HTTP `200` and the original transaction record rather than a second reservation.

Then reuse that key with a different payload, such as changing `amount_base_units` to `2000000`. Expected: HTTP `409` because the same idempotency key cannot identify two different proposals. Use a new key for a new proposal.


## 6. Expired approval and reapproval

This path is intentionally time-based. Use a separate demo database and restart the API with a short approval lifetime so the walkthrough does not require waiting five minutes. Stop the existing API first. In the server terminal, keep the same distinct agent and owner tokens, then set:

```bash
export AEGIS_DB_PATH="./aegis-expiry-demo.sqlite3"
export AEGIS_APPROVAL_EXPIRES_SECONDS="3"
uvicorn app.main:app --reload
```

In the client terminal, use the same tokens as the server and submit a proposal to the unknown placeholder recipient:

```bash
curl -sS -X POST "$API/transactions/propose" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-expiry-001" \
  -d "{\"chain_id\":84532,\"token_symbol\":\"USDC\",\"token_address\":\"$TOKEN\",\"recipient\":\"$UNKNOWN_RECIPIENT\",\"amount_base_units\":1000000,\"token_decimals\":6}"
```

Copy the returned `transaction_id` and `fingerprint` into the variables below, then approve using the owner token:

```bash
export TX_ID="paste-the-expiry-demo-transaction-id"
export TX_FINGERPRINT="paste-the-exact-64-character-fingerprint"
curl -sS -X POST "$API/transactions/$TX_ID/approve" \
  -H "Authorization: Bearer $OWNER_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"transaction_fingerprint\":\"$TX_FINGERPRINT\",\"confirmation\":\"APPROVE\"}"
```

Wait longer than the configured three-second lifetime, then attempt simulated execution:

```bash
python -c "import time; time.sleep(4)"
curl -sS -i -X POST "$API/transactions/$TX_ID/execute" \
  -H "Authorization: Bearer $OWNER_TOKEN"
```

Expected: HTTP `409` with `Human approval is required before execution`. The API invalidates the stale approval and returns the transaction to `awaiting_approval`; inspect `GET /transactions/$TX_ID` with the owner token to verify. Approve again with the same transaction fingerprint and repeat execution promptly. The renewed approval should allow the workflow to reach `simulated_executed`, but it still does not sign or submit a transaction.

The timing step is intentionally approximate: if the approval expires before the first execution request, that is the expected outcome. Do not use this short expiry setting for any environment beyond the local demo.

## 7. Inspect the audit trail

For a transaction ID, use the owner token to inspect persisted state and ordered audit events:

```bash
curl -sS "$API/transactions/$TX_ID" \
  -H "Authorization: Bearer $OWNER_TOKEN"
curl -sS "$API/transactions/$TX_ID/audit" \
  -H "Authorization: Bearer $OWNER_TOKEN"
```

Expected: the transaction response shows its current state, while the audit response records the workflow events without exposing bearer tokens or raw idempotency keys.

## Expected-outcome checklist

| Scenario | Expected result |
|---|---|
| Small allowed proposal to configured recipient | `ready`; simulated execution succeeds |
| Unknown recipient | `awaiting_approval`; execute before approval returns HTTP 409 |
| Exact owner approval using the stored fingerprint | Approval succeeds; later simulated execution succeeds |
| Token metadata/policy mismatch | Hard-blocked; approval cannot override it; execute returns HTTP 403 |
| Same idempotency key and identical proposal | HTTP 200 replay of original record |
| Same idempotency key and different proposal | HTTP 409 conflict |
| Audit retrieval | Ordered workflow events visible to owner; secrets remain redacted |

The exact risk score and reason strings are implementation details; validate the stable decision/status and HTTP behavior above. This demo does not use live RPC calls as part of the transaction workflow and does not establish token authenticity or recipient identity.

## Resetting the demo

Stop the API, then remove the local demo database only if you no longer need its transaction and audit history:

```bash
rm -f ./aegis-demo.sqlite3
```

Restart the API with the same environment. Do not remove a database that contains data you want to keep.