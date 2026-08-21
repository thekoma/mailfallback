"""The /api/v1/agent surface: scope gating, scoping to the caller's mailboxes,
and response shapes that do not leak internal dict drift."""

from unittest.mock import patch

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

    def test_body_matched_is_null_when_deep_not_requested(
        self, client, db_session, default_store, tmp_path, agent_user, read_token
    ):
        _indexed_account(db_session, default_store, tmp_path, agent_user)

        resp = client.post(f"{BASE}/search", json={"query": "invoice"}, headers=_bearer(read_token))

        assert resp.status_code == 200
        hit = resp.json()["results"][0]
        assert "body_matched" in hit
        assert hit["body_matched"] is None

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


@pytest.fixture
def sync_token(db_session, agent_user):
    _, token = svc.create_credential(
        db_session, agent_user, name="syncer", scopes=[svc.SCOPE_SYNC_TRIGGER]
    )
    return token


class TestAttachmentDownload:
    def test_a_read_token_can_fetch_attachment_bytes(
        self, client, db_session, default_store, tmp_path, agent_user, read_token
    ):
        """Built with a real attachment so the part_index contract is exercised."""
        import os
        from email.message import EmailMessage

        acc = Account(
            name="withatt",
            imap_host="h",
            email_address="withatt@example.com",
            maildir_path=str(tmp_path / "mail-att"),
            store_id=default_store.id,
        )
        db_session.add(acc)
        db_session.flush()
        db_session.execute(account_owners.insert().values(account_id=acc.id, user_id=agent_user.id))
        db_session.commit()

        msg = EmailMessage()
        msg["Message-Id"] = "<att@x>"
        msg["From"] = "a@b.c"
        msg["To"] = "d@e.f"
        msg["Subject"] = "with attachment"
        msg["Date"] = "Thu, 11 Jun 2026 10:00:00 +0200"
        msg.set_content("see attached")
        msg.add_attachment(
            b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="invoice.pdf"
        )
        cur = os.path.join(acc.maildir_path, "cur")
        os.makedirs(cur, exist_ok=True)
        with open(os.path.join(cur, "200.att.host:2,S"), "wb") as f:
            f.write(msg.as_bytes())
        index_service.upsert_message_set(db_session, acc.id)
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()

        # the part_index comes from the search result — the Task 3 contract
        hits = client.post(
            f"{BASE}/search", json={"query": "attachment"}, headers=_bearer(read_token)
        ).json()
        att = hits["results"][0]["attachments"][0]

        resp = client.get(
            f"{BASE}/messages/{acc.id}/{row.message_id_hash.hex()}/attachments/{att['part_index']}",
            headers=_bearer(read_token),
        )

        assert resp.status_code == 200
        assert resp.content == b"%PDF-1.4 fake"
        assert resp.headers["content-type"] == "application/octet-stream"
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert "invoice.pdf" in resp.headers["content-disposition"]

    def test_another_users_attachment_is_404(
        self, client, db_session, default_store, tmp_path, agent_user, read_token
    ):
        other = create_user(
            db_session, "other4", "otherpass12345", UserRole.user, store_id=default_store.id
        )
        acc, row = _indexed_account(db_session, default_store, tmp_path, other, name="theirs2")

        resp = client.get(
            f"{BASE}/messages/{acc.id}/{row.message_id_hash.hex()}/attachments/1",
            headers=_bearer(read_token),
        )

        assert resp.status_code == 404


class TestImapCoords:
    def test_resolves_message_ids_to_folders_and_uids(
        self, client, db_session, default_store, tmp_path, agent_user, read_token
    ):
        """The bridge to the IMAP path: an agent searches here, fetches there.

        There is no Dovecot to connect to in this test environment, so this
        is the unreachable case by construction: the ids must fold into
        `missing` AND `imap_unavailable` must be True, so a caller can tell
        "could not check" apart from "checked and gone" and retry instead of
        concluding the messages don't exist.
        """
        acc, row = _indexed_account(db_session, default_store, tmp_path, agent_user)

        resp = client.post(
            f"{BASE}/imap-coords",
            json={"account_id": acc.id, "message_ids": [row.message_id]},
            headers=_bearer(read_token),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"resolved", "missing", "imap_unavailable"}
        assert body["resolved"] == {}
        assert body["missing"] == [row.message_id]
        assert body["imap_unavailable"] is True

    def test_ids_beyond_the_cap_are_ignored_entirely(
        self, client, db_session, default_store, tmp_path, agent_user, read_token
    ):
        """Mirrors the UI-side resolve-uids cap test (RESOLVE_UIDS_MAX_IDS):
        ids past RESOLVE_COORDS_MAX_IDS must land in NEITHER `resolved` nor
        `missing` — a naive truncation-less implementation instead reports
        them as missing, which is the bug this test exists to catch."""
        from mailfallback.routers.agent import RESOLVE_COORDS_MAX_IDS

        acc, _ = _indexed_account(db_session, default_store, tmp_path, agent_user)
        ids = [f"<bogus-{i}@x>" for i in range(RESOLVE_COORDS_MAX_IDS + 100)]

        resp = client.post(
            f"{BASE}/imap-coords",
            json={"account_id": acc.id, "message_ids": ids},
            headers=_bearer(read_token),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["resolved"] == {}
        assert len(body["missing"]) == RESOLVE_COORDS_MAX_IDS
        for beyond_cap in ids[RESOLVE_COORDS_MAX_IDS:]:
            assert beyond_cap not in body["missing"]

    def test_another_users_account_is_404(
        self, client, db_session, default_store, tmp_path, agent_user, read_token
    ):
        other = create_user(
            db_session, "other5", "otherpass12345", UserRole.user, store_id=default_store.id
        )
        acc, row = _indexed_account(db_session, default_store, tmp_path, other, name="theirs3")

        resp = client.post(
            f"{BASE}/imap-coords",
            json={"account_id": acc.id, "message_ids": [row.message_id]},
            headers=_bearer(read_token),
        )

        assert resp.status_code == 404


SUBMIT_PATCH = "mailfallback.routers.agent.submit_sync_job"


class TestSync:
    def test_a_read_token_cannot_trigger_a_sync(
        self, client, db_session, default_store, tmp_path, agent_user, read_token
    ):
        acc, _ = _indexed_account(db_session, default_store, tmp_path, agent_user)

        resp = client.post(f"{BASE}/sync/{acc.id}", headers=_bearer(read_token))

        assert resp.status_code == 403
        assert "sync:trigger" in resp.json()["detail"]

    def test_a_sync_token_queues_a_job(
        self, client, db_session, default_store, tmp_path, agent_user, sync_token
    ):
        acc, _ = _indexed_account(db_session, default_store, tmp_path, agent_user)

        resp = client.post(f"{BASE}/sync/{acc.id}", headers=_bearer(sync_token))

        assert resp.status_code == 200
        body = resp.json()
        assert body["account_id"] == acc.id
        assert body["status"] == "pending"
        assert body["source"] == "agent"
        assert body["already_queued"] is False

    def test_a_new_job_is_actually_submitted_to_the_executor(
        self, client, db_session, default_store, tmp_path, agent_user, sync_token
    ):
        """The bug this closes: a row in the DB is not a sync. Without a call
        to submit_sync_job, the job sits in `pending` forever — nothing
        re-drives pending rows, and the orphaned row then makes
        create_sync_job's existing-job guard return None for this account on
        every later call (see sync_worker.recover_zombie_sync_jobs docstring).
        A test that only checks the DB row would have let that through."""
        acc, _ = _indexed_account(db_session, default_store, tmp_path, agent_user)

        with patch(SUBMIT_PATCH) as mock_submit:
            resp = client.post(f"{BASE}/sync/{acc.id}", headers=_bearer(sync_token))

        assert resp.status_code == 200
        job_id = resp.json()["job_id"]
        mock_submit.assert_called_once_with(job_id)

    def test_a_second_trigger_reports_already_queued_rather_than_failing(
        self, client, db_session, default_store, tmp_path, agent_user, sync_token
    ):
        """An agent polling a queue should not have to handle an error for the
        ordinary "one is already running" case."""
        acc, _ = _indexed_account(db_session, default_store, tmp_path, agent_user)

        first = client.post(f"{BASE}/sync/{acc.id}", headers=_bearer(sync_token)).json()
        resp = client.post(f"{BASE}/sync/{acc.id}", headers=_bearer(sync_token))

        assert resp.status_code == 200
        body = resp.json()
        assert body["already_queued"] is True
        assert body["job_id"] == first["job_id"]

    def test_the_already_queued_branch_never_resubmits(
        self, client, db_session, default_store, tmp_path, agent_user, sync_token
    ):
        """A run is already in flight for that job; resubmitting it would run
        the same mbsync job twice concurrently."""
        acc, _ = _indexed_account(db_session, default_store, tmp_path, agent_user)
        client.post(f"{BASE}/sync/{acc.id}", headers=_bearer(sync_token))

        with patch(SUBMIT_PATCH) as mock_submit:
            resp = client.post(f"{BASE}/sync/{acc.id}", headers=_bearer(sync_token))

        assert resp.status_code == 200
        assert resp.json()["already_queued"] is True
        mock_submit.assert_not_called()

    def test_a_trigger_is_audited(
        self, client, db_session, default_store, tmp_path, agent_user, sync_token
    ):
        from mailfallback.models import AuditLog

        acc, _ = _indexed_account(db_session, default_store, tmp_path, agent_user)

        with patch(SUBMIT_PATCH):
            resp = client.post(f"{BASE}/sync/{acc.id}", headers=_bearer(sync_token))

        assert resp.status_code == 200
        entry = db_session.query(AuditLog).filter(AuditLog.action == "account.sync").one()
        assert entry.resource_id == acc.id
        assert entry.details["via"] == "agent_api"

    def test_a_suspended_account_refuses_with_409_and_no_job(
        self, client, db_session, default_store, tmp_path, agent_user, sync_token
    ):
        from mailfallback.models import SyncJob

        acc, _ = _indexed_account(db_session, default_store, tmp_path, agent_user)
        acc.suspended = True
        db_session.commit()

        resp = client.post(f"{BASE}/sync/{acc.id}", headers=_bearer(sync_token))

        assert resp.status_code == 409
        assert "suspended" in resp.json()["detail"]
        assert db_session.query(SyncJob).filter(SyncJob.account_id == acc.id).count() == 0

    def test_a_migrating_account_refuses_with_409_and_no_job(
        self, client, db_session, default_store, tmp_path, agent_user, sync_token
    ):
        from mailfallback.models import SyncJob

        acc, _ = _indexed_account(db_session, default_store, tmp_path, agent_user)
        acc.migrating = True
        db_session.commit()

        resp = client.post(f"{BASE}/sync/{acc.id}", headers=_bearer(sync_token))

        assert resp.status_code == 409
        assert "migration" in resp.json()["detail"]
        assert db_session.query(SyncJob).filter(SyncJob.account_id == acc.id).count() == 0

    def test_a_migrating_owner_refuses_with_409_and_no_job(
        self, client, db_session, default_store, tmp_path, agent_user, sync_token
    ):
        """A CO-owner migrating, not the token's own user — the token owner's
        own migrating flag is already caught earlier, at credential
        verification (see app_credential_service.verify_credential), which
        would 401 rather than exercise this 409 guard. A shared account with
        another owner mid-migration is the case this guard actually protects."""
        from mailfallback.models import SyncJob

        acc, _ = _indexed_account(db_session, default_store, tmp_path, agent_user)
        co_owner = create_user(
            db_session, "co-owner", "co-ownerpass12345", UserRole.user, store_id=default_store.id
        )
        co_owner.migrating = True
        db_session.execute(account_owners.insert().values(account_id=acc.id, user_id=co_owner.id))
        db_session.commit()

        resp = client.post(f"{BASE}/sync/{acc.id}", headers=_bearer(sync_token))

        assert resp.status_code == 409
        assert "user migration" in resp.json()["detail"]
        assert db_session.query(SyncJob).filter(SyncJob.account_id == acc.id).count() == 0

    def test_a_paused_account_refuses_with_409_naming_the_reason_and_no_job(
        self, client, db_session, default_store, tmp_path, agent_user, sync_token
    ):
        """Deliberate divergence from the UI: the UI overrides a
        self-recovering pause with a warning, because a human can weigh
        burning the provider's daily quota. An agent cannot weigh that, so
        the pause here is a plain refusal that names the reason."""
        from datetime import UTC, datetime, timedelta

        from mailfallback.models import SyncJob

        acc, _ = _indexed_account(db_session, default_store, tmp_path, agent_user)
        acc.sync_paused_until = datetime.now(UTC) + timedelta(hours=1)
        acc.pause_reason = "budget"
        db_session.commit()

        resp = client.post(f"{BASE}/sync/{acc.id}", headers=_bearer(sync_token))

        assert resp.status_code == 409
        assert "budget" in resp.json()["detail"]
        assert db_session.query(SyncJob).filter(SyncJob.account_id == acc.id).count() == 0

    def test_job_status_is_readable(
        self, client, db_session, default_store, tmp_path, agent_user, sync_token
    ):
        acc, _ = _indexed_account(db_session, default_store, tmp_path, agent_user)
        job_id = client.post(f"{BASE}/sync/{acc.id}", headers=_bearer(sync_token)).json()["job_id"]

        resp = client.get(f"{BASE}/sync/jobs/{job_id}", headers=_bearer(sync_token))

        assert resp.status_code == 200
        assert resp.json()["job_id"] == job_id

    def test_another_users_job_is_404(
        self, client, db_session, default_store, tmp_path, agent_user, sync_token
    ):
        from mailfallback.services import sync_service

        other = create_user(
            db_session, "other6", "otherpass12345", UserRole.user, store_id=default_store.id
        )
        acc, _ = _indexed_account(db_session, default_store, tmp_path, other, name="theirs4")
        job = sync_service.create_sync_job(db_session, acc.id, source="api")

        resp = client.get(f"{BASE}/sync/jobs/{job.id}", headers=_bearer(sync_token))

        assert resp.status_code == 404

    def test_syncing_another_users_account_is_404(
        self, client, db_session, default_store, tmp_path, agent_user, sync_token
    ):
        other = create_user(
            db_session, "other7", "otherpass12345", UserRole.user, store_id=default_store.id
        )
        acc, _ = _indexed_account(db_session, default_store, tmp_path, other, name="theirs5")

        resp = client.post(f"{BASE}/sync/{acc.id}", headers=_bearer(sync_token))

        assert resp.status_code == 404

    def test_job_not_found_and_job_not_yours_return_byte_identical_bodies(
        self, client, db_session, default_store, tmp_path, agent_user, sync_token
    ):
        """A garbage job id and a real job the caller cannot see must be
        indistinguishable from outside — both are 404, and the detail text
        must not become a second oracle telling the two cases apart."""
        from mailfallback.services import sync_service

        other = create_user(
            db_session, "other8", "otherpass12345", UserRole.user, store_id=default_store.id
        )
        acc, _ = _indexed_account(db_session, default_store, tmp_path, other, name="theirs6")
        job = sync_service.create_sync_job(db_session, acc.id, source="api")

        missing = client.get(f"{BASE}/sync/jobs/does-not-exist", headers=_bearer(sync_token))
        not_mine = client.get(f"{BASE}/sync/jobs/{job.id}", headers=_bearer(sync_token))

        assert missing.status_code == not_mine.status_code == 404
        assert missing.content == not_mine.content
