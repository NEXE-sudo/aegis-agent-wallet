# Mandate-driven agent decisions

The deterministic decision engine is available as `agent.autonomy.decide_payments`. It is a pure decision function: it does not call the API, sign transactions, approve/reject proposals, mutate policy, or broadcast anything.

## Trust model

- The owner supplies a `Mandate` and an owner-verified `Payee` registry.
- Tasks carry a registry `payee_id`, invoice identifier, token symbol, integer token base-unit amount, and timezone-aware timestamps.
- `description` is untrusted display text. The decision engine never parses it for recipient, token, chain, or amount.
- Unknown and unverified payees escalate without a proposed amount or address. They require owner action outside the agent.
- A `propose` decision is a recommendation for the existing policy gateway, not approval or execution authority.
- Amounts and budgets are expressed in integer base units; token-specific conversion must happen at a trusted ingestion boundary, never from free text.

## Decision order

1. Order tasks by due date, creation time, and stable task ID.
2. Escalate potential threshold-evasion patterns: at least three sub-threshold payments to one registered payee within 24 hours whose sum reaches the approval threshold.
3. Escalate unknown or unverified payees, invalid amounts, disallowed tokens/categories, per-payment violations, daily-limit violations, and transactions meeting the human-approval threshold.
4. Skip duplicate invoices, exhausted mandate budgets, and payments that would breach the treasury buffer.
5. Propose only when all deterministic checks pass; return a rationale, confidence and evidence references for each decision.

This engine is a deterministic baseline, not a validated fraud detector. Its confidence field is a rule label, not a calibrated probability. Persisted mandates, payee verification APIs, LLM orchestration, decision audit hashes, and broad adversarial evaluation are follow-up work. The existing Aegis policy gateway and AegisVault remain authoritative. Simulated execution remains the default and the vault remains unaudited.
