# tests/test_ui.py
from mailfallback.models import UserRole
from mailfallback.services.user_service import create_user


def test_login_page(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "Login" in resp.text


def test_dashboard_redirects_when_not_logged_in(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)


def test_dashboard_shows_when_logged_in(client, db_session):
    create_user(db_session, "admin", "pass", UserRole.admin)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Email Accounts" in resp.text
