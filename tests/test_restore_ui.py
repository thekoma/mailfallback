from mailfallback.models import UserRole
from mailfallback.services.user_service import create_user


def test_restore_page_redirects_unauthenticated(client):
    resp = client.get("/restore", follow_redirects=False)
    assert resp.status_code == 307


def test_restore_page_renders(client, db_session, default_store):
    create_user(db_session, "uitest", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "uitest", "password": "pass"})
    resp = client.get("/restore")
    assert resp.status_code == 200
    assert "Restore" in resp.text
