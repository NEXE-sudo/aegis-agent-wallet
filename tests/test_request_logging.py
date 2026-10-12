import logging

from fastapi.testclient import TestClient

from app.main import app


def test_request_logs_use_route_template_and_exclude_sensitive_request_data(caplog):
    bearer_secret = "bearer-secret-must-not-be-logged"
    query_secret = "query-secret-must-not-be-logged"

    with TestClient(app) as client, caplog.at_level(logging.INFO, logger="aegis.request"):
        response = client.get(
            f"/health?api_key={query_secret}",
            headers={"Authorization": f"Bearer {bearer_secret}"},
        )

    assert response.status_code == 200
    records = [record for record in caplog.records if record.name == "aegis.request"]
    assert len(records) == 1

    record = records[0]
    assert record.getMessage() == "http_request"
    assert record.http_method == "GET"
    assert record.http_route == "/health"
    assert record.http_status_code == 200
    assert record.duration_ms >= 0
    assert bearer_secret not in caplog.text
    assert query_secret not in caplog.text
    assert query_secret not in record.http_route


def test_request_logs_unhandled_failures_without_exception_details(caplog):
    import pytest

    route_path = "/__test_request_logging_failure"
    exception_detail = "sensitive internal failure detail"

    def fail_request():
        raise RuntimeError(exception_detail)

    app.add_api_route(route_path, fail_request, methods=["GET"])
    try:
        with (
            TestClient(app) as client,
            caplog.at_level(logging.INFO, logger="aegis.request"),
            pytest.raises(RuntimeError, match=exception_detail),
        ):
            client.get(route_path)
    finally:
        app.router.routes[:] = [
            route for route in app.router.routes
            if getattr(route, "path", None) != route_path
        ]

    records = [record for record in caplog.records if record.name == "aegis.request"]
    assert len(records) == 1
    record = records[0]
    assert record.getMessage() == "http_request"
    assert record.http_method == "GET"
    assert record.http_route == route_path
    assert record.http_status_code == 500
    assert record.duration_ms >= 0
    assert exception_detail not in caplog.text
