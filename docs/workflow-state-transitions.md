# Workflow State-Transition Table

This document describes the state transitions implemented by `TransactionController` and `WorkflowStore`. It is a behavioral contract for the simulation-only workflow, not a claim that transactions are submitted on-chain.

## States

- `ready`: proposal passed initial policy checks and does not currently require approval.
- `awaiting_approval`: execution is gated on an owner approval.
- `approved`: an owner approved the proposal fingerprint and the approval timestamp is still valid.
- `blocked`: policy hard-blocked the proposal, either initially or during execution-time revalidation.
- `executed_simulated`: the simulation completed. This is terminal; no real signing or submission occurs.

## State-changing transitions

| Operation / condition | From | To | Audit event |
| --- | --- | --- | --- |
| Proposal is hard-blocked by policy | none | `blocked` | `proposal_created` |
| Proposal requires approval or starts high-risk | none | `awaiting_approval` | `proposal_created` |
| Proposal is allowed without approval | none | `ready` | `proposal_created` |
| Valid owner approval | `awaiting_approval` | `approved` | `approval_granted` |
| Approved timestamp is missing, malformed, in the future, or expired | `approved` | `awaiting_approval` | `approval_expired` |
| Execution-time policy revalidation blocks the proposal | `ready` or `approved` | `blocked` | `policy_revalidation_blocked` |
| Execution-time risk requires fresh approval | `ready` or `approved` | `awaiting_approval` | `risk_escalation_requires_approval` |
| Execution-time policy introduces a new approval requirement | `ready` or `approved` | `awaiting_approval` | `policy_revalidation_requires_approval` |
| Successful simulated execution | `ready` or `approved` | `executed_simulated` | `simulated_execution` |

For the policy-revalidation approval case, an already-approved transaction may proceed when the same approval requirement was present at proposal time and the approval remains valid. A newly introduced approval requirement invalidates that path. Risk escalation from an approved transaction requires fresh approval when the recalculated risk changed.

## Rejected or idempotent operations

These outcomes must not change the stored status:

| Operation / condition | Outcome | Expected status effect |
| --- | --- | --- |
| Approve a `ready` transaction | `approval_not_required` | Remains `ready` |
| Approve a `blocked` transaction | `blocked` | Remains `blocked` |
| Approve an `executed_simulated` transaction | `already_executed` | Remains `executed_simulated` |
| Approve a valid, already-approved transaction | `already_approved` | Remains `approved` |
| Execute an `awaiting_approval` transaction | `approval_required` | Remains `awaiting_approval` |
| Execute a `blocked` transaction | `blocked` | Remains `blocked` |
| Execute an `executed_simulated` transaction again | `already_executed` | Remains `executed_simulated` |
| Execute an `approved` transaction whose approval fingerprint does not match | `approval_mismatch` | Remains `approved` |

A missing transaction returns `not_found` and does not create a transaction state or audit event. A persisted proposal integrity mismatch is rejected without changing status and is audited as `proposal_integrity_mismatch`.

## Audit expectations and limitations

Every state-changing workflow transition above appends an audit event with matching `from_status` and `to_status` values, within the same SQLite transaction as the state update. Ordinary rejected/no-op operations listed above do not currently append audit events; this is an explicit current behavior, not a guarantee that every failed request is audited. Proposal creation is represented by `from_status = null` and its initial status as `to_status`.

The tests in `tests/test_workflow_transitions.py` cover core legal transitions, terminal-state behavior, and rejected operations that must leave state unchanged. Existing tests cover expiry, integrity failures, policy revalidation, risk escalation, and their associated audit events.
