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
