"""Aegis Agent Wallet API with simulated or explicitly gated Base Sepolia execution."""
import logging
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from app.auth import require_agent, require_owner
from app.chain.base_sepolia import (
    BASE_SEPOLIA_CHAIN_ID,
    BASE_SEPOLIA_USDC_ADDRESS,
    RpcConfigurationError,
    RpcRequestError,
)
from app.chain.execution import (
    BaseSepoliaExecutor,
    BaseSepoliaVaultExecutor,
    ExecutionConfigurationError,
    ExecutionSettings,
    PreflightError,
    UnsignedTransactionInvariantError,
    VaultExecutionError,
    VaultTransactionInvariantError,
)
from app.chain.router import router as base_sepolia_router
from app.market_data import (
    CompositeMarketDataProvider,
    MarketDataError,
    UnsupportedMarketSymbol,
)
from app.policy.canonical import POLICY_HASH_FORMAT_VERSION, hash_policy
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
from app.workflow.store import OnchainSubmissionError, WorkflowDataError, WorkflowStore


def _canonical_ascii_proposal_field(value: object, field_name: str) -> object:
    """Canonicalize proposal text using ASCII only, before schema length checks."""
    if not isinstance(value, str):
        return value
    if not value.isascii():
        raise ValueError(f"{field_name} must contain ASCII characters")
    value = value.strip()
    if field_name == "token_symbol":
        return value.upper()
    return value.lower()


class EvaluationRequest(BaseModel):
    agent_id: str = Field(default="devops-01", max_length=64)
    chain_id: int = Field(strict=True, gt=0, le=MAX_SQLITE_INTEGER)
    token_symbol: str = Field(max_length=16)
    token_address: str = Field(max_length=42)
    recipient: str = Field(max_length=42)
    amount_base_units: int = Field(strict=True, gt=0, le=MAX_SQLITE_INTEGER)
    token_decimals: int = Field(strict=True, ge=0, le=36)
    # Only the standalone evaluation endpoints accept caller-supplied simulated spend.
    # The transaction workflow never trusts this value.
    daily_spent_base_units: int = Field(default=0, strict=True, ge=0, le=MAX_SQLITE_INTEGER)

    @field_validator("token_symbol", "token_address", "recipient", mode="before")
    @classmethod
    def canonicalize_text_fields(cls, value: object, info: ValidationInfo) -> object:
        return _canonical_ascii_proposal_field(value, info.field_name)


class ProposalRequest(BaseModel):
    agent_id: str = Field(default="devops-01", max_length=64)
    chain_id: int = Field(strict=True, gt=0, le=MAX_SQLITE_INTEGER)
    token_symbol: str = Field(max_length=16)
    token_address: str = Field(max_length=42)
    recipient: str = Field(max_length=42)
    amount_base_units: int = Field(strict=True, gt=0, le=MAX_SQLITE_INTEGER)
    token_decimals: int = Field(strict=True, ge=0, le=36)

    @field_validator("token_symbol", "token_address", "recipient", mode="before")
    @classmethod
    def canonicalize_text_fields(cls, value: object, info: ValidationInfo) -> object:
        return _canonical_ascii_proposal_field(value, info.field_name)


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


class MarketDataResponse(BaseModel):
    asset_class: Literal["crypto", "equity"]
    symbol: str
    price: float = Field(gt=0)
    currency: Literal["USD"]
    as_of: datetime
    source: str
    is_stale: bool


class ApprovalRequest(BaseModel):
    transaction_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: Literal["APPROVE"]


class PauseRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=256)


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


class AgentTransactionStatusResponse(BaseModel):
    transaction_id: str
    status: str
    execution_reference: str | None = None
    updated_at: str


class TransactionReceiptResponse(BaseModel):
    transaction_id: str
    transaction_hash: str
    chain_id: Literal[84532]
    status: Literal["pending", "success", "failed"]
    block_number: int | None = None


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

execution_settings = ExecutionSettings.from_environment()
store = WorkflowStore(os.environ.get("AEGIS_DB_PATH", "aegis-workflow.sqlite3"))
controller = TransactionController(DEMO_POLICY, store)
market_data_provider = CompositeMarketDataProvider()


@asynccontextmanager
async def lifespan(application: FastAPI):
    global execution_settings
    execution_settings = ExecutionSettings.from_environment()
    application.state.execution_settings = execution_settings
    yield


app = FastAPI(
    title="Aegis Agent Wallet API",
    description="Policy, risk, and approval workflow with simulated execution by default and explicitly gated Base Sepolia testnet execution.",
    version="1.10.3",
    lifespan=lifespan,
)


request_logger = logging.getLogger("aegis.request")


@app.middleware("http")
async def log_http_requests(request: Request, call_next):
    """Log request metadata without recording raw URLs, headers, or exception text."""
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # Record the failure status and route metadata, but never exception text.
        route = request.scope.get("route")
        route_template = getattr(route, "path", "<unmatched>")
        request_logger.info(
            "http_request",
            extra={
                "http_method": request.method,
                "http_route": route_template,
                "http_status_code": 500,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
        )
        raise

    route = request.scope.get("route")
    route_template = getattr(route, "path", "<unmatched>")
    request_logger.info(
        "http_request",
        extra={
            "http_method": request.method,
            "http_route": route_template,
            "http_status_code": response.status_code,
            "duration_ms": round((perf_counter() - started) * 1000, 2),
        },
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response


@app.exception_handler(sqlite3.OperationalError)
async def sqlite_operational_error_handler(_: Request, _exc: sqlite3.OperationalError) -> JSONResponse:
    """Return a stable, sanitized response when SQLite operations are unavailable."""
    return JSONResponse(status_code=503, content={"detail": "Workflow database is unavailable"})


@app.exception_handler(WorkflowDataError)
async def workflow_data_error_handler(_: Request, _exc: WorkflowDataError) -> JSONResponse:
    """Return a stable, sanitized error for malformed persisted workflow data."""
    return JSONResponse(status_code=500, content={"detail": "Stored workflow data is invalid"})


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
    return {"status": "ok", "mode": execution_settings.mode}


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


@app.get("/policy/hash", dependencies=[Depends(require_owner)])
def get_policy_hash() -> dict[str, str]:
    return {
        "policy_hash": hash_policy(DEMO_POLICY),
        "format_version": POLICY_HASH_FORMAT_VERSION,
    }


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
    if outcome == "pending_cap_exceeded":
        raise HTTPException(
            status_code=429,
            detail={"code": "pending_proposal_limit_exceeded", "message": "Pending proposal limit exceeded"},
        )
    if outcome == "replayed":
        response.status_code = 200
    return _record_response(record)


@app.get("/transactions", response_model=list[TransactionResponse], dependencies=[Depends(require_owner)])
def list_transactions(limit: int = Query(default=50, ge=1, le=100)) -> list[TransactionResponse]:
    """List recent persisted workflow records for the local owner-facing UI."""
    return [_record_response(record) for record in store.list_recent(limit)]


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


@app.get(
    "/agent/transactions/{transaction_id}",
    response_model=AgentTransactionStatusResponse,
    dependencies=[Depends(require_agent)],
)
def get_agent_transaction_status(
    transaction_id: str,
) -> AgentTransactionStatusResponse:
    record = store.get(transaction_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return AgentTransactionStatusResponse(
        transaction_id=record["transaction_id"],
        status=record["status"],
        execution_reference=record["execution_reference"],
        updated_at=record["updated_at"],
    )


@app.post("/transactions/{transaction_id}/approve", response_model=TransactionResponse,
          dependencies=[Depends(require_owner)])
def approve_transaction(transaction_id: str, request: ApprovalRequest) -> TransactionResponse:
    if store.is_paused():
        raise HTTPException(
            status_code=409,
            detail={"code": "wallet_paused", "message": "Wallet is paused; approvals are disabled"},
        )
    outcome, record = controller.approve(transaction_id, request.transaction_fingerprint)
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Transaction not found")
    if outcome == "fingerprint_mismatch":
        raise HTTPException(status_code=409, detail="Approval fingerprint does not match transaction")
    if outcome == "proposal_integrity_mismatch":
        raise HTTPException(status_code=409, detail="Persisted proposal failed integrity verification")
    if outcome == "blocked":
        raise HTTPException(status_code=409, detail="Blocked transactions cannot be approved")
    if outcome == "wallet_paused":
        raise HTTPException(
            status_code=409,
            detail={"code": "wallet_paused", "message": "Wallet is paused; approvals are disabled"},
        )
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
    if store.is_paused():
        raise HTTPException(
            status_code=409,
            detail={"code": "wallet_paused", "message": "Wallet is paused; execution is disabled"},
        )
    executor = None
    if execution_settings.mode == "base_sepolia":
        try:
            executor = BaseSepoliaExecutor(execution_settings, DEMO_POLICY)
        except ExecutionConfigurationError as exc:
            raise HTTPException(status_code=503, detail="Base Sepolia execution is not configured") from exc
    elif execution_settings.mode == "base_sepolia_vault":
        try:
            executor = BaseSepoliaVaultExecutor(execution_settings)
        except ExecutionConfigurationError as exc:
            raise HTTPException(
                status_code=503,
                detail="Base Sepolia vault execution is not configured",
            ) from exc
    try:
        outcome, record = controller.execute(transaction_id, executor)
    except (
        PreflightError,
        UnsignedTransactionInvariantError,
        VaultExecutionError,
        VaultTransactionInvariantError,
    ) as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except (RpcConfigurationError, RpcRequestError, OnchainSubmissionError) as exc:
        raise HTTPException(status_code=502, detail="Base Sepolia transaction submission failed") from exc
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Transaction not found")
    if outcome == "blocked":
        raise HTTPException(status_code=403, detail="Policy-blocked transactions cannot execute")
    if outcome == "proposal_integrity_mismatch":
        raise HTTPException(status_code=409, detail="Persisted proposal failed integrity verification")
    if outcome == "wallet_paused":
        raise HTTPException(
            status_code=409,
            detail={"code": "wallet_paused", "message": "Wallet is paused; execution is disabled"},
        )
    if outcome == "approval_required":
        raise HTTPException(status_code=409, detail="Human approval is required before execution")
    if outcome == "already_executed":
        raise HTTPException(status_code=409, detail="Transaction has already been executed")
    if outcome == "already_submitted":
        raise HTTPException(status_code=409, detail="Transaction has already been submitted")
    if outcome in {"invalid_state", "approval_mismatch"}:
        raise HTTPException(status_code=409, detail="Transaction is not in an executable state")
    return _record_response(record or {})


@app.get(
    "/transactions/{transaction_id}/receipt",
    response_model=TransactionReceiptResponse,
    dependencies=[Depends(require_owner)],
)
def get_transaction_receipt(transaction_id: str) -> TransactionReceiptResponse:
    record = store.get(transaction_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if execution_settings.mode not in {"base_sepolia", "base_sepolia_vault"} or record["status"] not in {
        "submitting_onchain",
        "submitted_onchain",
    }:
        raise HTTPException(status_code=409, detail="Transaction has no Base Sepolia receipt")
    try:
        if execution_settings.mode == "base_sepolia_vault":
            executor = BaseSepoliaVaultExecutor(execution_settings)
        else:
            executor = BaseSepoliaExecutor(execution_settings, DEMO_POLICY)
        receipt = executor.receipt(record["execution_reference"])
    except ExecutionConfigurationError as exc:
        raise HTTPException(status_code=503, detail="Base Sepolia execution is not configured") from exc
    except (RpcConfigurationError, RpcRequestError) as exc:
        raise HTTPException(status_code=502, detail="Base Sepolia receipt is unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Base Sepolia receipt is unavailable") from exc
    return TransactionReceiptResponse(
        transaction_id=transaction_id,
        transaction_hash=receipt["transaction_hash"],
        chain_id=BASE_SEPOLIA_CHAIN_ID,
        status=receipt["status"],
        block_number=receipt["block_number"],
    )


@app.post(
    "/transactions/{transaction_id}/cancel",
    response_model=TransactionResponse,
    dependencies=[Depends(require_owner)],
)
def cancel_transaction(transaction_id: str) -> TransactionResponse:
    outcome, record = controller.cancel(transaction_id)
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Transaction not found")
    if outcome == "invalid_state":
        raise HTTPException(status_code=409, detail="Transaction cannot be cancelled in its current state")
    if outcome == "already_cancelled":
        raise HTTPException(status_code=409, detail="Transaction has already been cancelled")
    return _record_response(record or {})


@app.post(
    "/transactions/{transaction_id}/reject",
    response_model=TransactionResponse,
    dependencies=[Depends(require_owner)],
)
def reject_transaction(transaction_id: str) -> TransactionResponse:
    outcome, record = controller.reject(transaction_id)
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Transaction not found")
    if outcome == "invalid_state":
        raise HTTPException(status_code=409, detail="Transaction cannot be rejected in its current state")
    return _record_response(record or {})


def _market_quote(asset_class: str, symbol: str) -> MarketDataResponse:
    """Return a validated, read-only crypto or equity quote."""
    try:
        quote = market_data_provider.get_quote(asset_class, symbol)
    except UnsupportedMarketSymbol as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail="Market-data provider unavailable") from exc
    return MarketDataResponse(**quote.__dict__)


@app.get(
    "/market-data/{asset_class}/{symbol}",
    response_model=MarketDataResponse,
    dependencies=[Depends(require_owner)],
)
def get_market_quote(asset_class: str, symbol: str) -> MarketDataResponse:
    return _market_quote(asset_class, symbol)


@app.get(
    "/agent/market-data/{asset_class}/{symbol}",
    response_model=MarketDataResponse,
    dependencies=[Depends(require_agent)],
)
def get_agent_market_quote(asset_class: str, symbol: str) -> MarketDataResponse:
    return _market_quote(asset_class, symbol)


@app.post("/pause", dependencies=[Depends(require_owner)])
def pause_transactions(request: PauseRequest | None = None) -> dict[str, object]:
    reason = request.reason.strip() if request and request.reason else None
    return store.set_pause_state(True, reason, actor_role="owner")


@app.post("/resume", dependencies=[Depends(require_owner)])
def resume_transactions(request: PauseRequest | None = None) -> dict[str, object]:
    reason = request.reason.strip() if request and request.reason else None
    return store.set_pause_state(False, reason, actor_role="owner")


# Read-only chain inspection; this router exposes no signing or submission methods.
app.include_router(base_sepolia_router)


# Serve the dependency-free local control centre without a catch-all static mount.
_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/", include_in_schema=False)
def frontend_index() -> HTMLResponse:
    """Serve the local control centre with a nonce-scoped script policy."""
    nonce = secrets.token_urlsafe(18)
    html = (_frontend_dir / "index.html").read_text(encoding="utf-8")
    html = html.replace("<script>", f'<script nonce="{nonce}">', 1)
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": (
                f"default-src 'self'; script-src 'nonce-{nonce}'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
            ),
            "Cache-Control": "no-store",
        },
    )
