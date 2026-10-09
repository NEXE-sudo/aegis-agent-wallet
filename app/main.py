from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.policy.engine import evaluate_transaction
from app.policy.models import AgentPolicy, PolicyDecision, TransactionProposal
from app.risk.engine import assess_transaction_risk
from app.risk.models import RiskLevel

app = FastAPI(
    title="Aegis Agent Wallet API",
    description="Policy and risk simulation only; no wallet signing or blockchain submission yet.",
    version="0.2.0",
)


class EvaluationRequest(BaseModel):
    agent_id: str = "devops-01"
    chain_id: int
    token_symbol: str
    token_address: str
    recipient: str
    amount_base_units: int = Field(gt=0)
    token_decimals: int = Field(ge=0, le=36)
    daily_spent_base_units: int = Field(default=0, ge=0)


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


# Demo-only policy. The addresses are placeholders and must be replaced with verified values.
DEMO_POLICY = AgentPolicy(
    agent_id="devops-01",
    allowed_chain_ids={84532},  # Base Sepolia
    allowed_token_addresses={"0x1111111111111111111111111111111111111111"},
    allowed_token_symbols={"USDC"},
    max_transaction_base_units=50_000_000,  # 50 USDC, assuming 6 decimals
    daily_limit_base_units=150_000_000,     # 150 USDC, assuming 6 decimals
    approval_threshold_base_units=50_000_000,
    allowed_recipients={
        "0x2222222222222222222222222222222222222222",
        "0x3333333333333333333333333333333333333333",
    },
    unknown_recipient_requires_approval=True,
    enabled=True,
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "policy-simulation-only"}


@app.post("/policy/evaluate", response_model=EvaluationResponse)
def evaluate(request: EvaluationRequest) -> EvaluationResponse:
    if request.agent_id != DEMO_POLICY.agent_id:
        raise HTTPException(status_code=404, detail="Unknown demo agent")
    proposal = TransactionProposal(
        agent_id=request.agent_id,
        chain_id=request.chain_id,
        token_symbol=request.token_symbol,
        token_address=request.token_address,
        recipient=request.recipient,
        amount_base_units=request.amount_base_units,
        token_decimals=request.token_decimals,
    )
    result = evaluate_transaction(proposal, DEMO_POLICY, request.daily_spent_base_units)
    return EvaluationResponse(
        decision=result.decision,
        reasons=result.reasons,
        amount_base_units=request.amount_base_units,
        projected_daily_spend_base_units=(
            request.daily_spent_base_units + request.amount_base_units
        ),
    )


@app.post("/risk/assess", response_model=RiskResponse)
def assess_risk(request: EvaluationRequest) -> RiskResponse:
    """Assess transaction risk independently of policy enforcement."""
    if request.agent_id != DEMO_POLICY.agent_id:
        raise HTTPException(status_code=404, detail="Unknown demo agent")
    proposal = TransactionProposal(
        agent_id=request.agent_id,
        chain_id=request.chain_id,
        token_symbol=request.token_symbol,
        token_address=request.token_address,
        recipient=request.recipient,
        amount_base_units=request.amount_base_units,
        token_decimals=request.token_decimals,
    )
    result = assess_transaction_risk(
        proposal, DEMO_POLICY, request.daily_spent_base_units
    )
    return RiskResponse(
        score=result.score,
        level=result.level,
        reasons=result.reasons,
        amount_base_units=request.amount_base_units,
        projected_daily_spend_base_units=(
            request.daily_spent_base_units + request.amount_base_units
        ),
    )
