import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import Browser, Page, expect, sync_playwright

from app.main import app

client = TestClient(app)


@pytest.fixture(scope="session")
def browser() -> Browser:
    with sync_playwright() as playwright:
        browser_instance = playwright.chromium.launch()
        yield browser_instance
        browser_instance.close()


@pytest.fixture
def frontend_url() -> str:
    html = client.get("/").content

    class FrontendHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), FrontendHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def page(browser: Browser, frontend_url: str):
    browser_page = browser.new_page(viewport={"width": 1440, "height": 900})
    yield browser_page
    browser_page.close()


def _mock_api(
    page: Page,
    *,
    transactions: list[dict] | None = None,
    transaction_statuses: list[int] | None = None,
    health_statuses: list[int] | None = None,
    audit_events: list[dict] | None = None,
    policy_config: dict | None = None,
    wallet_balances: dict | None = None,
    receipt_payload: dict | None = None,
) -> list[dict]:
    calls: list[dict] = []
    remaining_transaction_statuses = list(transaction_statuses or [])
    remaining_health_statuses = list(health_statuses or [])

    def fulfill(route, status: int, payload: object) -> None:
        route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps(payload),
        )

    def handle(route) -> None:
        request = route.request
        parsed = urlsplit(request.url)
        if parsed.path == "/":
            route.continue_()
            return

        authorization = request.headers.get("authorization")
        calls.append({
            "method": request.method,
            "path": parsed.path,
            "authorization": authorization,
            "post_data": request.post_data,
        })
        if parsed.path == "/health":
            status = remaining_health_statuses.pop(0) if remaining_health_statuses else 200
            fulfill(
                route,
                status,
                {"status": "ok", "mode": "simulated"}
                if status == 200 else {"detail": "unavailable"},
            )
        elif parsed.path == "/transactions" and request.method == "GET":
            status = remaining_transaction_statuses.pop(0) if remaining_transaction_statuses else 200
            fulfill(route, status, transactions if status == 200 and transactions is not None else [])
        elif parsed.path.startswith("/chain/base-sepolia/address/") and parsed.path.endswith("/balances") and request.method == "GET":
            fulfill(route, 200, wallet_balances or {
                "address": "0x2222222222222222222222222222222222222222",
                "chain_id": 84532,
                "native_balance": {"symbol": "ETH", "decimals": 18, "balance_base_units": 1250000000000000000, "formatted_balance": "1.25"},
                "tokens": [{"address": "0x036cbd53842c5426634e7929541ec2318f3dcf7e", "symbol": "USDC", "decimals": 6, "balance_base_units": 12345678, "formatted_balance": "12.345678"}],
                "read_only": True,
            })
        elif parsed.path == "/policy/config" and request.method == "GET":
            fulfill(route, 200, policy_config or {
                "agent_id": "devops-01",
                "enabled": True,
                "allowed_chain_ids": [84532],
                "allowed_tokens": [{"address": "0x036cbd53842c5426634e7929541ec2318f3dcf7e", "symbol": "USDC", "chain_id": 84532, "decimals": 6}],
                "allowed_token_symbols": ["USDC"],
                "allowed_recipients": ["0x2222222222222222222222222222222222222222"],
                "max_transaction_base_units": 50000000,
                "daily_limit_base_units": 150000000,
                "approval_threshold_base_units": 50000000,
                "approval_expires_seconds": 300,
                "unknown_recipient_requires_approval": True,
                "recipient_token_allowlist": {},
                "recipient_daily_limits_base_units": {},
                "recipient_transaction_limits_base_units": {},
            })
        elif parsed.path.startswith("/transactions/") and parsed.path.endswith("/audit"):
            fulfill(route, 200, audit_events or [])
        elif parsed.path.startswith("/transactions/") and parsed.path.endswith("/receipt") and request.method == "GET":
            fulfill(route, 200, receipt_payload or {
                "transaction_id": parsed.path.split("/")[-2],
                "transaction_hash": "0x" + "a" * 64,
                "chain_id": 84532,
                "status": "success",
                "block_number": 123456,
            })
        elif parsed.path.startswith("/market-data/"):
            fulfill(route, 502, {"detail": "provider temporarily unavailable"})
        elif parsed.path == "/transactions/propose" and request.method == "POST":
            fulfill(route, 503, {"detail": "workflow temporarily unavailable"})
        elif parsed.path.endswith("/approve") and request.method == "POST":
            fulfill(route, 409, {"detail": "Transaction is not awaiting approval"})
        elif parsed.path.endswith("/execute") and request.method == "POST":
            fulfill(route, 503, {"detail": "workflow temporarily unavailable"})
        else:
            fulfill(route, 404, {"detail": "not found"})

    page.route("**/*", handle)
    return calls


def _connect(page: Page, agent_token: str, owner_token: str) -> None:
    dialog = page.get_by_role("dialog", name="Connect to Aegis")
    if not dialog.is_visible():
        page.get_by_role("button", name="Connect to local API").click()
    page.get_by_label("Agent bearer token").fill(agent_token)
    page.get_by_label("Owner bearer token").fill(owner_token)
    page.get_by_role("button", name="Connect", exact=True).click()


def test_browser_connection_modal_lifecycle_and_disconnect_clear_memory(page: Page, frontend_url: str):
    calls = _mock_api(page)
    page.goto(frontend_url)

    opener = page.get_by_role("button", name="Connect to local API")
    opener.click()
    dialog = page.get_by_role("dialog", name="Connect to Aegis")
    expect(dialog).to_be_visible()
    agent_input = page.get_by_label("Agent bearer token")
    owner_input = page.get_by_label("Owner bearer token")
    expect(agent_input).to_be_focused()

    page.keyboard.press("Tab")
    expect(owner_input).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(agent_input).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(page.get_by_role("button", name="Close")).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(page.get_by_role("button", name="Connect", exact=True)).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()

    _connect(page, "test-agent-token", "test-owner-token")
    expect(page.locator("#connection-status")).to_have_text("Agent token stored in memory")
    page.wait_for_load_state("networkidle")
    assert any(
        call["path"] == "/transactions"
        and call["authorization"] == "Bearer test-owner-token"
        for call in calls
    )
    assert any(call["path"] == "/health" and call["authorization"] is None for call in calls)

    page.locator('nav [data-view="settings"]').click()
    page.get_by_role("button", name="Disconnect").click()
    expect(page.locator("#connection-status")).to_have_text("No active session")
    expect(page.locator("#settings-status")).to_have_text("Disconnected")
    assert page.evaluate(
        "() => ({local: localStorage.length, session: sessionStorage.length, cookie: document.cookie})"
    ) == {"local": 0, "session": 0, "cookie": ""}
    assert "test-agent-token" not in page.locator("body").inner_text()
    assert "test-owner-token" not in page.locator("body").inner_text()
    assert "test-agent-token" not in page.url
    assert "test-owner-token" not in page.url

    calls_before_refresh = len(calls)
    with page.expect_response(lambda response: urlsplit(response.url).path == "/health"):
        page.get_by_role("button", name="Refresh data").click()
    expect(page.locator("#connection-status")).to_have_text("No active session")
    assert not any(call["path"] == "/transactions" for call in calls[calls_before_refresh:])
    assert all(call["authorization"] is None for call in calls[calls_before_refresh:])


def test_browser_connection_failures_are_visible_and_retryable(page: Page, frontend_url: str):
    calls = _mock_api(
        page,
        health_statuses=[200, 503, 200, 200],
        transaction_statuses=[401, 200],
    )
    page.goto(frontend_url)

    _connect(page, "agent-token", "owner-token")
    expect(page.locator("#connection-feedback")).to_contain_text(
        "The API or workflow database is unavailable."
    )
    connect_button = page.get_by_role("button", name="Connect", exact=True)
    expect(connect_button).to_be_enabled()
    expect(page.locator("#connection-status")).to_have_text("No active session")

    _connect(page, "agent-token", "owner-token")
    expect(page.locator("#connection-feedback")).to_contain_text(
        "Authentication failed. Check the token for this operation."
    )
    expect(connect_button).to_be_enabled()
    expect(page.locator("#connection-status")).to_have_text("No active session")

    _connect(page, "agent-token", "owner-token")
    expect(page.locator("#connection-status")).to_have_text("Agent token stored in memory")
    assert sum(call["path"] == "/transactions" for call in calls) == 2


def test_browser_mobile_workflow_escaping_and_market_unavailable_state(page: Page, frontend_url: str):
    transaction_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    recipient = "0x" + "a" * 40
    fingerprint = "b" * 64
    malicious_text = '<img src=x onerror="window.__unexpectedMarkup = true">'
    transactions = [{
        "transaction_id": transaction_id,
        "status": "awaiting_approval",
        "fingerprint": fingerprint,
        "risk_level": "medium",
        "risk_score": 35,
        "policy_decision": "require_approval",
        "policy_reasons": [malicious_text],
        "risk_reasons": [],
        "proposal": {
            "recipient": recipient,
            "amount_base_units": 2500000,
            "token_symbol": "USDC",
            "token_decimals": 6,
        },
        "created_at": "2026-10-15T10:00:00Z",
        "updated_at": "2026-10-15T10:00:00Z",
    }]
    audit_events = [{
        "event_id": "event-1",
        "transaction_id": transaction_id,
        "actor_role": malicious_text,
        "event_type": "proposal_created",
        "from_status": None,
        "to_status": "awaiting_approval",
        "fingerprint": fingerprint,
        "created_at": "2026-10-15T10:00:00Z",
        "details": {"message": malicious_text},
    }]
    _mock_api(page, transactions=transactions, audit_events=audit_events)
    page.goto(frontend_url)
    _connect(page, "agent-token", "owner-token")
    expect(page.locator("#connection-status")).to_have_text("Agent token stored in memory")

    page.locator('nav [data-view="markets"]').click()
    expect(page.locator("#market-stale")).to_have_text("UNAVAILABLE")
    expect(page.locator("#market-price")).to_have_text("—")
    expect(page.locator("#market-feedback")).to_contain_text(
        "The market-data provider is unavailable."
    )
    expect(page.locator("#market-kind")).to_contain_text("informational")

    page.get_by_role("button", name="Approvals").click()
    approval = page.locator(".approval-card")
    expect(approval).to_be_visible()
    expect(approval).to_contain_text(malicious_text)
    assert page.locator(".approval-card img").count() == 0
    assert page.evaluate("() => window.__unexpectedMarkup !== true")

    for width in (1440, 375):
        page.set_viewport_size({"width": width, "height": 900})
        dimensions = page.evaluate(
            """() => {
              const card = document.querySelector('.approval-card');
              return {
                viewport: document.documentElement.clientWidth,
                page: document.documentElement.scrollWidth,
                card: card.clientWidth,
                cardContent: card.scrollWidth,
                fingerprint: card.querySelector('.fingerprint').scrollWidth,
                fingerprintBox: card.querySelector('.fingerprint').clientWidth
              };
            }"""
        )
        assert dimensions["page"] <= dimensions["viewport"]
        assert dimensions["cardContent"] <= dimensions["card"]
        assert dimensions["fingerprint"] <= dimensions["fingerprintBox"]

    page.get_by_role("button", name="Inspect").click()
    expect(page.locator(".event-details")).to_contain_text("window.__unexpectedMarkup = true")
    assert page.locator("#audit-detail img").count() == 0
    assert page.locator("#audit-detail svg").count() == 0
    assert page.evaluate("() => window.__unexpectedMarkup !== true")


def test_browser_transaction_failures_keep_auth_and_confirmation_contracts(page: Page, frontend_url: str):
    fingerprint = "c" * 64
    transaction_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    ready_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    transactions = [
        {
            "transaction_id": transaction_id,
            "status": "awaiting_approval",
            "fingerprint": fingerprint,
            "risk_level": "medium",
            "risk_score": 35,
            "policy_decision": "require_approval",
            "policy_reasons": [],
            "risk_reasons": [],
            "proposal": {
                "recipient": "0x" + "a" * 40,
                "amount_base_units": 2500000,
                "token_symbol": "USDC",
                "token_decimals": 6,
            },
            "created_at": "2026-10-15T10:00:00Z",
            "updated_at": "2026-10-15T10:00:00Z",
        },
        {
            "transaction_id": ready_id,
            "status": "ready",
            "fingerprint": "d" * 64,
            "risk_level": "low",
            "risk_score": 5,
            "policy_decision": "allow",
            "policy_reasons": [],
            "risk_reasons": [],
            "proposal": {
                "recipient": "0x" + "b" * 40,
                "amount_base_units": 1000000,
                "token_symbol": "USDC",
                "token_decimals": 6,
            },
            "created_at": "2026-10-15T10:00:00Z",
            "updated_at": "2026-10-15T10:00:00Z",
        },
    ]
    calls = _mock_api(page, transactions=transactions)
    page.goto(frontend_url)
    _connect(page, "agent-token", "owner-token")
    expect(page.locator("#connection-status")).to_have_text("Agent token stored in memory")

    page.locator('nav [data-view="proposals"]').click()
    page.locator("#amount").fill("51")
    page.get_by_role("button", name="Create proposal").click()
    assert not any(call["path"] == "/transactions/propose" for call in calls)

    page.locator("#amount").fill("2.5")
    page.get_by_role("button", name="Create proposal").click()
    expect(page.locator("#proposal-result")).to_be_visible()
    expect(page.locator("#proposal-result")).to_contain_text("Proposal was not confirmed")
    assert not any(call["path"] == "/transactions/propose" and call["method"] == "GET" for call in calls)
    assert any(
        call["path"] == "/transactions/propose"
        and call["method"] == "POST"
        and call["authorization"] == "Bearer agent-token"
        for call in calls
    )

    page.locator('nav [data-view="approvals"]').click()
    page.get_by_role("button", name="Approve").click()
    expect(page.locator("#global-feedback")).to_contain_text("Approval was not confirmed")
    approval_call = next(
        call for call in calls
        if call["path"] == f"/transactions/{transaction_id}/approve"
    )
    assert approval_call["authorization"] == "Bearer owner-token"
    assert json.loads(approval_call["post_data"]) == {
        "transaction_fingerprint": fingerprint,
        "confirmation": "APPROVE",
    }

    confirmation_messages: list[str] = []
    page.on("dialog", lambda dialog: (confirmation_messages.append(dialog.message), dialog.accept()))
    page.locator('nav [data-view="activity"]').click()
    page.get_by_role("button", name="Simulate").click()
    expect(page.locator("#global-feedback")).to_contain_text("Execution request was not confirmed")
    assert confirmation_messages == [
        "Request a simulation only? No transaction will be signed or broadcast."
    ]
    execution_call = next(
        call for call in calls
        if call["path"] == f"/transactions/{ready_id}/execute"
    )
    assert execution_call["authorization"] == "Bearer owner-token"
    assert "confirmed simulated execution" not in page.locator("#global-feedback").inner_text()


def test_browser_receipt_result_stays_visible_in_activity(page: Page, frontend_url: str):
    transaction_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    transaction_hash = "0x" + "a" * 64
    transactions = [{
        "transaction_id": transaction_id,
        "status": "submitted_onchain",
        "fingerprint": "c" * 64,
        "execution_reference": transaction_hash,
        "risk_level": "low",
        "risk_score": 5,
        "policy_decision": "allow",
        "policy_reasons": [],
        "risk_reasons": [],
        "proposal": {
            "recipient": "0x" + "b" * 40,
            "amount_base_units": 1000000,
            "token_symbol": "USDC",
            "token_decimals": 6,
        },
        "created_at": "2026-10-15T10:00:00Z",
        "updated_at": "2026-10-15T10:00:00Z",
    }]
    calls = _mock_api(
        page,
        transactions=transactions,
        receipt_payload={
            "transaction_id": transaction_id,
            "transaction_hash": transaction_hash,
            "chain_id": 84532,
            "status": "success",
            "block_number": 123456,
        },
    )
    page.goto(frontend_url)
    _connect(page, "agent-token", "owner-token")

    page.locator('nav [data-view="activity"]').click()
    page.get_by_role("button", name="Check receipt").click()

    receipt_result = page.locator("#activity-rows .receipt-result")
    expect(receipt_result).to_contain_text("Base Sepolia receipt · success")
    expect(receipt_result).to_contain_text("Chain ID 84532 · Block 123456")
    expect(receipt_result).to_contain_text(transaction_hash)
    expect(receipt_result).to_contain_text("not proof of transaction finality")
    receipt_call = next(call for call in calls if call["path"].endswith("/receipt"))
    assert receipt_call["authorization"] == "Bearer owner-token"



def test_browser_wallet_balances_are_owner_authenticated_and_read_only(page: Page, frontend_url: str):
    calls = _mock_api(page)
    page.goto(frontend_url)
    _connect(page, "agent-token", "owner-token")
    expect(page.locator("#connection-status")).to_have_text("Agent token stored in memory")
    address = "0x2222222222222222222222222222222222222222"
    page.get_by_label("Public wallet address").fill(address)
    page.get_by_role("button", name="Inspect balances").click()
    panel = page.locator("#wallet-balances")
    expect(panel).to_contain_text("1.25 ETH")
    expect(panel).to_contain_text("12.345678 USDC")
    expect(panel).to_contain_text(address)
    balance_call = next(call for call in calls if call["path"].endswith("/balances"))
    assert balance_call["path"] == f"/chain/base-sepolia/address/{address}/balances"
    assert balance_call["authorization"] == "Bearer owner-token"
    assert not any(
        call["path"].endswith("/balances") and call["authorization"] == "Bearer agent-token"
        for call in calls
    )
    assert "private key" in page.locator("#wallet-address-help").inner_text().lower()


def test_browser_policy_configuration_is_owner_read_only(page: Page, frontend_url: str):
    policy_config = {
        "agent_id": "devops-01",
        "enabled": True,
        "allowed_chain_ids": [84532],
        "allowed_tokens": [{
            "address": "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
            "symbol": "USDC",
            "chain_id": 84532,
            "decimals": 6,
        }],
        "allowed_token_symbols": ["USDC"],
        "allowed_recipients": ["0x2222222222222222222222222222222222222222"],
        "max_transaction_base_units": 50000000,
        "daily_limit_base_units": 150000000,
        "approval_threshold_base_units": 50000000,
        "approval_expires_seconds": 300,
        "unknown_recipient_requires_approval": True,
        "recipient_token_allowlist": {},
        "recipient_daily_limits_base_units": {},
        "recipient_transaction_limits_base_units": {},
    }
    calls = _mock_api(page, policy_config=policy_config)
    page.goto(frontend_url)
    _connect(page, "agent-token", "owner-token")
    expect(page.locator("#connection-status")).to_have_text("Agent token stored in memory")
    page.locator('nav [data-view="settings"]').click()
    panel = page.locator("#policy-config-content")
    expect(panel).to_contain_text("50 USDC")
    expect(panel).to_contain_text("150 USDC")
    expect(panel).to_contain_text("300 seconds")
    expect(panel).to_contain_text("0x2222222222222222222222222222222222222222")
    assert any(
        call["path"] == "/policy/config" and call["authorization"] == "Bearer owner-token"
        for call in calls
    )
    assert not any(
        call["path"] == "/policy/config" and call["authorization"] == "Bearer agent-token"
        for call in calls
    )


def test_browser_receipt_rpc_error_remains_visible_in_activity(page: Page, frontend_url: str):
    transaction_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    transactions = [{
        "transaction_id": transaction_id,
        "status": "submitted_onchain",
        "fingerprint": "c" * 64,
        "execution_reference": "0x" + "a" * 64,
        "risk_level": "low",
        "risk_score": 5,
        "policy_decision": "allow",
        "policy_reasons": [],
        "risk_reasons": [],
        "proposal": {"recipient": "0x" + "b" * 40, "amount_base_units": 1000000, "token_symbol": "USDC", "token_decimals": 6},
        "created_at": "2026-10-15T10:00:00Z",
        "updated_at": "2026-10-15T10:00:00Z",
    }]
    _mock_api(page, transactions=transactions)
    page.route("**/transactions/*/receipt", lambda route: route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": "receipt provider unavailable"})))
    page.goto(frontend_url)
    _connect(page, "agent-token", "owner-token")
    page.locator('nav [data-view="activity"]').click()
    page.get_by_role("button", name="Check receipt").click()
    expect(page.locator("#global-feedback")).to_contain_text("Receipt status is unavailable")
    expect(page.locator("#activity-rows .receipt-result")).to_have_count(0)
    expect(page.locator("#activity-rows .receipt-error")).to_contain_text("receipt provider unavailable")
    page.get_by_role("button", name="Disconnect").click()
    expect(page.locator("#activity-rows .receipt-error")).to_have_count(0)
