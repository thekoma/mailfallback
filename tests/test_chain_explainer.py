# tests/test_chain_explainer.py
"""First-time explainer callout on the chain hero, dismissed via
POST /profile/dismiss-chain-explainer."""

from mailfallback.models import Account, UserRole
from mailfallback.services.user_service import create_user


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


def _make_account(db_session, default_store):
    acc = Account(
        name="Gmail",
        email_address="me@gmail.com",
        imap_host="imap.gmail.com",
        imap_port=993,
        maildir_path="/data/mailboxes/gmail",
        store_id=default_store.id,
    )
    db_session.add(acc)
    db_session.commit()
    return acc


def test_explainer_shown_for_fresh_user(client, db_session, default_store):
    create_user(db_session, "admin", "non-default-pass", UserRole.admin, store_id=default_store.id)
    _make_account(db_session, default_store)
    _login(client, "admin", "non-default-pass")
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    # The explainer copy must be present.
    assert "How MailFallBack keeps your mail safe" in body
    assert "Got it" in body


def test_explainer_hidden_after_dismiss(client, db_session, default_store):
    create_user(db_session, "admin", "non-default-pass", UserRole.admin, store_id=default_store.id)
    _make_account(db_session, default_store)
    _login(client, "admin", "non-default-pass")

    # Dismiss it.
    resp = client.post("/profile/dismiss-chain-explainer", follow_redirects=False)
    assert resp.status_code == 303

    # Next dashboard render: explainer is gone.
    resp = client.get("/")
    body = resp.text
    assert "How MailFallBack keeps your mail safe" not in body


def test_dismiss_requires_login(client, db_session):
    resp = client.post("/profile/dismiss-chain-explainer", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
