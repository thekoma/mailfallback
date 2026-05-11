"""Tests for the restore workspace router (search across namespaces)."""

from unittest.mock import MagicMock

from mailfallback.routers.restore import _search_namespace_for_query


def test_search_namespace_returns_envelopes():
    conn = MagicMock()
    # SELECT
    conn.select.return_value = ("OK", [b"3"])
    # SEARCH returns three UIDs
    conn.uid.side_effect = [
        ("OK", [b"1 2 3"]),
        # FETCH for each UID — returns minimal envelope tuple
        ("OK", [(b"1 (UID 1 ENVELOPE (...))", b"Subject: hi\r\nFrom: a@b\r\n\r\n")]),
        ("OK", [(b"2 (UID 2 ENVELOPE (...))", b"Subject: hello\r\nFrom: c@d\r\n\r\n")]),
        ("OK", [(b"3 (UID 3 ENVELOPE (...))", b"Subject: bye\r\nFrom: e@f\r\n\r\n")]),
    ]

    hits = _search_namespace_for_query(conn, namespace="snap-abc/", query="hi")

    assert len(hits) == 3
    assert all(h["namespace"] == "snap-abc/" for h in hits)
    assert all(h["folder"] == "INBOX" for h in hits)
    subjects = {h["subject"] for h in hits}
    assert subjects == {"hi", "hello", "bye"}
    uids = {h["uid"] for h in hits}
    assert uids == {"1", "2", "3"}


def test_search_namespace_empty_search_result():
    conn = MagicMock()
    conn.select.return_value = ("OK", [b"0"])
    conn.uid.return_value = ("OK", [b""])

    hits = _search_namespace_for_query(conn, namespace="snap-abc/", query="nope")

    assert hits == []


def test_search_namespace_select_fails():
    conn = MagicMock()
    conn.select.return_value = ("NO", [b"folder missing"])

    hits = _search_namespace_for_query(conn, namespace="missing/", query="anything")

    assert hits == []


def test_search_namespace_empty_namespace_targets_folder_only():
    """An empty namespace (live mailbox) should target just the folder."""
    conn = MagicMock()
    conn.select.return_value = ("OK", [b"1"])
    conn.uid.side_effect = [
        ("OK", [b"1"]),
        ("OK", [(b"1 (UID 1 ENVELOPE (...))", b"Subject: live\r\nFrom: x@y\r\n\r\n")]),
    ]

    hits = _search_namespace_for_query(conn, namespace="", query="live")

    assert len(hits) == 1
    assert hits[0]["namespace"] == ""
    assert hits[0]["folder"] == "INBOX"
    # SELECT should have been called with just "INBOX"
    conn.select.assert_called_once_with('"INBOX"', readonly=True)
