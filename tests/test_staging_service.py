"""Staging service — copy-in, reconcile, quota, lifecycle (real Maildirs).

Live messages are read straight from disk via the index locator; snapshot-only
messages go through restic (mocked here exactly like tests/test_preview_service.py).
"""

import hashlib
import os
import re
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

from mailfallback.config import settings
from mailfallback.models import (
    Account,
    BackupPolicy,
    MailIndexMessage,
    MailStore,
    Repository,
    RestoreJob,
    RestoreMode,
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


def _mk_push_account(db_session, store, tmp_path, name):
    """Pushable target: create_restore_job requires credentials + not busy."""
    acc = Account(
        name=name,
        imap_host="h",
        maildir_path=str(tmp_path / f"mail-{name}"),
        store_id=store.id,
        credentials="enc",
    )
    db_session.add(acc)
    db_session.commit()
    return acc


def _stage_raw(db_session, user, account, msgid, fname, folder="INBOX"):
    """Staged file + row built directly (no indexer) — full folder control."""
    sdir = staging_service.staging_dir(user)
    os.makedirs(os.path.join(sdir, "cur"), exist_ok=True)
    raw = f"Message-ID: {msgid}\r\nSubject: s\r\n\r\nbody".encode()
    with open(os.path.join(sdir, "cur", fname), "wb") as f:
        f.write(raw)
    area = db_session.query(StagingArea).filter_by(user_id=user.id).first()
    if area is None:
        area = StagingArea(user_id=user.id, expires_at=datetime.now(UTC) + timedelta(hours=1))
        db_session.add(area)
        db_session.flush()
    db_session.add(
        StagingMessage(
            staging_id=area.id,
            source_account_id=account.id,
            message_id_hash=hashlib.sha1(msgid.encode(), usedforsecurity=False).digest(),
            original_folder=folder,
            staged_filename=fname,
            size_bytes=len(raw),
        )
    )
    area.bytes_used += len(raw)
    db_session.commit()


_SUBMIT = "mailfallback.services.restore_worker.submit_restore_job"


class TestPush:
    def test_push_origin_groups_by_source_and_folder(
        self, db_session, real_store, staging_user, tmp_path
    ):
        """destination="origin": one job per SOURCE account, manifest keyed by
        each row's original folder. Push only creates jobs — rows and files
        stay staged until the worker confirms delivery."""
        acc1 = _mk_push_account(db_session, real_store, tmp_path, "a1")
        acc2 = _mk_push_account(db_session, real_store, tmp_path, "a2")
        _stage_raw(db_session, staging_user, acc1, "<g1@x>", "100.aa.h:2,", folder="INBOX")
        _stage_raw(db_session, staging_user, acc1, "<g2@x>", "101.bb.h:2,", folder="Sent")
        _stage_raw(db_session, staging_user, acc2, "<g3@x>", "102.cc.h:2,", folder="Archive/2025")

        with patch(_SUBMIT) as mock_submit:
            result = staging_service.push(db_session, staging_user, "origin", "original")

        job_ids = result["job_ids"]
        assert len(job_ids) == 2
        assert result["skipped_targets"] == []
        jobs = {j.source_account_id: j for j in db_session.query(RestoreJob).all()}
        assert set(jobs) == {acc1.id, acc2.id}
        for j in jobs.values():
            assert j.restore_mode == RestoreMode.staging_push
            assert j.target_account_id == j.source_account_id
            assert j.skip_duplicates is True
            assert j.requested_by == staging_user.id
            assert j.id in job_ids
        assert jobs[acc1.id].selected_uids == {
            "INBOX": ["100.aa.h:2,"],
            "Sent": ["101.bb.h:2,"],
        }
        assert jobs[acc2.id].selected_uids == {"Archive/2025": ["102.cc.h:2,"]}
        assert {c.args[0] for c in mock_submit.call_args_list} == set(job_ids)
        # Nothing cleaned yet: the worker owns the post-delivery cleanup.
        assert db_session.query(StagingMessage).count() == 3
        assert len(_staged_files(staging_user)) == 3

    def test_push_override_groups_to_single_target(
        self, db_session, real_store, staging_user, tmp_path
    ):
        acc1 = _mk_push_account(db_session, real_store, tmp_path, "a1")
        acc2 = _mk_push_account(db_session, real_store, tmp_path, "a2")
        override = _mk_push_account(db_session, real_store, tmp_path, "dest")
        _stage_raw(db_session, staging_user, acc1, "<o1@x>", "100.aa.h:2,", folder="INBOX")
        _stage_raw(db_session, staging_user, acc2, "<o2@x>", "101.bb.h:2,", folder="Sent")

        with patch(_SUBMIT) as mock_submit:
            result = staging_service.push(db_session, staging_user, override.id, "original")

        job_ids = result["job_ids"]
        assert len(job_ids) == 1
        assert result["skipped_targets"] == []
        job = db_session.query(RestoreJob).one()
        assert job.id == job_ids[0]
        assert job.source_account_id == override.id
        assert job.target_account_id == override.id
        assert job.selected_uids == {"INBOX": ["100.aa.h:2,"], "Sent": ["101.bb.h:2,"]}
        mock_submit.assert_called_once_with(job.id)

    def test_push_restored_folder_mode_uses_dated_folder(
        self, db_session, real_store, staging_user, tmp_path
    ):
        acc = _mk_push_account(db_session, real_store, tmp_path, "a1")
        _stage_raw(db_session, staging_user, acc, "<d1@x>", "100.aa.h:2,", folder="INBOX")
        _stage_raw(db_session, staging_user, acc, "<d2@x>", "101.bb.h:2,", folder="Sent")

        with patch(_SUBMIT):
            result = staging_service.push(db_session, staging_user, "origin", "restored")

        assert len(result["job_ids"]) == 1
        stamp = datetime.now(UTC).strftime("%Y-%m-%d")
        job = db_session.query(RestoreJob).one()
        assert job.selected_uids == {f"Restored/{stamp}": ["100.aa.h:2,", "101.bb.h:2,"]}

    def test_push_without_area_returns_no_jobs(self, db_session, real_store, staging_user):
        with patch(_SUBMIT) as mock_submit:
            result = staging_service.push(db_session, staging_user, "origin", "original")

        assert result == {"job_ids": [], "skipped_targets": []}
        assert db_session.query(RestoreJob).count() == 0
        mock_submit.assert_not_called()

    def test_push_expired_area_is_noop(self, db_session, real_store, staging_user, tmp_path):
        """An expired-but-unswept area is a corpse: pushing from it would
        resurrect content the TTL already condemned."""
        acc = _mk_push_account(db_session, real_store, tmp_path, "a1")
        _stage_raw(db_session, staging_user, acc, "<e1@x>", "100.aa.h:2,")
        area = db_session.query(StagingArea).one()
        area.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db_session.commit()

        with patch(_SUBMIT) as mock_submit:
            result = staging_service.push(db_session, staging_user, "origin", "original")

        assert result == {"job_ids": [], "skipped_targets": []}
        assert db_session.query(RestoreJob).count() == 0
        mock_submit.assert_not_called()

    def test_push_reconciles_first_deletions_win(
        self, db_session, real_store, staging_user, tmp_path
    ):
        """A file deleted in webmail between staging and push must NOT appear
        in any manifest: reconcile runs before grouping."""
        acc = _mk_push_account(db_session, real_store, tmp_path, "a1")
        _stage_raw(db_session, staging_user, acc, "<w1@x>", "100.aa.h:2,")
        _stage_raw(db_session, staging_user, acc, "<w2@x>", "101.bb.h:2,")
        cur = os.path.join(staging_service.staging_dir(staging_user), "cur")
        os.remove(os.path.join(cur, "100.aa.h:2,"))

        with patch(_SUBMIT):
            result = staging_service.push(db_session, staging_user, "origin", "original")

        assert len(result["job_ids"]) == 1
        job = db_session.query(RestoreJob).one()
        assert job.selected_uids == {"INBOX": ["101.bb.h:2,"]}
        # The deleted file's row died in the reconcile, not in some manifest.
        assert db_session.query(StagingMessage).one().staged_filename == "101.bb.h:2,"

    def test_push_custom_folder_mode_nests_everything_verbatim(
        self, db_session, real_store, staging_user, tmp_path
    ):
        """folder_mode="custom": every staged message's manifest folder is the
        user-named path VERBATIM — like "restored" but user-named, and the
        same path across every per-target job."""
        acc1 = _mk_push_account(db_session, real_store, tmp_path, "a1")
        acc2 = _mk_push_account(db_session, real_store, tmp_path, "a2")
        _stage_raw(db_session, staging_user, acc1, "<c1@x>", "100.aa.h:2,", folder="INBOX")
        _stage_raw(db_session, staging_user, acc1, "<c2@x>", "101.bb.h:2,", folder="Sent")
        _stage_raw(db_session, staging_user, acc2, "<c3@x>", "102.cc.h:2,", folder="Archive/2025")

        with patch(_SUBMIT):
            result = staging_service.push(
                db_session, staging_user, "origin", "custom", custom_folder="Recovered/Q2"
            )

        assert len(result["job_ids"]) == 2
        assert result["skipped_targets"] == []
        jobs = {j.source_account_id: j for j in db_session.query(RestoreJob).all()}
        assert jobs[acc1.id].selected_uids == {"Recovered/Q2": ["100.aa.h:2,", "101.bb.h:2,"]}
        assert jobs[acc2.id].selected_uids == {"Recovered/Q2": ["102.cc.h:2,"]}

    @pytest.mark.parametrize("missing", [None, "", "   "])
    def test_push_custom_mode_without_folder_raises(
        self, db_session, real_store, staging_user, tmp_path, missing
    ):
        """Defense in depth below the endpoint validation: custom mode without
        a usable path must fail loudly, never group manifests under "None"."""
        acc = _mk_push_account(db_session, real_store, tmp_path, "a1")
        _stage_raw(db_session, staging_user, acc, "<c4@x>", "100.aa.h:2,")

        with patch(_SUBMIT) as mock_submit, pytest.raises(ValueError):
            staging_service.push(
                db_session, staging_user, "origin", "custom", custom_folder=missing
            )

        assert db_session.query(RestoreJob).count() == 0
        mock_submit.assert_not_called()

    def test_push_skips_busy_targets_and_reports(
        self, db_session, real_store, staging_user, tmp_path
    ):
        """A target with a pending/running job cannot take another one
        (create_restore_job busy check): its messages stay staged and the
        caller learns WHICH targets were skipped instead of a silent short
        job_ids list."""
        busy = _mk_push_account(db_session, real_store, tmp_path, "busy")
        free = _mk_push_account(db_session, real_store, tmp_path, "free")
        db_session.add(
            RestoreJob(
                source_account_id=busy.id,
                target_account_id=busy.id,
                restore_mode=RestoreMode.selection,
                requested_by=staging_user.id,
            )
        )
        db_session.commit()
        _stage_raw(db_session, staging_user, busy, "<b1@x>", "100.aa.h:2,")
        _stage_raw(db_session, staging_user, free, "<b2@x>", "101.bb.h:2,")

        with patch(_SUBMIT) as mock_submit:
            result = staging_service.push(db_session, staging_user, "origin", "original")

        assert result["skipped_targets"] == [busy.id]
        assert len(result["job_ids"]) == 1
        new_job = (
            db_session.query(RestoreJob)
            .filter(RestoreJob.restore_mode == RestoreMode.staging_push)
            .one()
        )
        assert new_job.id == result["job_ids"][0]
        assert new_job.source_account_id == free.id
        mock_submit.assert_called_once_with(new_job.id)
        # The busy target's message is untouched — staged for a later push.
        assert db_session.query(StagingMessage).count() == 2
        assert len(_staged_files(staging_user)) == 2


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
