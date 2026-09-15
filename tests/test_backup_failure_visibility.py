# tests/test_backup_failure_visibility.py
"""A failing backup has to be visible (#248) and has to notify (#249).

The nightly config backup failed every night for 45 days and nobody noticed.
The serialisation bug behind it (#226) is fixed; these are the two reasons it
stayed invisible:

- the status was rendered only inside a button's `title` tooltip, with no
  visual difference between ok and failed, while a failing *mailbox* backup
  already produced a dashboard Needs Attention entry
- run_config_backup notified on SUCCESS and stayed silent on failure, and the
  event vocabulary had no backup-problem key to subscribe to at all
"""

from unittest.mock import patch

import pytest

from mailfallback.models import BackupStatus, UserRole
from mailfallback.routers.ui import _config_backup_attention_items
from mailfallback.services import notification_service as ns
from mailfallback.services.user_service import create_user


class _Repo:
    def __init__(self, id, name, enabled=True, status=None, error=None):
        self.id = id
        self.name = name
        self.config_backup_enabled = enabled
        self.last_config_backup_status = status
        self.last_config_backup_error = error


class TestConfigBackupReachesTheDashboard:
    def test_a_failed_config_backup_becomes_an_error_item(self):
        items = _config_backup_attention_items(
            [
                _Repo(
                    "r1",
                    "Repo01",
                    status="failed",
                    error="Object of type date is not JSON serializable",
                )
            ]
        )

        assert len(items) == 1
        assert items[0]["type"] == "error"
        assert items[0]["name"] == "Repo01"
        assert "date is not JSON serializable" in items[0]["reason"]

    def test_the_item_links_to_the_backup_admin_page_not_an_account(self):
        # The panel's default link is /accounts/{id}; a repository is not an
        # account and that link would 404.
        items = _config_backup_attention_items([_Repo("r1", "Repo01", status="failed")])

        assert items[0]["href"] == "/admin/backup"

    def test_it_carries_a_fallback_reason_when_no_error_was_recorded(self):
        items = _config_backup_attention_items([_Repo("r1", "Repo01", status="failed")])

        assert items[0]["reason"]

    def test_a_successful_config_backup_produces_nothing(self):
        assert _config_backup_attention_items([_Repo("r1", "Repo01", status="ok")]) == []

    def test_a_repository_with_config_backup_off_is_ignored(self):
        # Status can be left over from when it was enabled.
        repos = [_Repo("r1", "Repo01", enabled=False, status="failed")]

        assert _config_backup_attention_items(repos) == []

    def test_a_repository_that_never_ran_produces_nothing(self):
        assert _config_backup_attention_items([_Repo("r1", "Repo01", status=None)]) == []


class TestBackupFailedIsASubscribableProblem:
    def test_backup_failed_is_a_problem_event(self):
        # Problem, not activity: it shares the opt-in group with sync_error and
        # inherits the same "tell me when something breaks" intent.
        assert "backup_failed" in ns.PROBLEM_EVENT_KEYS
        assert "backup_failed" not in ns.ACTIVITY_EVENT_KEYS

    def test_the_event_list_offered_in_the_ui_matches_the_service(self):
        # The profile template used to hardcode its own copy of this list, so
        # adding a key to the service left it unsubscribable — the checkbox
        # simply never appeared.
        offered = {e[0] for e in ns.PROBLEM_EVENT_OPTIONS} | {
            e[0] for e in ns.ACTIVITY_EVENT_OPTIONS
        }
        assert offered == set(ns.EVENT_KEYS)

    def test_every_option_carries_a_label_and_a_badge_class(self):
        for value, badge, css, label in ns.PROBLEM_EVENT_OPTIONS + ns.ACTIVITY_EVENT_OPTIONS:
            assert value and badge and css and label


@pytest.fixture
def repo_and_admin(db_session, default_store):
    from mailfallback.config import settings
    from mailfallback.models import BackendType, Repository
    from mailfallback.security import encrypt_credentials

    admin = create_user(
        db_session, "bfadmin", "pass1234", UserRole.admin, store_id=default_store.id
    )
    repo = Repository(
        name="Repo01",
        backend_type=BackendType.s3,
        s3_endpoint=encrypt_credentials("https://s3.example.com", settings.secret_key),
        s3_bucket=encrypt_credentials("b", settings.secret_key),
        s3_access_key=encrypt_credentials("ak", settings.secret_key),
        s3_secret_key=encrypt_credentials("sk", settings.secret_key),  # pragma: allowlist secret
        restic_password=encrypt_credentials("rp", settings.secret_key),  # pragma: allowlist secret
        config_backup_enabled=True,
        config_backup_passphrase=encrypt_credentials("pw", settings.secret_key),
    )
    db_session.add(repo)
    db_session.commit()
    return repo, admin


class TestConfigBackupNotifiesOnFailure:
    def test_a_failed_config_backup_emits_backup_failed(self, db_session, repo_and_admin):
        from mailfallback.services.config_backup_service import run_config_backup

        repo, admin = repo_and_admin
        with (
            patch(
                "mailfallback.services.restic_service.init_repo",
                side_effect=RuntimeError("bucket unreachable"),
            ),
            patch.object(ns, "notify_users", autospec=True) as notify,
        ):
            result = run_config_backup(db_session, repo)

        assert result["ok"] is False
        assert notify.call_count == 1
        # It is a repository-level failure with no account, so it has to be
        # addressed to the admins explicitly.
        assert notify.call_args.args[1] == [admin.id]
        assert notify.call_args.args[2] == "backup_failed"
        assert repo.last_config_backup_status == "failed"

    def test_the_notification_cannot_turn_a_recorded_failure_into_a_crash(
        self, db_session, repo_and_admin
    ):
        # The status write is already committed when the notify runs; a send
        # that raises must not escape and must not undo it.
        from mailfallback.services.config_backup_service import run_config_backup

        repo, _ = repo_and_admin
        with (
            patch(
                "mailfallback.services.restic_service.init_repo",
                side_effect=RuntimeError("bucket unreachable"),
            ),
            patch.object(
                ns, "notify_users", autospec=True, side_effect=RuntimeError("apprise exploded")
            ),
        ):
            result = run_config_backup(db_session, repo)

        assert result["ok"] is False
        assert repo.last_config_backup_status == "failed"
        assert "bucket unreachable" in (repo.last_config_backup_error or "")


class TestMailboxBackupNotifiesOnFailure:
    def test_a_failed_mailbox_backup_emits_backup_failed_to_the_owners(
        self, db_session, default_store
    ):
        from mailfallback.services.account_service import assign_owner, create_account
        from mailfallback.services.backup_worker import _notify_backup_failed

        user = create_user(
            db_session, "bfowner", "pass1234", UserRole.user, store_id=default_store.id
        )
        account = create_account(
            db_session, "Main", "imap.example.com", 993, "app_password", store=default_store
        )
        assign_owner(db_session, account.id, user.id)

        # autospec, not a hand-written lambda: notify_account_problem takes no
        # details payload, and a call that does not match its real signature
        # raises TypeError into the caller's own except, degrading the
        # notification to a logged warning that never sends. A permissive
        # lambda hides exactly that.
        with patch.object(ns, "notify_account_problem", autospec=True) as notify:
            _notify_backup_failed(db_session, account, "restic exited 1")

        assert notify.call_count == 1
        args = notify.call_args.args
        assert args[1] is account
        assert args[2] == "backup_failed"

    def test_it_never_raises(self, db_session, default_store):
        from mailfallback.services.account_service import create_account
        from mailfallback.services.backup_worker import _notify_backup_failed

        account = create_account(
            db_session, "Main2", "imap.example.com", 993, "app_password", store=default_store
        )
        with patch.object(
            ns, "notify_account_problem", autospec=True, side_effect=RuntimeError("boom")
        ):
            _notify_backup_failed(db_session, account, "restic exited 1")  # must not raise


def test_backup_status_enum_is_untouched():
    # Guard: this work must not quietly change the mailbox backup vocabulary.
    assert BackupStatus.failed.value == "failed"


class TestExistingChannelsGetTheNewProblemEvent:
    """Adding a key to the vocabulary is not enough on its own.

    Subscription is strict opt-in (`event_key in channel.events`), so without a
    backfill every channel that already exists stays unsubscribed and the fix
    delivers nothing to anyone already set up — the same silence #249 is about,
    reached by a different route.

    Scoped to channels that already take at least one PROBLEM event: those
    users asked to hear about breakage, and a new class of breakage is inside
    that intent. A channel that deliberately takes only activity events is left
    alone.
    """

    def _channel(self, db, user_id, events, label="c"):
        from mailfallback.config import settings
        from mailfallback.models import NotificationChannel
        from mailfallback.security import encrypt_credentials

        ch = NotificationChannel(
            user_id=user_id,
            label=label,
            apprise_url=encrypt_credentials("json://example.com", settings.secret_key),
            events=events,
        )
        db.add(ch)
        db.commit()
        db.refresh(ch)
        return ch

    def test_a_channel_subscribed_to_problems_gains_backup_failed(self, db_session, default_store):
        from mailfallback.app import _backfill_backup_failed_subscription

        user = create_user(db_session, "bf1", "pass1234", UserRole.user, store_id=default_store.id)
        ch = self._channel(db_session, user.id, ["sync_error", "stale"])

        assert _backfill_backup_failed_subscription(db_session) == 1

        db_session.refresh(ch)
        assert "backup_failed" in ch.events
        assert "sync_error" in ch.events  # nothing else disturbed

    def test_an_activity_only_channel_is_left_alone(self, db_session, default_store):
        from mailfallback.app import _backfill_backup_failed_subscription

        user = create_user(db_session, "bf2", "pass1234", UserRole.user, store_id=default_store.id)
        ch = self._channel(db_session, user.id, ["sync_completed"])

        assert _backfill_backup_failed_subscription(db_session) == 0

        db_session.refresh(ch)
        assert "backup_failed" not in ch.events

    def test_a_channel_with_no_events_is_left_alone(self, db_session, default_store):
        from mailfallback.app import _backfill_backup_failed_subscription

        user = create_user(db_session, "bf3", "pass1234", UserRole.user, store_id=default_store.id)
        ch = self._channel(db_session, user.id, [])

        assert _backfill_backup_failed_subscription(db_session) == 0

        db_session.refresh(ch)
        assert ch.events == []

    def test_it_is_idempotent(self, db_session, default_store):
        from mailfallback.app import _backfill_backup_failed_subscription

        user = create_user(db_session, "bf4", "pass1234", UserRole.user, store_id=default_store.id)
        ch = self._channel(db_session, user.id, ["sync_error"])

        _backfill_backup_failed_subscription(db_session)
        assert _backfill_backup_failed_subscription(db_session) == 0

        db_session.refresh(ch)
        assert ch.events.count("backup_failed") == 1

    def test_a_disabled_channel_is_still_updated(self, db_session, default_store):
        # Disabled is a send-time state, not an unsubscribe; re-enabling it
        # later must not silently lack the event.
        from mailfallback.app import _backfill_backup_failed_subscription

        user = create_user(db_session, "bf5", "pass1234", UserRole.user, store_id=default_store.id)
        ch = self._channel(db_session, user.id, ["sync_error"])
        ch.enabled = False
        db_session.commit()

        assert _backfill_backup_failed_subscription(db_session) == 1

        db_session.refresh(ch)
        assert "backup_failed" in ch.events


class TestTheDashboardActuallyRendersIt:
    """The unit tests above cover the item builder, not the template.

    A Jinja error in the attention block — the repository entry uses an href
    instead of the account link every other entry assumes — would pass every
    one of them and break the page at runtime.
    """

    def test_an_admin_sees_the_failed_config_backup_on_the_dashboard(
        self, client, db_session, repo_and_admin
    ):
        repo, _admin = repo_and_admin
        repo.last_config_backup_status = "failed"
        repo.last_config_backup_error = "Object of type date is not JSON serializable"
        db_session.commit()
        client.post("/api/auth/login", json={"username": "bfadmin", "password": "pass1234"})

        resp = client.get("/")

        assert resp.status_code == 200
        assert "Repo01" in resp.text
        assert "date is not JSON serializable" in resp.text
        assert 'href="/admin/backup"' in resp.text
        # and never the account link the other entries use
        assert f'href="/accounts/{repo.id}"' not in resp.text

    def test_a_non_admin_does_not(self, client, db_session, repo_and_admin, default_store):
        repo, _ = repo_and_admin
        repo.last_config_backup_status = "failed"
        repo.last_config_backup_error = "boom"
        db_session.commit()
        create_user(db_session, "bfplain", "pass1234", UserRole.user, store_id=default_store.id)
        client.post("/api/auth/login", json={"username": "bfplain", "password": "pass1234"})

        resp = client.get("/")

        assert resp.status_code == 200
        assert "Repo01" not in resp.text

    def test_a_healthy_repository_adds_no_entry(self, client, db_session, repo_and_admin):
        repo, _ = repo_and_admin
        repo.last_config_backup_status = "ok"
        db_session.commit()
        client.post("/api/auth/login", json={"username": "bfadmin", "password": "pass1234"})

        resp = client.get("/")

        assert resp.status_code == 200
        assert "Repo01" not in resp.text
