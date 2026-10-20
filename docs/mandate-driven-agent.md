# Mandate-driven agent decisions

The deterministic decision engine in `agent.autonomy.decide_payments` is now connected to a persisted governance API through `app.governance`. The agent can process owner-created structured tasks and submit proposals to the existing wallet policy gateway; it cannot approve, reject, execute, change mandates, or verify payees itself.

## Owner workflow

All governance mutation and read endpoints require the owner bearer token:

- `PUT /governance/mandate` creates or replaces the single active mandate.
- `PUT /governance/payees/{payee_id}` creates or updates a payee record.
- `POST /governance/tasks` queues a structured payment task.
- `GET /governance/mandate`, `GET /governance/payees`, and `GET /governance/tasks` inspect saved state.
- `GET /governance/audit/verify` verifies the stored governance event chain, persisted payload hashes, and decision chain.

A mandate currently permits one token symbol per budget because a base-unit budget cannot safely aggregate tokens with different decimals. The mandate fields use integer token base units. `available_balance_base_units` is an owner-maintained planning value, not a live blockchain balance; it must not be presented as chain-observed liquidity. The actual wallet policy gateway and AegisVault remain the enforcement boundaries.

Payee records are limited to Base Sepolia (chain ID 84532) and token metadata already trusted and enabled by the wallet policy. The owner must explicitly set `verified: true` after verifying the payee out of band. A registry entry does not itself expand the active wallet policy's recipient allowlist. If the policy requires approval or blocks a proposal, that decision remains in force. Tasks are bound to the mandate and payee payload hashes present when they are queued; if either record changes, the task escalates and must be reviewed and requeued. Governance-created proposals are blocked from approval/execution if their mandate or payee record later changes, or if governance integrity verification fails.

## Agent workflow

The agent runner can process the owner-managed queue without accepting caller-supplied payment fields:

```python
result = await AegisAgent.from_environment().run({"mode": "governance_queue"})
```

The API endpoint `POST /agent/governance/decide-pending` evaluates all pending tasks against the active mandate, registered payees, prior decisions, duplicate invoices, daily and monthly budget reservations, treasury-buffer planning, approval thresholds, and possible split-payment structuring. For `propose` decisions, the service builds the proposal from the registered payee's chain/token/address metadata and the owner-created task's integer amount, then sends it through `TransactionController.propose`. The agent cannot submit a recipient or token override in this mode.

The queue is deterministic. The existing optional LLM path remains a separate tool-limited workflow and has not yet been integrated as a reasoner over this persisted queue. A decision is a proposal recommendation, not approval or execution. A proposal may still be marked `awaiting_approval` or `blocked` by the existing wallet policy and risk checks.

## Trust model

- Owner-authenticated records are the source of mandate, payee, and task payment fields.
- Free-text `description` is untrusted display context. The decision engine never parses it for recipient, token, chain, or amount.
- Unknown and unverified payees escalate without a proposed amount or address.
- Amounts, budgets, limits, and balances use integer token base units; no floating-point conversions are used.
- Governance and decision payloads are stored with SHA-256 hashes and linked audit records. Verification detects payload/hash inconsistencies and chain breaks; it is not a digital signature and does not protect against an attacker who can rewrite the entire SQLite database and recompute every hash.
- Previously proposed amounts are conservatively counted in mandate/payee budget history. This first iteration does not reconcile cancelled or blocked proposals back out of governance budget history; treat the governance budget as a conservative reservation until that lifecycle integration is completed.
- Existing wallet policy revalidation, owner approvals, and on-chain vault caps remain authoritative. Simulated execution is the default; the vault is unaudited and testnet-only.

## Decision order

1. Order pending tasks by due date, creation time, and stable task ID.
2. Escalate possible threshold evasion: at least three sub-threshold payments to one registered payee within 24 hours whose sum reaches the approval threshold.
3. Escalate unknown/unverified payees, invalid amounts, disallowed tokens/categories, per-payment violations, daily-limit violations, and payments meeting the human-approval threshold.
4. Skip duplicate invoices, exhausted mandate budgets, and payments that would breach the treasury buffer.
5. Propose only when the deterministic checks pass; attach rationale, rule evidence references, mandate ID/hash, and the resulting wallet proposal status.

The confidence field is a rule label, not a calibrated probability. The engine is not a validated fraud detector. Broad adversarial evaluation, LLM orchestration over the persisted queue, transaction-lifecycle reconciliation, and owner-facing frontend views remain outstanding.
