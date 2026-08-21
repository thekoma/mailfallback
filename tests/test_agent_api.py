"""The /api/v1/agent surface: scope gating, scoping to the caller's mailboxes,
and response shapes that do not leak internal dict drift."""

import pytest

from mailfallback.models import Account, MailIndexMessage, UserRole, account_owners
from mailfallback.services import app_credential_service as svc
from mailfallback.services import index_service
from mailfallback.services.user_service import create_user

BASE = "/api/v1/agent"


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def agent_user(db_session, default_store):
    return create_user(
        db_session, "agentuser", "agentpass12345", UserRole.user, store_id=default_store.id
    )


@pytest.fixture
def read_token(db_session, agent_user):
    _, token = svc.create_credential(
        db_session, agent_user, name="reader", scopes=[svc.SCOPE_MAIL_READ]
    )
    return token


@pytest.fixture
def imap_only_token(db_session, agent_user):
    _, token = svc.create_credential(
        db_session, agent_user, name="imap only", scopes=[svc.SCOPE_IMAP]
    )
    return token


def _indexed_account(db_session, store, tmp_path, owner, name="acc", msgid="<m1@x>"):
    """An account with one indexed message on disk."""
    import os
    from email.message import EmailMessage

    acc = Account(
        name=name,
        imap_host="h",
        email_address=f"{name}@example.com",
        maildir_path=str(tmp_path / f"mail-{name}"),
        store_id=store.id,
    )
    db_session.add(acc)
    db_session.flush()
    db_session.execute(account_owners.insert().values(account_id=acc.id, user_id=owner.id))
    db_session.commit()

    msg = EmailMessage()
    msg["Message-Id"] = msgid
    msg["From"] = "Mittente <sender@example.com>"
    msg["To"] = "dest@example.com"
    msg["Subject"] = "quarterly invoice"
    msg["Date"] = "Thu, 11 Jun 2026 10:00:00 +0200"
    msg.set_content("body text")
    cur = os.path.join(acc.maildir_path, "cur")
    os.makedirs(cur, exist_ok=True)
    with open(os.path.join(cur, "100.m1.host:2,S"), "wb") as f:
        f.write(msg.as_bytes())
    index_service.upsert_message_set(db_session, acc.id)
    row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
    return acc, row


class TestAuthAndScope:
    def test_every_endpoint_refuses_an_unauthenticated_call(self, client, db_session):
        assert client.get(f"{BASE}/mailboxes").status_code == 401
        assert client.post(f"{BASE}/search", json={"query": "x"}).status_code == 401

    def test_an_imap_only_token_is_refused_with_403(self, client, db_session, imap_only_token):
        """The IMAP skills' token must not reach the API."""
        resp = client.get(f"{BASE}/mailboxes", headers=_bearer(imap_only_token))
        assert resp.status_code == 403
        assert "mail:read" in resp.json()["detail"]

    def test_a_mail_read_token_is_accepted(self, client, db_session, read_token):
        assert client.get(f"{BASE}/mailboxes", headers=_bearer(read_token)).status_code == 200

    def test_a_revoked_token_is_refused(self, client, db_session, agent_user):
        cred, token = svc.create_credential(
            db_session, agent_user, name="doomed", scopes=[svc.SCOPE_MAIL_READ]
        )
        svc.revoke_credential(db_session, agent_user, cred.id)

        assert client.get(f"{BASE}/mailboxes", headers=_bearer(token)).status_code == 401

    def test_an_admins_token_still_sees_only_their_own_mailboxes(
        self, client, db_session, default_store, tmp_path
    ):
        """The spec's sharpest requirement: role does not travel with a token.

        An admin can reach every mailbox through the UI. A token they issue must
        not inherit that, because the agent holding it is not them.
        """
        admin = create_user(
            db_session, "bigboss", "bigbosspass12345", UserRole.admin, store_id=default_store.id
        )
        _, admin_token = svc.create_credential(
            db_session, admin, name="admins agent", scopes=[svc.SCOPE_MAIL_READ]
        )
        victim = create_user(
            db_session, "someoneelse", "elsepass12345", UserRole.user, store_id=default_store.id
        )
        _indexed_account(db_session, default_store, tmp_path, victim, name="notmine")

        mailboxes = client.get(f"{BASE}/mailboxes", headers=_bearer(admin_token))
        search = client.post(f"{BASE}/search", json={"query": ""}, headers=_bearer(admin_token))

        assert mailboxes.status_code == 200
        assert mailboxes.json() == []
        assert search.status_code == 200
        assert search.json()["total"] == 0

    def test_include_all_cannot_be_smuggled_in(
        self, client, db_session, default_store, tmp_path, agent_user, read_token
    ):
        """The spec forbids admin escalation through token auth. The field does
        not exist, so sending it must change nothing — not error, not widen."""
        other = create_user(
            db_session, "victim", "victimpass12345", UserRole.admin, store_id=default_store.id
        )
        _indexed_account(db_session, default_store, tmp_path, other, name="theirs")

        resp = client.post(
            f"{BASE}/search",
            json={"query": "", "include_all": True},
            headers=_bearer(read_token),
        )

        assert resp.status_code in (200, 422)
        if resp.status_code == 200:
            assert resp.json()["total"] == 0


class TestMailboxes:
    def test_lists_only_the_callers_mailboxes(
        self, client, db_session, default_store, tmp_path, agent_user, read_token
    ):
        _indexed_account(db_session, default_store, tmp_path, agent_user, name="mine")
        other = create_user(
            db_session, "other", "otherpass12345", UserRole.user, store_id=default_store.id
        )
        _indexed_account(db_session, default_store, tmp_path, other, name="theirs")

        resp = client.get(f"{BASE}/mailboxes", headers=_bearer(read_token))

        assert resp.status_code == 200
        body = resp.json()
        assert [m["name"] for m in body] == ["mine"]
        assert body[0]["indexed_messages"] == 1
        assert body[0]["folders"] == ["INBOX"]
        assert "account_id" in body[0]


class TestSearch:
    def test_finds_an_indexed_message_and_returns_the_declared_shape(
        self, client, db_session, default_store, tmp_path, agent_user, read_token
    ):
        _indexed_account(db_session, default_store, tmp_path, agent_user)

        resp = client.post(f"{BASE}/search", json={"query": "invoice"}, headers=_bearer(read_token))

        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"results", "total", "page", "page_size", "partial"}
        assert body["total"] == 1
        hit = body["results"][0]
        assert hit["subject"] == "quarterly invoice"
        assert hit["folder_path"] == "INBOX"
        assert hit["alive_in_live"] is True
        # message_id_hash is the address the message and attachment endpoints take
        assert len(hit["message_id_hash"]) == 40

    def test_does_not_reach_another_users_mail(
        self, client, db_session, default_store, tmp_path, agent_user, read_token
    ):
        other = create_user(
            db_session, "other2", "otherpass12345", UserRole.user, store_id=default_store.id
        )
        _indexed_account(db_session, default_store, tmp_path, other, name="theirs")

        resp = client.post(f"{BASE}/search", json={"query": ""}, headers=_bearer(read_token))

        assert resp.json()["total"] == 0

    def test_page_size_is_capped(self, client, db_session, read_token):
        resp = client.post(
            f"{BASE}/search", json={"query": "x", "page_size": 5000}, headers=_bearer(read_token)
        )
        assert resp.status_code == 422


class TestMessage:
    def test_returns_the_message_and_its_attachment_indexes(
        self, client, db_session, default_store, tmp_path, agent_user, read_token
    ):
        acc, row = _indexed_account(db_session, default_store, tmp_path, agent_user)

        resp = client.get(
            f"{BASE}/messages/{acc.id}/{row.message_id_hash.hex()}", headers=_bearer(read_token)
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["subject"] == "quarterly invoice"
        assert body["source"] == "live"
        assert "body_snippet" in body
        assert isinstance(body["attachments"], list)

    def test_another_users_message_is_404_not_403(
        self, client, db_session, default_store, tmp_path, agent_user, read_token
    ):
        """404, not 403: the caller must not learn that the account exists."""
        other = create_user(
            db_session, "other3", "otherpass12345", UserRole.user, store_id=default_store.id
        )
        acc, row = _indexed_account(db_session, default_store, tmp_path, other, name="theirs")

        resp = client.get(
            f"{BASE}/messages/{acc.id}/{row.message_id_hash.hex()}", headers=_bearer(read_token)
        )

        assert resp.status_code == 404

    def test_a_malformed_hash_is_400(
        self, client, db_session, default_store, tmp_path, agent_user, read_token
    ):
        acc, _ = _indexed_account(db_session, default_store, tmp_path, agent_user)

        resp = client.get(f"{BASE}/messages/{acc.id}/nothex", headers=_bearer(read_token))

        assert resp.status_code == 400


class TestAttachmentSearch:
    def test_reports_whether_content_search_is_available(self, client, db_session, read_token):
        resp = client.post(
            f"{BASE}/search-attachments", json={"query": "pdf"}, headers=_bearer(read_token)
        )

        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"results", "total", "page", "page_size", "content_search_available"}
        assert isinstance(body["content_search_available"], bool)
