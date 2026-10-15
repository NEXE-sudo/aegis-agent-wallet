from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_frontend_homepage_is_served():
    response = client.get("/")

    assert response.status_code == 200
    assert "Aegis" in response.text
    assert 'id="connection-form"' in response.text


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

    for endpoint in (
        "/transactions/propose",
        "/approve",
        "/execute",
        "/audit",
        "/market-data/${assetClass}/${symbol}",
    ):
        assert endpoint in response.text

    assert 'confirmation: \'APPROVE\'' in response.text
    assert 'data-action="execute"' in response.text


def test_frontend_does_not_persist_credentials_or_show_fabricated_market_moves():
    response = client.get("/")

    assert response.status_code == 200
    assert "localStorage.setItem" not in response.text
    assert "sessionStorage.setItem" not in response.text
    assert "document.cookie" not in response.text
    assert "+0.8%" not in response.text
    assert "Daily closing data; not an intraday or executable price." in response.text
    assert "No on-chain transaction occurred." in response.text
