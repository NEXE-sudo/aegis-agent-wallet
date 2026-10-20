"""Aegis Agent Wallet API with simulated or explicitly gated Base Sepolia execution."""
import logging
import os
import re
import secrets
import sqlite3
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from time import monotonic, perf_counter, time
from typing import Literal

from eth_utils import keccak
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from app.auth import require_agent, require_owner
from app.chain.base_sepolia import (
    BASE_SEPOLIA_CHAIN_ID,
    BASE_SEPOLIA_USDC_ADDRESS,
    BaseSepoliaRpc,
    RpcConfigurationError,
    RpcRequestError,
    validate_address,
)
from app.chain.execution import (
    BaseSepoliaVaultExecutor,
    ExecutionConfigurationError,
    ExecutionSettings,
    VaultExecutionError,
    VaultTransactionInvariantError,
)
from app.chain.router import router as base_sepolia_router
from app.governance import GovernanceDataError, GovernanceStore, router as governance_router
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
from app.workflow.controller import TransactionController, fingerprint_proposal
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
    approval_expires_at: str | None = None
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
    version="1.13.0",
    lifespan=lifespan,
)

app.state.controller = controller
app.state.governance_store = GovernanceStore(store.db_path)
app.include_router(governance_router)


request_logger = logging.getLogger("aegis.request")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_RATE_LIMIT_WINDOW_SECONDS = 60.0
_RATE_LIMIT_MAX_REQUESTS = max(1, int(os.environ.get("AEGIS_RATE_LIMIT_REQUESTS", "120")))
_rate_lock = Lock()
_rate_windows: dict[str, deque[float]] = {}
_metrics_lock = Lock()
_http_metrics: dict[tuple[str, str, int], int] = {}


def _record_http_metric(method: str, route: str, status: int) -> None:
    key = (method, route, status)
    with _metrics_lock:
        _http_metrics[key] = _http_metrics.get(key, 0) + 1


def _consume_write_limit(client_key: str, now: float) -> tuple[bool, int]:
    """Apply a process-local sliding-window limit to mutating requests."""
    cutoff = now - _RATE_LIMIT_WINDOW_SECONDS
    with _rate_lock:
        requests = _rate_windows.setdefault(client_key, deque())
        while requests and requests[0] <= cutoff:
            requests.popleft()
        if len(requests) >= _RATE_LIMIT_MAX_REQUESTS:
            return False, max(1, int(_RATE_LIMIT_WINDOW_SECONDS - (now - requests[0])))
        requests.append(now)
        return True, _RATE_LIMIT_MAX_REQUESTS - len(requests)


@app.middleware("http")
async def log_http_requests(request: Request, call_next):
    """Correlate requests and log bounded route metadata without credentials or bodies."""
    started = perf_counter()
    supplied_id = request.headers.get("X-Request-ID", "")
    request_id = supplied_id if _REQUEST_ID_PATTERN.fullmatch(supplied_id) else uuid.uuid4().hex
    request.state.request_id = request_id

    response = None
    status_code = 500
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        client_key = request.client.host if request.client else "unknown"
        allowed, remaining = _consume_write_limit(client_key, monotonic())
        if not allowed:
            response = JSONResponse(
                status_code=429,
                content={"detail": {"code": "rate_limit_exceeded", "message": "Too many write requests; retry after the rate-limit window."}},
                headers={"Retry-After": str(remaining)},
            )
            status_code = 429

    try:
        if response is None:
            response = await call_next(request)
            status_code = response.status_code
    except Exception:
        route = request.scope.get("route")
        route_template = getattr(route, "path", "<unmatched>")
        _record_http_metric(request.method, route_template, 500)
        request_logger.info(
            "http_request",
            extra={
                "request_id": request_id,
                "http_method": request.method,
                "http_route": route_template,
                "http_status_code": 500,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
        )
        raise

    route = request.scope.get("route")
    route_template = getattr(route, "path", "<unmatched>")
    _record_http_metric(request.method, route_template, status_code)
    request_logger.info(
        "http_request",
        extra={
            "request_id": request_id,
            "http_method": request.method,
            "http_route": route_template,
            "http_status_code": status_code,
            "duration_ms": round((perf_counter() - started) * 1000, 2),
        },
    )
    response.headers.setdefault("X-Request-ID", request_id)
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


@app.exception_handler(GovernanceDataError)
async def governance_data_error_handler(_: Request, _exc: GovernanceDataError) -> JSONResponse:
    """Return a stable, sanitized error for malformed persisted governance data."""
    return JSONResponse(status_code=500, content={"detail": "Stored governance data is invalid"})


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
    response = dict(record)
    response["approval_expires_at"] = None
    approved_at = response.get("approved_at")
    if response.get("status") == "approved" and isinstance(approved_at, str):
        try:
            approved_time = datetime.fromisoformat(approved_at)
            if approved_time.tzinfo is None:
                approved_time = approved_time.replace(tzinfo=UTC)
            response["approval_expires_at"] = (
                approved_time + timedelta(seconds=DEMO_POLICY.approval_expires_seconds)
            ).isoformat()
        except ValueError:
            # Malformed persisted timestamps must not produce a fabricated deadline.
            response["approval_expires_at"] = None
    return TransactionResponse(**response)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe: the process is running, without asserting database readiness."""
    return {"status": "ok", "mode": execution_settings.mode}


@app.get("/ready")
def readiness() -> JSONResponse:
    """Check workflow and governance storage reads without exposing database details."""
    checks: dict[str, str] = {}
    try:
        store.list_recent(1)
        checks["workflow_store"] = "ok"
    except Exception:
        checks["workflow_store"] = "unavailable"
    try:
        app.state.governance_store.get_active_mandate()
        app.state.governance_store.list_payees()
        checks["governance_store"] = "ok"
    except Exception:
        checks["governance_store"] = "unavailable"
    ready = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """Expose bounded-cardinality process-local request counters in Prometheus text format."""
    with _metrics_lock:
        snapshot = sorted(_http_metrics.items())
    lines = [
        "# HELP aegis_http_requests_total HTTP requests handled by this process.",
        "# TYPE aegis_http_requests_total counter",
    ]
    for (method, route, status), count in snapshot:
        safe_route = route.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(
            f'aegis_http_requests_total{{method="{method}",route="{safe_route}",status="{status}"}} {count}'
        )
    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


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


@app.get("/policy/config", dependencies=[Depends(require_owner)])
def get_policy_config() -> dict[str, object]:
    """Expose the active demo agent policy without credentials or mutable controls."""
    allowed_addresses = {address.lower() for address in DEMO_POLICY.allowed_token_addresses}
    allowed_tokens = [
        {
            "address": address.lower(),
            "symbol": token.symbol,
            "chain_id": token.chain_id,
            "decimals": token.decimals,
        }
        for address, token in sorted(
            DEMO_POLICY.trusted_tokens.items(), key=lambda item: item[0].lower()
        )
        if address.lower() in allowed_addresses
    ]
    return {
        "agent_id": DEMO_POLICY.agent_id,
        "enabled": DEMO_POLICY.enabled,
        "allowed_chain_ids": sorted(DEMO_POLICY.allowed_chain_ids),
        "allowed_tokens": allowed_tokens,
        "allowed_token_symbols": sorted(DEMO_POLICY.allowed_token_symbols),
        "allowed_recipients": sorted(
            address.lower() for address in DEMO_POLICY.allowed_recipients
        ),
        "max_transaction_base_units": DEMO_POLICY.max_transaction_base_units,
        "daily_limit_base_units": DEMO_POLICY.daily_limit_base_units,
        "approval_threshold_base_units": DEMO_POLICY.approval_threshold_base_units,
        "approval_expires_seconds": DEMO_POLICY.approval_expires_seconds,
        "unknown_recipient_requires_approval": DEMO_POLICY.unknown_recipient_requires_approval,
        "recipient_token_allowlist": {
            recipient.lower(): sorted(token.lower() for token in tokens)
            for recipient, tokens in sorted(
                DEMO_POLICY.recipient_token_allowlist.items(),
                key=lambda item: item[0].lower(),
            )
        },
        "recipient_daily_limits_base_units": {
            recipient.lower(): limit
            for recipient, limit in sorted(
                DEMO_POLICY.recipient_daily_limits_base_units.items(),
                key=lambda item: item[0].lower(),
            )
        },
        "recipient_transaction_limits_base_units": {
            recipient.lower(): limit
            for recipient, limit in sorted(
                DEMO_POLICY.recipient_transaction_limits_base_units.items(),
                key=lambda item: item[0].lower(),
            )
        },
    }


@app.get("/spending/summary", dependencies=[Depends(require_owner)])
def get_spending_summary() -> dict[str, object]:
    """Summarize today's local workflow spend and active reservations per allowed token."""
    allowed_addresses = {address.lower() for address in DEMO_POLICY.allowed_token_addresses}
    tokens: list[dict[str, object]] = []
    for address, token in sorted(
        DEMO_POLICY.trusted_tokens.items(), key=lambda item: item[0].lower()
    ):
        if (
            address.lower() not in allowed_addresses
            or token.chain_id not in DEMO_POLICY.allowed_chain_ids
        ):
            continue
        spent = store.daily_spend(token.chain_id, address)
        limit = DEMO_POLICY.daily_limit_base_units
        tokens.append({
            "chain_id": token.chain_id,
            "token_address": address.lower(),
            "symbol": token.symbol,
            "decimals": token.decimals,
            "spent_base_units": spent,
            "daily_limit_base_units": limit,
            "remaining_base_units": max(0, limit - spent),
            "limit_exceeded": spent > limit,
        })
    return {
        "as_of_date_utc": datetime.now(UTC).date().isoformat(),
        "accounting_source": "local_workflow",
        "tokens": tokens,
    }


_ZERO_ADDRESS = "0x" + "0" * 40
_VAULT_APPROVAL_SIGNATURE = "approveTransfer(address,address,uint256,bytes32,uint256)"
_VAULT_APPROVAL_SELECTOR = "0x" + keccak(text=_VAULT_APPROVAL_SIGNATURE)[:4].hex()
_SUGGESTED_VAULT_APPROVAL_LIFETIME_SECONDS = 3600


def _configured_vault_address() -> str:
    try:
        return validate_address(os.environ.get("AEGIS_VAULT_ADDRESS", ""))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="A valid AEGIS_VAULT_ADDRESS is required for vault operations",
        ) from exc


def _address_word(address: str) -> str:
    return validate_address(address)[2:].rjust(64, "0")


def _decode_vault_words(data: str, word_count: int, function_name: str) -> list[str]:
    if (
        not isinstance(data, str)
        or re.fullmatch(rf"0x[0-9a-fA-F]{{{word_count * 64}}}", data) is None
    ):
        raise RpcRequestError(f"AegisVault {function_name}() returned malformed data")
    return [
        data[2 + index * 64:2 + (index + 1) * 64].lower()
        for index in range(word_count)
    ]


def _decode_vault_bool(word: str, function_name: str) -> bool:
    value = int(word, 16)
    if value not in (0, 1):
        raise RpcRequestError(f"AegisVault {function_name}() returned malformed boolean data")
    return bool(value)


def _vault_read(rpc: BaseSepoliaRpc, vault: str, signature: str, arguments: str = "") -> str:
    selector = "0x" + keccak(text=signature)[:4].hex()
    return rpc.call(_ZERO_ADDRESS, vault, selector + arguments)


@app.get(
    "/transactions/{transaction_id}/vault-approval",
    dependencies=[Depends(require_owner)],
)
def get_vault_approval(transaction_id: str) -> dict[str, object]:
    record = store.get(transaction_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    proposal = record["proposal"]
    try:
        proposal_model = TransactionProposal(**proposal)
        token_address = validate_address(proposal_model.token_address)
        recipient = validate_address(proposal_model.recipient)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "proposal_integrity_mismatch",
                "message": "Persisted proposal failed integrity verification.",
            },
        ) from exc
    if (
        proposal_model.chain_id != BASE_SEPOLIA_CHAIN_ID
        or token_address not in {
            validate_address(address) for address in DEMO_POLICY.allowed_token_addresses
        }
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "vault_approval_token_unsupported",
                "message": "This proposal does not use a token supported by the Aegis vault policy.",
            },
        )

    amount = proposal_model.amount_base_units
    if (
        type(amount) is not int
        or amount <= 0
        or record["status"] not in {"awaiting_approval", "approved"}
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "vault_approval_unavailable",
                "message": "The proposal is not in a state eligible for on-chain approval.",
            },
        )

    allowed_recipients = {
        validate_address(address) for address in DEMO_POLICY.allowed_recipients
    }
    requires_approval = (
        recipient not in allowed_recipients
        or amount >= DEMO_POLICY.approval_threshold_base_units
    )
    if not requires_approval:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "vault_approval_not_required",
                "message": "The Aegis policy does not require on-chain approval for this proposal.",
            },
        )

    fingerprint = fingerprint_proposal(proposal_model)
    if (
        not isinstance(record.get("fingerprint"), str)
        or record["fingerprint"] != fingerprint
        or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "proposal_integrity_mismatch",
                "message": "Persisted proposal failed integrity verification.",
            },
        )

    vault_address = _configured_vault_address()
    reference = "0x" + fingerprint
    expiry = int(time()) + _SUGGESTED_VAULT_APPROVAL_LIFETIME_SECONDS
    calldata = (
        _VAULT_APPROVAL_SELECTOR
        + _address_word(token_address)
        + _address_word(recipient)
        + amount.to_bytes(32, "big").hex()
        + fingerprint
        + expiry.to_bytes(32, "big").hex()
    )
    command = (
        f'cast send "{vault_address}" "{_VAULT_APPROVAL_SIGNATURE}" '
        f'"{token_address}" "{recipient}" {amount} "{reference}" {expiry} '
        '--rpc-url "$BASE_SEPOLIA_RPC_URL" --private-key "$OWNER_PRIVATE_KEY"'
    )
    return {
        "vault_address": vault_address,
        "function_signature": _VAULT_APPROVAL_SIGNATURE,
        "arguments": {
            "token": token_address,
            "to": recipient,
            "amount": amount,
            "ref": reference,
            "expiry": expiry,
        },
        "calldata": calldata,
        "cast_send_command": command,
    }


@app.get(
    "/chain/base-sepolia/vault/status",
    dependencies=[Depends(require_owner)],
)
def get_vault_status() -> dict[str, object]:
    vault_address = _configured_vault_address()
    token_addresses = sorted(
        validate_address(address) for address in DEMO_POLICY.allowed_token_addresses
    )
    expected_recipients = sorted(
        {
            validate_address(address)
            for address in (
                DEMO_POLICY.allowed_recipients
                | set(DEMO_POLICY.recipient_token_allowlist)
            )
        }
    )

    try:
        with BaseSepoliaRpc() as rpc:
            chain_id = rpc.chain_id()
            if chain_id != BASE_SEPOLIA_CHAIN_ID:
                raise HTTPException(
                    status_code=503,
                    detail="Configured RPC endpoint is not Base Sepolia (84532)",
                )
            code = rpc.get_code(vault_address)
            if code in {"0x", "0x0"}:
                raise HTTPException(
                    status_code=503,
                    detail="Configured AegisVault address has no contract code",
                )

            owner_word = _decode_vault_words(
                _vault_read(rpc, vault_address, "owner()"), 1, "owner"
            )[0]
            agent_word = _decode_vault_words(
                _vault_read(rpc, vault_address, "agent()"), 1, "agent"
            )[0]
            paused_word = _decode_vault_words(
                _vault_read(rpc, vault_address, "paused()"), 1, "paused"
            )[0]
            if owner_word[:24] != "0" * 24 or agent_word[:24] != "0" * 24:
                raise RpcRequestError("AegisVault returned malformed address data")

            tokens: list[dict[str, object]] = []
            recipients: list[dict[str, object]] = []
            differences: list[dict[str, object]] = []
            recipient_token_allowlist = {
                validate_address(recipient): {
                    validate_address(token) for token in addresses
                }
                for recipient, addresses in DEMO_POLICY.recipient_token_allowlist.items()
            }
            allowed_recipients = {
                validate_address(address) for address in DEMO_POLICY.allowed_recipients
            }
            for token_address in token_addresses:
                policy_data = _vault_read(
                    rpc,
                    vault_address,
                    "tokenPolicy(address)",
                    _address_word(token_address),
                )
                allowed_word, per_tx_word, daily_word, threshold_word = _decode_vault_words(
                    policy_data, 4, "tokenPolicy"
                )
                onchain_policy = {
                    "allowed": _decode_vault_bool(allowed_word, "tokenPolicy"),
                    "per_tx_cap_base_units": int(per_tx_word, 16),
                    "daily_cap_base_units": int(daily_word, 16),
                    "approval_threshold_base_units": int(threshold_word, 16),
                }
                spend_data = _vault_read(
                    rpc,
                    vault_address,
                    "todaySpend(address)",
                    _address_word(token_address),
                )
                spend_word = _decode_vault_words(spend_data, 1, "todaySpend")[0]
                today_spend = int(spend_word, 16)
                expected_policy = {
                    "allowed": True,
                    "per_tx_cap_base_units": DEMO_POLICY.max_transaction_base_units,
                    "daily_cap_base_units": DEMO_POLICY.daily_limit_base_units,
                    "approval_threshold_base_units": DEMO_POLICY.approval_threshold_base_units,
                }
                for field_name, expected in expected_policy.items():
                    actual = onchain_policy[field_name]
                    if actual != expected:
                        differences.append({
                            "field": f"tokens.{token_address}.{field_name}",
                            "expected": expected,
                            "actual": actual,
                        })
                token_metadata = next(
                    (
                        metadata
                        for address, metadata in DEMO_POLICY.trusted_tokens.items()
                        if validate_address(address) == token_address
                    ),
                    None,
                )
                tokens.append({
                    "address": token_address,
                    "symbol": token_metadata.symbol if token_metadata else None,
                    "decimals": token_metadata.decimals if token_metadata else None,
                    "onchain_policy": onchain_policy,
                    "today_spend_base_units": today_spend,
                    "aegis_policy": expected_policy,
                })

                for recipient in expected_recipients:
                    recipient_data = _vault_read(
                        rpc,
                        vault_address,
                        "recipients(address)",
                        _address_word(recipient),
                    )
                    recipient_word = _decode_vault_words(
                        recipient_data, 1, "recipients"
                    )[0]
                    onchain_allowed = _decode_vault_bool(
                        recipient_word, "recipients"
                    )
                    token_allowlist = recipient_token_allowlist.get(recipient)
                    aegis_allowed = (
                        recipient in allowed_recipients
                        and (
                            token_allowlist is None
                            or token_address in token_allowlist
                        )
                    )
                    if onchain_allowed != aegis_allowed:
                        differences.append({
                            "field": f"recipients.{recipient}.{token_address}",
                            "expected": aegis_allowed,
                            "actual": onchain_allowed,
                        })
                    recipients.append({
                        "address": recipient,
                        "token": token_address,
                        "aegis_allowed": aegis_allowed,
                        "onchain_allowed": onchain_allowed,
                    })
    except HTTPException:
        raise
    except RpcConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (RpcRequestError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="AegisVault status response is unavailable or malformed") from exc

    return {
        "vault_address": vault_address,
        "chain_id": chain_id,
        "owner": "0x" + owner_word[24:],
        "agent": "0x" + agent_word[24:],
        "paused": _decode_vault_bool(paused_word, "paused"),
        "tokens": tokens,
        "recipients": recipients,
        "in_sync": not differences,
        "differences": differences,
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
def list_transactions(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[TransactionResponse]:
    """List persisted workflow records for the local owner-facing UI."""
    return [_record_response(record) for record in store.list_recent(limit, offset=offset)]


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


def _require_current_governance_for_transaction(transaction_id: str) -> None:
    """Block governance-created proposals when their governing records have changed."""
    record = store.get(transaction_id)
    if record is None or record["status"] in {
        "blocked", "cancelled", "rejected", "executed_simulated", "submitted_onchain"
    }:
        return
    governance = app.state.governance_store
    try:
        decision = governance.get_decision_for_transaction(transaction_id)
    except GovernanceDataError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "governance_integrity_failed", "message": "Governance evidence failed integrity verification."},
        ) from exc
    if decision is None:
        return
    mandate = governance.get_active_mandate()
    if mandate is None or mandate["payload_hash"] != decision.get("mandate_hash"):
        raise HTTPException(
            status_code=409,
            detail={"code": "governance_mandate_changed", "message": "The mandate changed after this proposal; requeue the task for owner review."},
        )
    payee_id = decision.get("payee_id")
    payee = governance.get_payee(payee_id) if isinstance(payee_id, str) else None
    if payee is None or payee["payload_hash"] != decision.get("payee_hash"):
        raise HTTPException(
            status_code=409,
            detail={"code": "governance_payee_changed", "message": "The registered payee changed after this proposal; requeue the task for owner review."},
        )


@app.post("/transactions/{transaction_id}/approve", response_model=TransactionResponse,
          dependencies=[Depends(require_owner)])
def approve_transaction(transaction_id: str, request: ApprovalRequest) -> TransactionResponse:
    _require_current_governance_for_transaction(transaction_id)
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
    _require_current_governance_for_transaction(transaction_id)
    if store.is_paused():
        raise HTTPException(
            status_code=409,
            detail={"code": "wallet_paused", "message": "Wallet is paused; execution is disabled"},
        )
    executor = None
    if execution_settings.mode == "base_sepolia_vault":
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


@app.post(
    "/agent/transactions/{transaction_id}/execute",
    response_model=TransactionResponse,
    dependencies=[Depends(require_agent)],
)
def execute_agent_transaction(transaction_id: str) -> TransactionResponse:
    """Execute an eligible agent proposal through the on-chain vault only."""
    if execution_settings.mode != "base_sepolia_vault":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "agent_execution_requires_vault",
                "message": "Agent execution is available only through the Base Sepolia vault.",
            },
        )
    record = store.get(transaction_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if record["status"] not in {"ready", "approved"}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "transaction_not_agent_executable",
                "message": "Only ready or approved transactions can be submitted by the agent.",
            },
        )
    # Reuse the owner route's execution path so policy, risk, approval, pause,
    # proposal-integrity, simulation, and receipt-state checks stay centralised.
    return execute_transaction(transaction_id)


@app.get(
    "/transactions/{transaction_id}/receipt",
    response_model=TransactionReceiptResponse,
    dependencies=[Depends(require_owner)],
)
def get_transaction_receipt(transaction_id: str) -> TransactionReceiptResponse:
    record = store.get(transaction_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if execution_settings.mode != "base_sepolia_vault" or record["status"] not in {
        "submitting_onchain",
        "submitted_onchain",
    }:
        raise HTTPException(status_code=409, detail="Transaction has no Base Sepolia vault receipt")
    try:
        executor = BaseSepoliaVaultExecutor(execution_settings)
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
