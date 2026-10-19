import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _script_function(source: str, name: str) -> str:
    start = source.index(f"const {name} = ")
    end = source.index("\n};", start) + 3
    return source[start:end]


def _run_node(source: str, harness: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; JavaScript runtime regression tests require it.")

    result = subprocess.run(
        [node, "--input-type=commonjs", "-e", f"{source}\n{harness}"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_frontend_homepage_is_served():
    response = client.get("/")

    assert response.status_code == 200
    assert "Aegis" in response.text
    assert 'id="connection-form"' in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    nonce = response.headers["content-security-policy"].split("script-src 'nonce-", 1)[1].split("'", 1)[0]
    assert nonce
    assert f'<script nonce="{nonce}">' in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_frontend_contains_inline_styles_and_application_script():
    response = client.get("/")

    assert response.status_code == 200
    assert "prefers-reduced-motion" in response.text
    assert "api('/transactions/propose'" in response.text


def test_frontend_covers_supported_views_and_workflow_endpoints():
    response = client.get("/")

    assert response.status_code == 200
    for view in ("overview", "markets", "proposals", "approvals", "activity", "settings"):
        assert f'id="view-{view}"' in response.text

    for element in (
        'id="market-list"',
        'id="proposal-form"',
        'id="approval-list"',
        'id="activity-rows"',
        'id="audit-detail"',
        'id="reset-session"',
    ):
        assert element in response.text

    for endpoint in (
        "api('/transactions/propose'",
        "/transactions/${encodeURIComponent(transactionId)}/approve",
        "/transactions/${encodeURIComponent(transactionId)}/execute",
        "/transactions/${encodeURIComponent(transactionId)}/receipt",
        "/transactions/${encodeURIComponent(transactionId)}/cancel",
        "/transactions/${encodeURIComponent(transactionId)}/audit",
        "/market-data/${assetClass}/${symbol}",
    ):
        assert endpoint in response.text

    assert 'confirmation: \'APPROVE\'' in response.text
    assert "transaction_fingerprint: fingerprint" in response.text
    assert "}, 'agent');" in response.text
    assert "}, 'owner');" in response.text
    assert 'data-action="execute"' in response.text
    assert 'data-action="cancel"' in response.text
    assert "No on-chain transaction occurred." in response.text


def test_frontend_shows_owner_authenticated_daily_spending_summary():
    response = client.get("/")

    assert response.status_code == 200
    assert 'id="spending-summary"' in response.text
    assert 'id="spending-date"' in response.text
    assert "api('/spending/summary'" in response.text
    assert "spent_base_units" in response.text
    assert "daily_limit_base_units" in response.text
    assert "remaining_base_units" in response.text
    assert "Local workflow accounting includes active reservations" in response.text
    assert "state.spendingRequestId += 1;" in _script_function(response.text, "invalidatePendingRequests")
    assert "sessionVersion !== state.sessionVersion || requestId !== state.spendingRequestId" in _script_function(response.text, "refreshSpendingSummary")
    assert "state.spendingSummary = null;" in _script_function(response.text, "clearSessionData")


def test_frontend_supports_vault_only_testnet_execution():
    response = client.get("/")

    assert response.status_code == 200
    assert "['simulated', 'base_sepolia_vault']" in response.text
    assert "base_sepolia_vault" in response.text
    assert "Execute via vault" in response.text
    assert "['simulated', 'base_sepolia']" not in response.text


def test_frontend_does_not_persist_credentials_or_show_fabricated_market_moves():
    response = client.get("/")

    assert response.status_code == 200
    assert "localStorage.setItem" not in response.text
    assert "localStorage.getItem" not in response.text
    assert "sessionStorage.setItem" not in response.text
    assert "sessionStorage.getItem" not in response.text
    assert "document.cookie" not in response.text
    assert "console.log" not in response.text
    assert "console.error" not in response.text
    assert "location.search" not in response.text
    assert "location.hash" not in response.text
    assert "+0.8%" not in response.text
    assert "Daily closing data; not an intraday or executable price." in response.text
    assert "No on-chain transaction occurred." in response.text
    assert "state.tokens.agent = '';" in _script_function(response.text, "resetSession")
    assert "state.tokens.owner = '';" in _script_function(response.text, "resetSession")
    assert "'No active session'" in _script_function(response.text, "resetSession")


def test_frontend_guards_async_work_against_stale_sessions():
    response = client.get("/")

    assert response.status_code == 200
    refresh = _script_function(response.text, "refreshTransactions")
    assert "const sessionVersion = state.sessionVersion;" in refresh
    assert refresh.count("sessionVersion !== state.sessionVersion || refreshId !== state.refreshId") == 2

    connect = _script_function(response.text, "handleConnect")
    assert connect.count(
        "sessionVersion !== state.sessionVersion || connectionRequestId !== state.connectionRequestId"
    ) == 3
    assert "if (connectionRequestId === state.connectionRequestId)" in connect

    reset = _script_function(response.text, "resetSession")
    assert "state.sessionVersion += 1;" in reset
    assert "invalidatePendingRequests();" in reset


def test_frontend_async_actions_do_not_clear_newer_session_busy_state():
    response = client.get("/")

    assert response.status_code == 200
    for name in ("handleApprove", "handleExecute"):
        function = _script_function(response.text, name)
        assert "if (sessionVersion === state.sessionVersion) {" in function
        assert "state.busyActions.delete(transactionId);" in function

    proposal = _script_function(response.text, "handleProposalSubmit")
    assert "requestId !== state.proposalRequestId" in proposal
    assert "sessionVersion === state.sessionVersion && requestId === state.proposalRequestId" in proposal

    quote = _script_function(response.text, "loadMarketQuote")
    assert "quoteRequestId !== state.marketQuoteRequestIds.get(key)" in quote


def test_workflow_api_failure_does_not_raise_a_secondary_javascript_error():
    response = client.get("/")
    function = _script_function(response.text, "refreshTransactions")
    _run_node(
        function,
        """
const assert = require('node:assert/strict');
const state = {
  sessionVersion: 4, refreshId: 0, tokens: {owner: 'memory-only-owner'},
  transactions: [], transactionsLoaded: true, transactionError: '', connected: true
};
const nodes = {
  'connection-label': {}, 'connection-status': {}, 'settings-status': {},
  '.connect-box': {classList: {remove() {}}}
};
const document = {
  getElementById(id) { return nodes[id] || (nodes[id] = {}); },
  querySelector(selector) { return nodes[selector]; }
};
const api = async () => { throw new Error('workflow API offline'); };
const setApiState = () => {};
const renderAll = () => {};
const refreshAuditSummary = async () => {};
(async () => {
  await refreshTransactions();
  assert.equal(state.transactionError, 'workflow API offline');
  assert.equal(state.connected, false);
  assert.deepEqual(state.transactions, []);
  assert.equal(nodes['connection-status'].textContent, 'Could not load workflow records');
})().catch((error) => { console.error(error); process.exitCode = 1; });
""",
    )


def test_stale_connection_attempt_cannot_overwrite_newer_session():
    response = client.get("/")
    source = "\n".join(
        (
            _script_function(response.text, "handleConnect"),
            _script_function(response.text, "setExecutionMode"),
            _script_function(response.text, "invalidatePendingRequests"),
        )
    )
    _run_node(
        source,
        """
const assert = require('node:assert/strict');
const state = {
  sessionVersion: 0, refreshId: 0, healthRequestId: 0, marketRequestId: 0,
  marketSnapshotId: 0, marketQuoteRequestIds: new Map(), auditRequestId: 0,
  auditSummaryRequestId: 0, proposalRequestId: 0, connectionRequestId: 0,
  tokens: {agent: '', owner: ''}, connected: false, executionMode: null
};
const firstHealth = {};
firstHealth.promise = new Promise((resolve, reject) => {
  firstHealth.resolve = resolve;
  firstHealth.reject = reject;
});
let healthCalls = 0;
const api = async (_path, _options, role) => {
  if (role === 'public' && ++healthCalls === 1) return firstHealth.promise;
  if (role === 'public') return {status: 'ok', mode: 'simulated'};
  return [];
};
let formCalls = 0;
class FormData {
  constructor() { this.values = ++formCalls === 1 ? ['old-agent', 'old-owner'] : ['new-agent', 'new-owner']; }
  get(key) { return key === 'agentToken' ? this.values[0] : this.values[1]; }
}
const submit = {disabled: false, textContent: ''};
const form = {querySelector: () => submit, reset() {}};
const nodes = {
  'connection-feedback': {},
  'connection-label': {},
  'connection-status': {},
  'settings-status': {},
  'settings-health': {},
  '.connect-box': {classList: {remove() {}, add() {}}}
};
const document = {
  getElementById(id) { return nodes[id] || (nodes[id] = {}); },
  querySelector(selector) { return nodes[selector]; }
};
const clearSessionData = () => {};
const closeConnectionModal = () => {};
const notify = () => {};
const renderAll = () => {};
const refreshAuditSummary = async () => {};
const refreshMarketSnapshot = async () => {};
const setApiState = () => {};
const event = {preventDefault() {}, currentTarget: form};
(async () => {
  const staleAttempt = handleConnect(event);
  const currentAttempt = handleConnect(event);
  await currentAttempt;
  firstHealth.reject(new Error('old attempt failed late'));
  await staleAttempt;
  assert.equal(state.connected, true);
  assert.equal(state.tokens.agent, 'new-agent');
  assert.equal(state.tokens.owner, 'new-owner');
  assert.equal(nodes['connection-status'].textContent, 'Agent token stored in memory');
})().catch((error) => { console.error(error); process.exitCode = 1; });
""",
    )


def test_disconnect_while_connecting_prevents_pending_response_from_restoring_session():
    response = client.get("/")
    source = "\n".join(
        (
            _script_function(response.text, "handleConnect"),
            _script_function(response.text, "resetSession"),
            _script_function(response.text, "invalidatePendingRequests"),
        )
    )
    _run_node(
        source,
        """
const assert = require('node:assert/strict');
const state = {
  sessionVersion: 0, refreshId: 0, healthRequestId: 0, marketRequestId: 0,
  marketSnapshotId: 0, marketQuoteRequestIds: new Map(), auditRequestId: 0,
  auditSummaryRequestId: 0, proposalRequestId: 0, connectionRequestId: 0,
  tokens: {agent: '', owner: ''}, connected: false
};
let resolveHealth;
const health = new Promise((resolve) => { resolveHealth = resolve; });
const api = async (_path, _options, role) => role === 'public' ? health : [];
class FormData { get(key) { return key === 'agentToken' ? 'agent-secret' : 'owner-secret'; } }
const submit = {disabled: false, textContent: ''};
const form = {querySelector: () => submit, reset() {}};
const box = {classList: {remove() {}, add() {}}};
const nodes = {
  'connection-feedback': {}, 'connection-label': {}, 'connection-status': {},
  'settings-status': {}, 'settings-health': {}, 'connection-form': form,
  '#connection-form [type="submit"]': submit, '.connect-box': box
};
const document = {
  getElementById(id) { return nodes[id] || (nodes[id] = {}); },
  querySelector(selector) { return nodes[selector]; }
};
const clearSessionData = () => {};
const closeConnectionModal = () => {};
const notify = () => {};
const renderAll = () => {};
const refreshAuditSummary = async () => {};
const refreshMarketSnapshot = async () => {};
const setApiState = () => {};
const event = {preventDefault() {}, currentTarget: form};
(async () => {
  const pending = handleConnect(event);
  resetSession();
  resolveHealth({status: 'ok'});
  await pending;
  assert.equal(state.connected, false);
  assert.equal(state.tokens.agent, '');
  assert.equal(state.tokens.owner, '');
  assert.equal(nodes['connection-status'].textContent, 'No active session');
})().catch((error) => { console.error(error); process.exitCode = 1; });
""",
    )


def test_market_failure_is_unavailable_and_action_failures_are_not_success():
    response = client.get("/")
    source = "\n".join(
        _script_function(response.text, name)
        for name in ("loadMarketQuote", "handleApprove", "handleExecute")
    )
    _run_node(
        source,
        """
const assert = require('node:assert/strict');
const state = {
  sessionVersion: 1, executionMode: 'simulated', transactions: [],
  marketRequestId: 0, marketQuoteRequestIds: new Map(),
  marketSelected: {assetClass: 'crypto', symbol: 'BTC'},
  marketQuotes: {}, tokens: {owner: 'owner-token'}, busyActions: new Set()
};
let unavailable = '';
let quoteShown = false;
const showMarketQuote = () => { quoteShown = true; };
const showMarketLoading = () => {};
const showMarketUnavailable = (message) => { unavailable = message; };
const renderMarketSnapshot = () => {};
const api = async () => { throw new Error('backend rejected the operation'); };
const notices = [];
const notify = (message, kind) => notices.push({message, kind});
const renderApprovals = () => {};
const renderActivity = () => {};
const refreshTransactions = async () => {};
const window = {confirm: () => true};
(async () => {
  await loadMarketQuote('crypto', 'BTC');
  assert.equal(state.marketQuotes['crypto:BTC'].error, 'backend rejected the operation');
  assert.equal(unavailable, 'backend rejected the operation');
  assert.equal(state.marketQuotes['crypto:BTC'].data, undefined);
  assert.equal(quoteShown, false);

  await handleApprove('tx-1', 'a'.repeat(64));
  await handleExecute('tx-2');
  assert.equal(notices.length, 2);
  assert.match(notices[0].message, /Approval was not confirmed/);
  assert.match(notices[1].message, /Execution request was not confirmed/);
  assert.ok(notices.every((notice) => notice.kind === 'error'));
  assert.ok(notices.every((notice) => !/confirmed approval|confirmed simulated execution/.test(notice.message)));
  assert.equal(state.busyActions.size, 0);
})().catch((error) => { console.error(error); process.exitCode = 1; });
""",
    )
