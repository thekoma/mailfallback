"""Backup state must reach the dashboard's Needs Attention panel.

The panel is already account-keyed with a linked mailbox name — it is the
answer to "which mailbox is that backup job?" — but backups never fed into
it, so the dashboard could only show a bare count.
"""

from datetime import UTC, datetime, timedelta

from mailfallback.models import BackupStatus
from mailfallback.routers.ui import _backup_attention_items


class _Acct:
    def __init__(self, id, name):
        self.id = id
        self.name = name


class _Policy:
    def __init__(self, account_id, status, error=None, run_at=None):
        self.account_id = account_id
        self.last_status = status
        self.last_error = error
        self.last_run_at = run_at


def test_running_backup_produces_an_info_item_naming_the_mailbox():
    items = _backup_attention_items(
        [_Acct("a1", "Main gMail")], [_Policy("a1", BackupStatus.running)]
    )
    assert len(items) == 1
    assert items[0]["name"] == "Main gMail"
    assert items[0]["id"] == "a1"
    assert items[0]["type"] == "info"
    assert "Off-site backup running" in items[0]["reason"]


def test_running_backup_reports_elapsed_time():
    started = datetime.now(UTC) - timedelta(minutes=4)
    items = _backup_attention_items(
        [_Acct("a1", "Main gMail")],
        [_Policy("a1", BackupStatus.running, run_at=started)],
    )
    assert "4m" in items[0]["reason"]


def test_failed_backup_produces_an_error_item_with_the_reason():
    items = _backup_attention_items(
        [_Acct("a1", "Main gMail")],
        [_Policy("a1", BackupStatus.failed, error="repository is locked")],
    )
    assert items[0]["type"] == "error"
    assert items[0]["backup"] is True
    assert "repository is locked" in items[0]["reason"]


def test_failed_backup_without_an_error_still_reads_sensibly():
    items = _backup_attention_items(
        [_Acct("a1", "Main gMail")], [_Policy("a1", BackupStatus.failed)]
    )
    assert "Off-site backup failed" in items[0]["reason"]


def test_long_errors_are_truncated():
    items = _backup_attention_items(
        [_Acct("a1", "M")], [_Policy("a1", BackupStatus.failed, error="x" * 500)]
    )
    assert len(items[0]["reason"]) <= 80


def test_healthy_backup_produces_nothing():
    assert (
        _backup_attention_items(
            [_Acct("a1", "Main gMail")], [_Policy("a1", BackupStatus.completed)]
        )
        == []
    )


def test_idle_backup_produces_nothing():
    assert _backup_attention_items([_Acct("a1", "M")], [_Policy("a1", BackupStatus.idle)]) == []


def test_a_policy_without_a_matching_account_is_skipped():
    assert _backup_attention_items([], [_Policy("ghost", BackupStatus.running)]) == []


def test_each_mailbox_gets_its_own_entry():
    items = _backup_attention_items(
        [_Acct("a1", "Main gMail"), _Acct("a2", "Guerrilla")],
        [
            _Policy("a1", BackupStatus.running),
            _Policy("a2", BackupStatus.failed, error="boom"),
        ],
    )
    assert {i["name"] for i in items} == {"Main gMail", "Guerrilla"}
