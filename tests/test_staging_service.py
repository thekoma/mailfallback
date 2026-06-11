"""Staging service — copy-in, reconcile, quota, lifecycle (real Maildirs).

Live messages are read straight from disk via the index locator; snapshot-only
messages go through restic (mocked here exactly like tests/test_preview_service.py).
"""

import os
import re
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import pytest
from sqlalchemy.exc import IntegrityError

from mailfallback.config import settings
from mailfallback.models import (
    Account,
    BackupPolicy,
    MailIndexMessage,
    MailStore,
    Repository,
    SnapshotMessage,
    StagingArea,
    StagingMessage,
    User,
    UserRole,
)
from mailfallback.services import index_service, preview_service, staging_service


def _write_maildir_message(maildir_root, filename, msg):
    cur = os.path.join(maildir_root, "cur")
    os.makedirs(cur, exist_ok=True)
    with open(os.path.join(cur, filename), "wb") as f:
        f.write(msg.as_bytes())


def _msg(msgid, subject="hello"):
    msg = EmailMessage()
    msg["Message-Id"] = msgid
    msg["From"] = "Mittente <sender@example.com>"
    msg["To"] = "dest@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Thu, 11 Jun 2026 10:00:00 +0200"
    msg.set_content("body text")
    return msg


@pytest.fixture
def real_store(db_session, tmp_path):
    """MailStore whose path actually exists on disk — staging_dir() writes under it.

    The conftest default_store points at the fictional /data/mailboxes; the
    staging Maildir lives under the USER's store, so that path must be real.
    """
    store = MailStore(name="staging-store", path=str(tmp_path / "store"))
    db_session.add(store)
    db_session.commit()
    return store


def _mk_user(db_session, store, username="mario", role=UserRole.user):
    user = User(
        username=username,
        password_hash="x",
        role=role,
        enabled=True,
        store_id=store.id,
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def staging_user(db_session, real_store):
    return _mk_user(db_session, real_store)


def _mk_indexed_account(
    db_session,
    store,
    tmp_path,
    owner=None,
    name="acc1",
    msgid="<m1@x>",
    filename="100.m1.host:2,S",
):
    acc = Account(
        name=name,
        imap_host="h",
        maildir_path=str(tmp_path / f"mail-{name}"),
        store_id=store.id,
    )
    db_session.add(acc)
    db_session.flush()
    if owner is not None:
        acc.owners.append(owner)
    db_session.commit()
    _write_maildir_message(acc.maildir_path, filename, _msg(msgid))
    index_service.upsert_message_set(db_session, acc.id)
    row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
    return acc, row


def _make_snapshot_only(db_session, acc, row, filename="100.m1.host:2,S"):
    """Capture the live file's raw bytes, then turn the message snapshot-only:
    live file removed, row marked deleted, repo + policy + snapshot bit wired."""
    live_path = os.path.join(acc.maildir_path, "cur", filename)
    with open(live_path, "rb") as f:
        raw = f.read()
    os.remove(live_path)
    row.deleted_at = datetime.now(UTC)
    db_session.commit()
    repo = Repository(name="r", backend_type="local", local_path="/tmp/r", restic_password="x")
    db_session.add(repo)
    db_session.flush()
    db_session.add(BackupPolicy(account_id=acc.id, destination_id=repo.id))
    db_session.add(
        SnapshotMessage(snapshot_id="ab12", account_id=acc.id, message_id_hash=row.message_id_hash)
    )
    db_session.commit()
    return raw, live_path


def _staged_files(user):
    cur = os.path.join(staging_service.staging_dir(user), "cur")
    return sorted(os.listdir(cur)) if os.path.isdir(cur) else []


class TestAddMessages:
    def test_add_live_message_copies_file_and_accounts_bytes(
        self, db_session, real_store, staging_user, tmp_path
    ):
        acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=staging_user)
        before = datetime.now(UTC)

        out = staging_service.add_messages(
            db_session, staging_user, [(acc.id, row.message_id_hash)]
        )

        assert out == {"staged": 1, "skipped": 0, "failed": 0}
        sdir = staging_service.staging_dir(staging_user)
        files = _staged_files(staging_user)
        assert len(files) == 1
        # Unique random token + stable hash (no positional counter — race-safe),
        # written via tmp/ then renamed into cur/: tmp/ must end up clean.
        assert re.fullmatch(r"\d+\.[0-9a-f]{8}\.[0-9a-f]{12}:2,", files[0])
        assert os.listdir(os.path.join(sdir, "tmp")) == []
        size = os.path.getsize(os.path.join(sdir, "cur", files[0]))
        m = db_session.query(StagingMessage).one()
        assert m.source_account_id == acc.id
        assert m.message_id_hash == row.message_id_hash
        assert m.original_folder == "INBOX"
        assert m.staged_filename == files[0]
        assert m.size_bytes == size
        area = db_session.query(StagingArea).one()
        assert area.user_id == staging_user.id
        assert area.bytes_used == size
        exp = area.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        expected = before + timedelta(minutes=settings.staging_ttl_minutes)
        assert abs((exp - expected).total_seconds()) < 120

    def test_add_snapshot_only_message_uses_dump_file(
        self, db_session, real_store, staging_user, tmp_path, monkeypatch
    ):
        acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=staging_user)
        raw, live_path = _make_snapshot_only(db_session, acc, row)

        def fake_dump(destination, account_id, snapshot_id, path, **kwargs):
            return raw if path == live_path else None

        # staging_service reuses preview_service._snapshot_bytes, whose restic
        # binding is the shared restic_service module — patch it there.
        monkeypatch.setattr(preview_service.restic_service, "dump_file", fake_dump)
        monkeypatch.setattr(
            preview_service.restic_service,
            "list_snapshots",
            lambda *a, **k: [{"short_id": "ab12", "time": "2026-06-01T00:00:00Z"}],
        )

        out = staging_service.add_messages(
            db_session, staging_user, [(acc.id, row.message_id_hash)]
        )

        assert out == {"staged": 1, "skipped": 0, "failed": 0}
        files = _staged_files(staging_user)
        assert len(files) == 1
        staged_path = os.path.join(staging_service.staging_dir(staging_user), "cur", files[0])
        with open(staged_path, "rb") as f:
            assert f.read() == raw

    def test_snapshot_dump_at_cap_counts_failed(
        self, db_session, real_store, staging_user, tmp_path, monkeypatch
    ):
        """restic dump truncates silently at max_bytes — a cap-sized result is
        presumed truncated and must be counted failed, never staged (C1)."""
        acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=staging_user)
        _make_snapshot_only(db_session, acc, row)
        monkeypatch.setattr(staging_service, "STAGING_DUMP_MAX_BYTES", 1024)
        seen_caps = []

        def fake_dump(destination, account_id, snapshot_id, path, **kwargs):
            seen_caps.append(kwargs.get("max_bytes"))
            return b"x" * 1024  # exactly cap-sized, like a truncated dump

        monkeypatch.setattr(preview_service.restic_service, "dump_file", fake_dump)
        monkeypatch.setattr(
            preview_service.restic_service,
            "list_snapshots",
            lambda *a, **k: [{"short_id": "ab12", "time": "2026-06-01T00:00:00Z"}],
        )

        out = staging_service.add_messages(
            db_session, staging_user, [(acc.id, row.message_id_hash)]
        )

        assert out == {"staged": 0, "skipped": 0, "failed": 1}
        assert _staged_files(staging_user) == []
        assert db_session.query(StagingMessage).count() == 0
        # The staging cap (not the smaller preview default) reaches restic dump.
        assert seen_caps[0] == 1024

    def test_quota_exceeded_rejects_before_copy(
        self, db_session, real_store, staging_user, tmp_path, monkeypatch
    ):
        acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=staging_user)
        monkeypatch.setattr(settings, "staging_max_bytes", 10)

        with pytest.raises(staging_service.StagingQuotaExceededError):
            staging_service.add_messages(db_session, staging_user, [(acc.id, row.message_id_hash)])

        # Nothing written AT ALL: no staged file, no row, and crucially no
        # StagingArea / staging Maildir — a rejected add must not burn the TTL.
        assert not os.path.isdir(staging_service.staging_dir(staging_user))
        assert db_session.query(StagingMessage).count() == 0
        assert db_session.query(StagingArea).count() == 0

    def test_add_is_idempotent_per_message(self, db_session, real_store, staging_user, tmp_path):
        acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=staging_user)

        first = staging_service.add_messages(
            db_session, staging_user, [(acc.id, row.message_id_hash)]
        )
        second = staging_service.add_messages(
            db_session, staging_user, [(acc.id, row.message_id_hash)]
        )

        assert first == {"staged": 1, "skipped": 0, "failed": 0}
        assert second == {"staged": 0, "skipped": 1, "failed": 0}
        files = _staged_files(staging_user)
        assert len(files) == 1
        assert db_session.query(StagingMessage).count() == 1
        area = db_session.query(StagingArea).one()
        assert area.bytes_used == db_session.query(StagingMessage).one().size_bytes

    def test_add_is_idempotent_within_batch(self, db_session, real_store, staging_user, tmp_path):
        """The same (account, hash) twice in ONE call stages once (I2)."""
        acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=staging_user)
        items = [(acc.id, row.message_id_hash), (acc.id, row.message_id_hash)]

        out = staging_service.add_messages(db_session, staging_user, items)

        assert out == {"staged": 1, "skipped": 1, "failed": 0}
        assert len(_staged_files(staging_user)) == 1
        assert db_session.query(StagingMessage).count() == 1

    def test_area_creation_race_recovers(
        self, db_session, real_store, staging_user, tmp_path, monkeypatch
    ):
        """Two concurrent first-adds race on unique(user_id): the loser must
        adopt the winner's area instead of erroring out (I3a)."""
        acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=staging_user)
        real_flush = db_session.flush
        state = {}

        def racing_flush(*a, **k):
            # Only hijack the flush that would INSERT the loser's area —
            # autoflushes from ordinary queries must pass through untouched.
            pending_area = any(isinstance(o, StagingArea) for o in db_session.new)
            if state.get("raced") or not pending_area:
                return real_flush(*a, **k)
            state["raced"] = True  # nested commit() below flushes through
            # Simulate the other request: its area lands (and commits) first,
            # so OUR pending insert violates unique(user_id).
            db_session.rollback()
            winner = StagingArea(
                user_id=staging_user.id,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            db_session.add(winner)
            db_session.commit()
            state["winner_id"] = winner.id
            raise IntegrityError(
                "INSERT INTO staging_areas", {}, Exception("UNIQUE constraint failed")
            )

        monkeypatch.setattr(db_session, "flush", racing_flush)

        out = staging_service.add_messages(
            db_session, staging_user, [(acc.id, row.message_id_hash)]
        )

        assert out == {"staged": 1, "skipped": 0, "failed": 0}
        area = db_session.query(StagingArea).one()
        assert area.id == state["winner_id"]
        assert db_session.query(StagingMessage).one().staging_id == state["winner_id"]
        assert len(_staged_files(staging_user)) == 1

    def test_visibility_enforced(self, db_session, real_store, staging_user, tmp_path):
        # Account owned by NOBODY — not accessible to the (non-admin) user.
        acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=None)

        with pytest.raises(ValueError, match="not accessible"):
            staging_service.add_messages(db_session, staging_user, [(acc.id, row.message_id_hash)])
        # include_all is ignored for non-admins — still rejected.
        with pytest.raises(ValueError, match="not accessible"):
            staging_service.add_messages(
                db_session, staging_user, [(acc.id, row.message_id_hash)], include_all=True
            )
        assert db_session.query(StagingMessage).count() == 0
        assert _staged_files(staging_user) == []
        # Rejected adds must not create an area (no TTL burned) — see I1.
        assert db_session.query(StagingArea).count() == 0

    def test_include_all_admin_stages_foreign_account(self, db_session, real_store, tmp_path):
        admin = _mk_user(db_session, real_store, username="boss", role=UserRole.admin)
        acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=None)

        # Without the flag even the admin is scoped to accessible accounts.
        with pytest.raises(ValueError, match="not accessible"):
            staging_service.add_messages(db_session, admin, [(acc.id, row.message_id_hash)])

        out = staging_service.add_messages(
            db_session, admin, [(acc.id, row.message_id_hash)], include_all=True
        )

        assert out == {"staged": 1, "skipped": 0, "failed": 0}
        assert len(_staged_files(admin)) == 1


class TestReconcile:
    def test_reconcile_drops_rows_for_deleted_files(
        self, db_session, real_store, staging_user, tmp_path
    ):
        acc, _row = _mk_indexed_account(db_session, real_store, tmp_path, owner=staging_user)
        _write_maildir_message(acc.maildir_path, "101.m2.host:2,S", _msg("<m2@x>", subject="due"))
        index_service.upsert_message_set(db_session, acc.id)
        rows = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).all()
        assert len(rows) == 2
        staging_service.add_messages(
            db_session, staging_user, [(acc.id, r.message_id_hash) for r in rows]
        )
        area = db_session.query(StagingArea).one()
        staged = db_session.query(StagingMessage).all()
        assert len(staged) == 2
        cur = os.path.join(staging_service.staging_dir(staging_user), "cur")
        # Webmail deletion removes one file...
        os.remove(os.path.join(cur, staged[0].staged_filename))
        # ...and a webmail read renames the other (flag suffix changes).
        renamed = staged[1].staged_filename + "S"
        os.rename(os.path.join(cur, staged[1].staged_filename), os.path.join(cur, renamed))

        dropped = staging_service.reconcile(db_session, staging_user, area)

        assert dropped == 1
        remaining = db_session.query(StagingMessage).all()
        assert len(remaining) == 1
        assert remaining[0].staged_filename == renamed
        assert area.bytes_used == remaining[0].size_bytes

    def test_get_status_reconciles_and_reports(
        self, db_session, real_store, staging_user, tmp_path
    ):
        status = staging_service.get_status(db_session, staging_user)
        assert status == {
            "exists": False,
            "count": 0,
            "bytes_used": 0,
            "expires_at": None,
            "max_bytes": settings.staging_max_bytes,
        }

        acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=staging_user)
        staging_service.add_messages(db_session, staging_user, [(acc.id, row.message_id_hash)])
        cur = os.path.join(staging_service.staging_dir(staging_user), "cur")
        os.remove(os.path.join(cur, os.listdir(cur)[0]))

        status = staging_service.get_status(db_session, staging_user)

        assert status["exists"] is True
        assert status["count"] == 0  # reconcile ran first: deleted file dropped its row
        assert status["bytes_used"] == 0
        assert isinstance(status["expires_at"], str)


class TestLifecycle:
    def test_empty_removes_files_rows_area(self, db_session, real_store, staging_user, tmp_path):
        acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=staging_user)
        staging_service.add_messages(db_session, staging_user, [(acc.id, row.message_id_hash)])
        assert len(_staged_files(staging_user)) == 1

        staging_service.empty(db_session, staging_user)

        assert not os.path.isdir(staging_service.staging_dir(staging_user))
        assert db_session.query(StagingMessage).count() == 0
        assert db_session.query(StagingArea).count() == 0

    def test_cleanup_expired_purges_files_and_area(
        self, db_session, real_store, staging_user, tmp_path
    ):
        acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=staging_user)
        staging_service.add_messages(db_session, staging_user, [(acc.id, row.message_id_hash)])
        area = db_session.query(StagingArea).one()
        area.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db_session.commit()

        purged = staging_service.cleanup_expired(db_session)

        assert purged == 1
        assert db_session.query(StagingArea).count() == 0
        assert db_session.query(StagingMessage).count() == 0
        assert not os.path.isdir(staging_service.staging_dir(staging_user))

    def test_expired_area_swept_and_recreated_on_add(
        self, db_session, real_store, staging_user, tmp_path
    ):
        """An expired-but-unswept area is absent for get_status and replaced by
        a FRESH area (new TTL, old files gone) on the next add (I4)."""
        acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=staging_user)
        staging_service.add_messages(db_session, staging_user, [(acc.id, row.message_id_hash)])
        area = db_session.query(StagingArea).one()
        old_id = area.id
        area.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db_session.commit()

        status = staging_service.get_status(db_session, staging_user)

        assert status["exists"] is False
        assert status["count"] == 0
        # get_status is read-only: it neither purges nor reconciles the corpse.
        assert db_session.query(StagingArea).one().id == old_id
        assert db_session.query(StagingMessage).count() == 1

        out = staging_service.add_messages(
            db_session, staging_user, [(acc.id, row.message_id_hash)]
        )

        assert out == {"staged": 1, "skipped": 0, "failed": 0}
        fresh = db_session.query(StagingArea).one()
        assert fresh.id != old_id
        exp = fresh.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        assert exp > datetime.now(UTC)
        # Old rows died with the old area; the swept dir holds only the re-add.
        assert db_session.query(StagingMessage).one().staging_id == fresh.id
        assert len(_staged_files(staging_user)) == 1

    def test_cleanup_expired_keeps_active_areas(
        self, db_session, real_store, staging_user, tmp_path
    ):
        acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=staging_user)
        staging_service.add_messages(db_session, staging_user, [(acc.id, row.message_id_hash)])

        purged = staging_service.cleanup_expired(db_session)

        assert purged == 0
        assert db_session.query(StagingArea).count() == 1
        assert len(_staged_files(staging_user)) == 1


class TestUserdbPathContract:
    def test_userdb_staging_mail_path_matches_staging_dir(self, client, db_session, real_store):
        """The namespace mail_path published to Dovecot must be byte-identical
        to staging_service.staging_dir() — any drift and webmail silently shows
        an empty Staging/ folder. Username needs sanitization on purpose."""
        user = _mk_user(db_session, real_store, username="mario rossi")
        db_session.add(
            StagingArea(user_id=user.id, expires_at=datetime.now(UTC) + timedelta(hours=1))
        )
        db_session.commit()

        resp = client.get(
            f"/api/internal/dovecot/userdb/{user.username}", headers={"X-API-Key": "test-key"}
        )

        assert resp.status_code == 200
        stg_ns = [n for n in resp.json()["namespaces"] if n["name"].startswith("stg_")]
        assert len(stg_ns) == 1
        assert stg_ns[0]["mail_path"] == staging_service.staging_dir(user)
        assert "mario_rossi" in stg_ns[0]["mail_path"]
