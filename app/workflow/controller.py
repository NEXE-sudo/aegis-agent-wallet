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

    def propose(self, proposal: TransactionProposal, daily_spent_base_units: int) -> dict[str, Any]:
        policy_result = evaluate_transaction(proposal, self.policy, daily_spent_base_units)
        risk_result = assess_transaction_risk(proposal, self.policy, daily_spent_base_units)
        fingerprint = fingerprint_proposal(proposal)

        if policy_result.decision == PolicyDecision.BLOCK:
            status = "blocked"
        elif (
            policy_result.decision == PolicyDecision.REQUIRE_APPROVAL
            or risk_result.level == RiskLevel.HIGH
        ):
            status = "awaiting_approval"
        else:
            status = "ready"

        transaction_id = str(uuid.uuid4())
        self.store.create({
            "transaction_id": transaction_id,
            "fingerprint": fingerprint,
            "proposal": asdict(proposal),
            "policy_decision": policy_result.decision.value,
            "policy_reasons": policy_result.reasons,
            "risk_score": risk_result.score,
            "risk_level": risk_result.level.value,
            "risk_reasons": risk_result.reasons,
            "status": status,
        })
        return self.store.get(transaction_id) or {}

    def approve(self, transaction_id: str, fingerprint: str) -> tuple[str, dict[str, Any] | None]:
        return self.store.approve(transaction_id, fingerprint)

    def execute(self, transaction_id: str) -> tuple[str, dict[str, Any] | None]:
        return self.store.execute_simulated(transaction_id)
