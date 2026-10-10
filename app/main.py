"""Aegis Agent Wallet API. Workflow execution is simulation-only."""
import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from app.auth import require_agent, require_owner
from app.chain.base_sepolia import BASE_SEPOLIA_USDC_ADDRESS
from app.chain.router import router as base_sepolia_router
from app.policy.engine import evaluate_transaction
from app.policy.models import (
    MAX_SQLITE_INTEGER,
    AgentPolicy,
    PolicyDecision,
    TransactionProposal,
    TrustedToken,
)
from app.risk.engine import assess_transaction_risk
from app.risk.models import RiskLevel
from app.workflow.controller import TransactionController
from app.workflow.store import WorkflowStore


class EvaluationRequest(BaseModel):
    agent_id: str = "devops-01"
    chain_id: int = Field(gt=0, le=MAX_SQLITE_INTEGER)
    token_symbol: str
    token_address: str
    recipient: str
    amount_base_units: int = Field(gt=0, le=MAX_SQLITE_INTEGER)
    token_decimals: int = Field(ge=0, le=36)
    # Only the standalone evaluation endpoints accept caller-supplied simulated spend.
    # The transaction workflow never trusts this value.
    daily_spent_base_units: int = Field(default=0, ge=0, le=MAX_SQLITE_INTEGER)


class ProposalRequest(BaseModel):
    agent_id: str = "devops-01"
    chain_id: int = Field(gt=0, le=MAX_SQLITE_INTEGER)
    token_symbol: str
    token_address: str
    recipient: str
    amount_base_units: int = Field(gt=0, le=MAX_SQLITE_INTEGER)
    token_decimals: int = Field(ge=0, le=36)


class EvaluationResponse(BaseModel):
    decision: PolicyDecision
    reasons: list[str]
    amount_base_units: int
    projected_daily_spend_base_units: int


class RiskResponse(BaseModel):
    score: int = Field(ge=0, le=100)
    level: RiskLevel
    reasons: list[str]
    amount_base_units: int
    projected_daily_spend_base_units: int


class ApprovalRequest(BaseModel):
    transaction_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: Literal["APPROVE"]


class TransactionResponse(BaseModel):
    transaction_id: str
    fingerprint: str
    proposal: dict
    policy_decision: str
    policy_reasons: list[str]
    risk_score: int
    risk_level: str
    risk_reasons: list[str]
    status: str
    approval_fingerprint: str | None = None
    approved_at: str | None = None
    execution_reference: str | None = None
    created_at: str
    updated_at: str


DEMO_POLICY = AgentPolicy(
    agent_id="devops-01",
    allowed_chain_ids={84532},
    allowed_token_addresses={BASE_SEPOLIA_USDC_ADDRESS},
    allowed_token_symbols={"USDC"},
    trusted_tokens={BASE_SEPOLIA_USDC_ADDRESS: TrustedToken(chain_id=84532, symbol="USDC", decimals=6)},
    max_transaction_base_units=50_000_000,
    daily_limit_base_units=150_000_000,
    approval_threshold_base_units=50_000_000,
    allowed_recipients={
        "0x2222222222222222222222222222222222222222",
        "0x3333333333333333333333333333333333333333",
    },
    recipient_token_allowlist={
        "0x2222222222222222222222222222222222222222": {BASE_SEPOLIA_USDC_ADDRESS},
        "0x3333333333333333333333333333333333333333": {BASE_SEPOLIA_USDC_ADDRESS},
    },
    unknown_recipient_requires_approval=True,
    enabled=True,
    approval_expires_seconds=int(os.environ.get("AEGIS_APPROVAL_EXPIRES_SECONDS", "300")),
)

store = WorkflowStore(os.environ.get("AEGIS_DB_PATH", "aegis-workflow.sqlite3"))
controller = TransactionController(DEMO_POLICY, store)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(
    title="Aegis Agent Wallet API",
    description="Policy, risk, and approval workflow simulation with optional read-only Base Sepolia inspection. No signing or transaction submission occurs.",
    version="1.10.0",
    lifespan=lifespan,
)


def _proposal(request: EvaluationRequest | ProposalRequest) -> TransactionProposal:
    if request.agent_id != DEMO_POLICY.agent_id:
        raise HTTPException(status_code=404, detail="Unknown demo agent")
    return TransactionProposal(
        agent_id=request.agent_id,
        chain_id=request.chain_id,
        token_symbol=request.token_symbol,
        token_address=request.token_address,
        recipient=request.recipient,
        amount_base_units=request.amount_base_units,
        token_decimals=request.token_decimals,
    )


def _record_response(record: dict) -> TransactionResponse:
    return TransactionResponse(**record)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "approval-workflow-simulation-only"}


@app.post("/policy/evaluate", response_model=EvaluationResponse)
def evaluate(request: EvaluationRequest) -> EvaluationResponse:
    proposal = _proposal(request)
    result = evaluate_transaction(proposal, DEMO_POLICY, request.daily_spent_base_units)
    return EvaluationResponse(
        decision=result.decision,
        reasons=result.reasons,
        amount_base_units=request.amount_base_units,
        projected_daily_spend_base_units=request.daily_spent_base_units + request.amount_base_units,
    )


@app.post("/risk/assess", response_model=RiskResponse)
def assess_risk(request: EvaluationRequest) -> RiskResponse:
    proposal = _proposal(request)
    result = assess_transaction_risk(proposal, DEMO_POLICY, request.daily_spent_base_units)
    return RiskResponse(
        score=result.score,
        level=result.level,
        reasons=result.reasons,
        amount_base_units=request.amount_base_units,
        projected_daily_spend_base_units=request.daily_spent_base_units + request.amount_base_units,
    )


@app.post("/transactions/propose", response_model=TransactionResponse, status_code=201,
          dependencies=[Depends(require_agent)])
def propose_transaction(
    request: ProposalRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=128),
) -> TransactionResponse:
    if idempotency_key is not None and not idempotency_key.strip():
        raise HTTPException(status_code=422, detail="Idempotency-Key must not be blank")

    proposal = _proposal(request)
    outcome, record = controller.propose(proposal, idempotency_key)
    if outcome == "idempotency_conflict":
        raise HTTPException(status_code=409, detail="Idempotency-Key was already used for a different proposal")
    if outcome == "replayed":
        response.status_code = 200
    return _record_response(record)


@app.get("/transactions/{transaction_id}/audit", dependencies=[Depends(require_owner)])
def get_transaction_audit(transaction_id: str) -> list[dict]:
    if store.get(transaction_id) is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return store.audit_events(transaction_id)


@app.get("/transactions/{transaction_id}", response_model=TransactionResponse,
         dependencies=[Depends(require_owner)])
def get_transaction(transaction_id: str) -> TransactionResponse:
    record = store.get(transaction_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return _record_response(record)


@app.post("/transactions/{transaction_id}/approve", response_model=TransactionResponse,
          dependencies=[Depends(require_owner)])
def approve_transaction(transaction_id: str, request: ApprovalRequest) -> TransactionResponse:
    outcome, record = controller.approve(transaction_id, request.transaction_fingerprint)
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Transaction not found")
    if outcome == "fingerprint_mismatch":
        raise HTTPException(status_code=409, detail="Approval fingerprint does not match transaction")
    if outcome == "proposal_integrity_mismatch":
        raise HTTPException(status_code=409, detail="Persisted proposal failed integrity verification")
    if outcome == "blocked":
        raise HTTPException(status_code=409, detail="Blocked transactions cannot be approved")
    if outcome == "already_executed":
        raise HTTPException(status_code=409, detail="Transaction has already been executed")
    if outcome == "approval_not_required":
        raise HTTPException(status_code=409, detail="Transaction is not awaiting approval")
    if outcome == "approval_expired":
        raise HTTPException(
            status_code=409,
            detail="Approval has expired; submit a fresh approval",
        )
    if outcome == "already_approved":
        raise HTTPException(status_code=409, detail="Transaction has already been approved")
    return _record_response(record or {})


@app.post("/transactions/{transaction_id}/execute", response_model=TransactionResponse,
          dependencies=[Depends(require_owner)])
def execute_transaction(transaction_id: str) -> TransactionResponse:
    outcome, record = controller.execute(transaction_id)
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Transaction not found")
    if outcome == "blocked":
        raise HTTPException(status_code=403, detail="Policy-blocked transactions cannot execute")
    if outcome == "proposal_integrity_mismatch":
        raise HTTPException(status_code=409, detail="Persisted proposal failed integrity verification")
    if outcome == "approval_required":
        raise HTTPException(status_code=409, detail="Human approval is required before execution")
    if outcome == "already_executed":
        raise HTTPException(status_code=409, detail="Transaction has already been executed")
    if outcome in {"invalid_state", "approval_mismatch"}:
        raise HTTPException(status_code=409, detail="Transaction is not in an executable state")
    return _record_response(record or {})


# Read-only chain inspection; this router exposes no signing or submission methods.
app.include_router(base_sepolia_router)
