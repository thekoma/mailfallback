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


def test_every_logged_action_has_a_human_label():
    """The audit log shows labels, not codes: every action string the app
    logs must resolve through ACTION_LABELS (unknown ones fall back raw)."""
    import re
    from pathlib import Path

    from mailfallback.services.audit_service import ACTION_LABELS, get_action_label

    src = Path(__file__).resolve().parents[1] / "src" / "mailfallback"
    # Literal action="x.y" arguments, plus any quoted "x.y" string with a
    # known action prefix in a file that calls log_action — actions picked
    # from a dict (restore_worker's status_actions) pass action=action.
    prefixes = "|".join(sorted({k.split(".")[0] for k in ACTION_LABELS}))
    dotted = re.compile(rf'"((?:{prefixes})\.(?!(?:html|json|js|css|py)")[a-z_]+)"')
    used = set()
    for path in src.rglob("*.py"):
        text = path.read_text()
        used |= set(re.findall(r'action="([a-z_]+\.[a-z_]+)"', text))
        if "log_action(" in text and path.name != "audit_service.py":
            used |= set(dotted.findall(text))
    assert used, "no logged actions found"
    missing = sorted(used - set(ACTION_LABELS))
    assert not missing, f"actions logged without a label: {missing}"
    assert get_action_label("user.login") == "Signed in"
    assert get_action_label("no.such_action") == "no.such_action"


def test_audit_table_shows_label_with_raw_code_as_title(client, db_session, default_store):
    user = _login_admin(client, db_session, default_store)
    log_action(
        db_session, user=user, action="user.login", resource_type="user", resource_name="admin"
    )
    resp = client.get("/admin/audit/table", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert 'title="user.login">Signed in<' in resp.text
