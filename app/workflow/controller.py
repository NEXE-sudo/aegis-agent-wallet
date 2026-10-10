"""Coordinates policy, risk, approval, and a deliberately simulated executor."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from typing import Any

from app.policy.engine import evaluate_transaction
from app.policy.models import AgentPolicy, PolicyDecision, TransactionProposal
from app.risk.engine import assess_transaction_risk
from app.risk.models import RiskLevel
from app.workflow.store import WorkflowStore


def fingerprint_proposal(proposal: TransactionProposal) -> str:
    canonical = json.dumps(asdict(proposal), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TransactionController:
    def __init__(self, policy: AgentPolicy, store: WorkflowStore) -> None:
        self.policy = policy
        self.store = store

    def propose(
        self, proposal: TransactionProposal, idempotency_key: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        fingerprint = fingerprint_proposal(proposal)

        def build_record(
            daily_spent_base_units: int,
            recipient_daily_spent_base_units: int,
        ) -> dict[str, Any]:
            policy_result = evaluate_transaction(
                proposal, self.policy, daily_spent_base_units, recipient_daily_spent_base_units
            )
            risk_result = assess_transaction_risk(proposal, self.policy, daily_spent_base_units)
            if policy_result.decision == PolicyDecision.BLOCK:
                status = "blocked"
            elif policy_result.decision == PolicyDecision.REQUIRE_APPROVAL or risk_result.level == RiskLevel.HIGH:
                status = "awaiting_approval"
            else:
                status = "ready"
            return {
                "transaction_id": str(uuid.uuid4()),
                "fingerprint": fingerprint,
                "proposal": asdict(proposal),
                "policy_decision": policy_result.decision.value,
                "policy_reasons": policy_result.reasons,
                "risk_score": risk_result.score,
                "risk_level": risk_result.level.value,
                "risk_reasons": risk_result.reasons,
                "status": status,
            }

        return self.store.create_evaluated(asdict(proposal), idempotency_key, build_record)

    def approve(self, transaction_id: str, fingerprint: str) -> tuple[str, dict[str, Any] | None]:
        return self.store.approve(transaction_id, fingerprint, approval_expires_seconds=self.policy.approval_expires_seconds)

    def execute(self, transaction_id: str) -> tuple[str, dict[str, Any] | None]:
        return self.store.execute_simulated(transaction_id, self.policy)
