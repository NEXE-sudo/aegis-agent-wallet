from fastapi.testclient import TestClient

from app import main
from app.governance import GovernanceStore
from app.workflow.controller import TransactionController
from app.workflow.store import WorkflowStore


def isolate_stores(tmp_path, monkeypatch):
    db_path = tmp_path / "operations.sqlite3"
    workflow = WorkflowStore(db_path)
    governance = GovernanceStore(db_path)
    monkeypatch.setattr(main, "store", workflow)
    monkeypatch.setattr(main, "controller", TransactionController(main.DEMO_POLICY, workflow))
    monkeypatch.setattr(main.app.state, "controller", main.controller, raising=False)
    monkeypatch.setattr(main.app.state, "governance_store", governance, raising=False)


def test_health_is_liveness_and_ready_checks_persistence(tmp_path, monkeypatch):
    isolate_stores(tmp_path, monkeypatch)
    client = TestClient(main.app)

    health = client.get("/health")
    ready = client.get("/ready")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "checks": {"workflow_store": "ok", "governance_store": "ok"},
    }


def test_request_id_is_returned_and_untrusted_values_are_replaced():
    client = TestClient(main.app)

    accepted = client.get("/health", headers={"X-Request-ID": "demo-request_01"})
    rejected = client.get("/health", headers={"X-Request-ID": "bad id with spaces"})

    assert accepted.headers["X-Request-ID"] == "demo-request_01"
    assert rejected.headers["X-Request-ID"] != "bad id with spaces"
    assert 1 <= len(rejected.headers["X-Request-ID"]) <= 64


def test_metrics_exposes_bounded_route_counters():
    client = TestClient(main.app)
    client.get("/health")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "# TYPE aegis_http_requests_total counter" in response.text
    assert 'route="/health"' in response.text


def test_mutating_requests_are_rate_limited_and_retry_after_is_set(monkeypatch):
    client = TestClient(main.app)
    with main._rate_lock:
        main._rate_windows.clear()
    monkeypatch.setattr(main, "_RATE_LIMIT_MAX_REQUESTS", 1)

    try:
        first = client.post("/transactions/propose", json={})
        second = client.post("/transactions/propose", json={})

        assert first.status_code == 401
        assert second.status_code == 429
        assert second.headers["Retry-After"].isdigit()
        assert second.headers.get("X-Request-ID")
        assert second.json()["detail"]["code"] == "rate_limit_exceeded"
    finally:
        with main._rate_lock:
            main._rate_windows.clear()
