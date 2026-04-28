# tests/test_auth.py
from mailfallback.models import UserRole
from mailfallback.services.user_service import create_user


def test_login_success(client, db_session):
    create_user(db_session, "admin", "secret123", UserRole.admin)
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "secret123"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["role"] == "admin"


def test_login_wrong_password(client, db_session):
    create_user(db_session, "admin", "secret123", UserRole.admin)
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401


def test_login_unknown_user(client, db_session):
    resp = client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    assert resp.status_code == 401


def test_logout(client, db_session):
    create_user(db_session, "user1", "pass", UserRole.user)
    client.post("/api/auth/login", json={"username": "user1", "password": "pass"})
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
