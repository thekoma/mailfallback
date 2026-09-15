from datetime import UTC, datetime
from pathlib import Path

import pytest

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


def test_quarantine_name_folds_colons_from_the_folder_name():
    # ":" separates the flags from the filename in Maildir.
    # A provider folder like "Junk:2" must become "Junk-2", not keep the colon.
    assert fr.quarantine_name("Junk:2", WHEN) == "Junk-2 (2026-09-15 1430)"


def test_original_folder_name_strips_the_container_and_the_date():
    quarantined = "Removed from Source/push-dixie (2026-09-15 1430)"
    assert fr.original_folder_name(quarantined) == "push-dixie"


def test_original_folder_name_leaves_an_ordinary_folder_alone():
    assert fr.original_folder_name("Receipt") == "Receipt"
    assert fr.original_folder_name("[Gmail]/All Mail") == "[Gmail]/All Mail"


def test_a_folder_missing_from_the_remote_is_quarantined():
    assert fr.folders_to_quarantine({"INBOX", "push-dixie"}, {"INBOX"}, []) == ["push-dixie"]


def test_a_folder_still_present_upstream_is_left_alone():
    assert fr.folders_to_quarantine({"INBOX", "Receipt"}, {"INBOX", "Receipt"}, []) == []


def test_a_folder_the_user_excluded_is_never_quarantined():
    # It has no .mbsyncstate anyway in practice; this is belt and braces.
    assert fr.folders_to_quarantine({"INBOX", "Spam"}, {"INBOX"}, ["Spam"]) == []


def test_a_glob_exclusion_is_honoured():
    assert fr.folders_to_quarantine({"INBOX", "[Gmail]/Spam"}, {"INBOX"}, ["[Gmail]/*"]) == []


def test_an_empty_remote_list_quarantines_nothing():
    # A failed lookup must never read as "everything was deleted".
    assert fr.folders_to_quarantine({"INBOX", "Receipt"}, set(), []) == []


def test_one_folder_missing_out_of_three_is_ordinary():
    # 33% of a small mailbox: the ratio alone would trip here, which is why
    # the guard needs an absolute floor too.
    assert fr.folders_to_quarantine({"a", "b", "c"}, {"a", "b"}, []) == ["c"]


def test_a_mass_disappearance_is_refused():
    local = {f"f{i}" for i in range(12)}
    with pytest.raises(fr.ProviderAnomaly):
        fr.folders_to_quarantine(local, {"f0", "f1"}, [])


def test_local_synced_folders_only_returns_folders_with_sync_state(tmp_path):
    for folder in ("INBOX", "push-dixie"):
        (tmp_path / folder / "cur").mkdir(parents=True)
        (tmp_path / folder / ".mbsyncstate").write_text("FarUidValidity 1\n")
    (tmp_path / "never-synced" / "cur").mkdir(parents=True)

    assert fr.local_synced_folders(str(tmp_path)) == {"INBOX", "push-dixie"}


def test_local_synced_folders_ignores_the_container(tmp_path):
    q = tmp_path / fr.REMOVED_CONTAINER / "old (2026-09-15 1430)"
    (q / "cur").mkdir(parents=True)
    (q / ".mbsyncstate").write_text("x")

    assert fr.local_synced_folders(str(tmp_path)) == set()


def test_quarantine_folder_moves_the_maildir_and_keeps_the_messages(tmp_path):
    src = tmp_path / "push-dixie" / "cur"
    src.mkdir(parents=True)
    (src / "1.m1.h:2,S").write_text("body")
    (tmp_path / "push-dixie" / ".mbsyncstate").write_text("FarUidValidity 1\n")

    dest = fr.quarantine_folder(str(tmp_path), "push-dixie", WHEN)

    assert dest == str(tmp_path / "Removed from Source" / "push-dixie (2026-09-15 1430)")
    assert (Path(dest) / "cur" / "1.m1.h:2,S").read_text() == "body"
    assert not (tmp_path / "push-dixie").exists()


def test_quarantine_folder_drops_the_sync_state(tmp_path):
    # This is what makes a returning folder a fresh box instead of an
    # "Unable to recover from UIDVALIDITY change" error.
    (tmp_path / "push-dixie" / "cur").mkdir(parents=True)
    (tmp_path / "push-dixie" / ".mbsyncstate").write_text("FarUidValidity 1\n")
    (tmp_path / "push-dixie" / ".mbsyncstate.journal").write_text("x")

    dest = Path(fr.quarantine_folder(str(tmp_path), "push-dixie", WHEN))

    assert not (dest / ".mbsyncstate").exists()
    assert not (dest / ".mbsyncstate.journal").exists()


def test_quarantine_folder_is_a_noop_when_the_folder_is_gone(tmp_path):
    assert fr.quarantine_folder(str(tmp_path), "missing", WHEN) is None


def test_a_second_quarantine_in_the_same_minute_gets_a_suffix(tmp_path):
    for _ in range(2):
        (tmp_path / "push-dixie" / "cur").mkdir(parents=True)
        (tmp_path / "push-dixie" / ".mbsyncstate").write_text("x")
        fr.quarantine_folder(str(tmp_path), "push-dixie", WHEN)

    container = tmp_path / "Removed from Source"
    assert sorted(p.name for p in container.iterdir()) == [
        "push-dixie (2026-09-15 1430)",
        "push-dixie (2026-09-15 1430) (2)",
    ]
