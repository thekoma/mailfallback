from mailfallback.models import UserRole
from mailfallback.services.audit_service import log_action
from mailfallback.services.user_service import create_user


def _login_admin(client, db_session, default_store):
    user = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})
    return user


def test_audit_page_requires_admin(client, db_session, default_store):
    create_user(db_session, "regular", "pass", UserRole.user, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "regular", "password": "pass"})
    resp = client.get("/admin/audit", follow_redirects=False)
    assert resp.status_code in (302, 307)


def test_audit_page_loads_for_admin(client, db_session, default_store):
    _login_admin(client, db_session, default_store)
    resp = client.get("/admin/audit")
    assert resp.status_code == 200
    assert "Audit Log" in resp.text


def test_audit_page_shows_entries(client, db_session, default_store):
    user = _login_admin(client, db_session, default_store)
    log_action(
        db_session, user=user, action="user.create", resource_type="user", resource_name="newuser"
    )
    resp = client.get("/admin/audit")
    assert resp.status_code == 200
    assert "Created user" in resp.text
    assert "newuser" in resp.text


def test_audit_page_filters_by_action(client, db_session, default_store):
    user = _login_admin(client, db_session, default_store)
    log_action(
        db_session, user=user, action="user.create", resource_type="user", resource_name="u1"
    )
    log_action(
        db_session, user=user, action="store.create", resource_type="store", resource_name="s1"
    )
    resp = client.get("/admin/audit?action=user.create")
    assert resp.status_code == 200
    assert "u1" in resp.text
    assert "s1" not in resp.text


def test_audit_table_partial(client, db_session, default_store):
    user = _login_admin(client, db_session, default_store)
    log_action(
        db_session, user=user, action="user.create", resource_type="user", resource_name="u1"
    )
    resp = client.get("/admin/audit/table?action=user.create", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "u1" in resp.text
