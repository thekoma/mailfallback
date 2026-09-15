from datetime import UTC, datetime

from mailfallback.services import folder_reconcile as fr

WHEN = datetime(2026, 9, 15, 14, 30, tzinfo=UTC)


def test_quarantine_name_carries_the_date_the_folder_left_the_source():
    assert fr.quarantine_name("push-dixie", WHEN) == "push-dixie (2026-09-15 1430)"


def test_quarantine_name_never_contains_a_colon():
    # ":" separates the flags from the filename in Maildir.
    assert ":" not in fr.quarantine_name("push-dixie", WHEN)


def test_quarantine_path_sits_inside_the_container_under_the_account_maildir():
    assert fr.quarantine_path("/data/mailboxes/acct", "push-dixie", WHEN) == (
        "/data/mailboxes/acct/Removed from Source/push-dixie (2026-09-15 1430)"
    )


def test_a_nested_folder_keeps_only_its_leaf_name():
    # "[Gmail]/Spam" must not create a nested tree inside the container.
    assert fr.quarantine_name("[Gmail]/Spam", WHEN) == "[Gmail]-Spam (2026-09-15 1430)"


def test_original_folder_name_strips_the_container_and_the_date():
    quarantined = "Removed from Source/push-dixie (2026-09-15 1430)"
    assert fr.original_folder_name(quarantined) == "push-dixie"


def test_original_folder_name_leaves_an_ordinary_folder_alone():
    assert fr.original_folder_name("Receipt") == "Receipt"
    assert fr.original_folder_name("[Gmail]/All Mail") == "[Gmail]/All Mail"
