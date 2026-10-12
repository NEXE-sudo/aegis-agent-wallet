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
    assert 'api("/transactions/propose"' in response.text
