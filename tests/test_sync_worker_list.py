from unittest.mock import MagicMock, patch

from mailfallback.services import sync_worker


class _Conn:
    def __init__(self, lines):
        self._lines = lines

    def list(self):
        return "OK", self._lines

    def logout(self):
        pass


def _account():
    a = MagicMock()
    a.imap_host, a.imap_port, a.tls_type = "imap.example.com", 993, "IMAPS"
    a.imap_user, a.email_address, a.name = "u", "u@example.com", "acct"
    return a


def test_list_returns_selectable_folder_names():
    lines = [
        b'(\\HasNoChildren) "/" "INBOX"',
        b'(\\HasNoChildren) "/" "push-dixie"',
    ]
    with patch("mailfallback.services.imap_check.connect_imap", return_value=_Conn(lines)):
        assert sync_worker._list_upstream_folders(_account(), "p", None) == {
            "INBOX",
            "push-dixie",
        }


def test_noselect_placeholders_are_dropped():
    lines = [
        b'(\\Noselect \\HasChildren) "/" "[Gmail]"',
        b'(\\HasNoChildren) "/" "[Gmail]/All Mail"',
    ]
    with patch("mailfallback.services.imap_check.connect_imap", return_value=_Conn(lines)):
        assert sync_worker._list_upstream_folders(_account(), "p", None) == {"[Gmail]/All Mail"}


def test_a_literal_encoded_name_is_decoded_not_stringified():
    # imaplib yields (prefix_with_flags, name_bytes) for names needing a
    # literal; str(tuple) would garble the name and silently drop the folder.
    lines = [(b'(\\HasNoChildren) "/" {7}', b"Fattur\xc3\xa8")]
    with patch("mailfallback.services.imap_check.connect_imap", return_value=_Conn(lines)):
        assert sync_worker._list_upstream_folders(_account(), "p", None) == {"Fatturè"}


def test_dot_delimiter_nested_folder_is_normalised_to_slash():
    # Self-hosted Dovecot/Courier commonly report "." as the hierarchy
    # delimiter. mbsync (SubFolders Verbatim) still writes nested folders as
    # real nested directories on disk, so the LIST name must be translated
    # to "/" or every nested folder on such a Source reads as removed.
    lines = [
        b'(\\HasNoChildren) "." "INBOX"',
        b'(\\HasNoChildren) "." "Parent.Child"',
    ]
    with patch("mailfallback.services.imap_check.connect_imap", return_value=_Conn(lines)):
        assert sync_worker._list_upstream_folders(_account(), "p", None) == {
            "INBOX",
            "Parent/Child",
        }


def test_slash_delimiter_nested_folder_is_unchanged():
    # Gmail regression guard: a "/" delimiter must be a no-op.
    lines = [b'(\\HasNoChildren) "/" "Parent/Child"']
    with patch("mailfallback.services.imap_check.connect_imap", return_value=_Conn(lines)):
        assert sync_worker._list_upstream_folders(_account(), "p", None) == {"Parent/Child"}


def test_dot_delimiter_literal_encoded_name_is_normalised():
    # The tuple/literal line shape carries the delimiter in its prefix half
    # (line[0]) just like the plain-bytes shape — must be normalised too.
    lines = [(b'(\\HasNoChildren) "." {12}', b"Parent.Fattur\xc3\xa8")]
    with patch("mailfallback.services.imap_check.connect_imap", return_value=_Conn(lines)):
        assert sync_worker._list_upstream_folders(_account(), "p", None) == {"Parent/Fatturè"}


def test_nil_delimiter_does_not_crash_or_mangle_name():
    # A flat namespace reports NIL as its delimiter — no hierarchy to
    # translate. Must not raise and must not touch the name.
    lines = [b'(\\HasNoChildren) NIL "INBOX"']
    with patch("mailfallback.services.imap_check.connect_imap", return_value=_Conn(lines)):
        assert sync_worker._list_upstream_folders(_account(), "p", None) == {"INBOX"}


def test_a_failed_list_returns_an_empty_set():
    class _Bad(_Conn):
        def list(self):
            return "NO", None

    with patch("mailfallback.services.imap_check.connect_imap", return_value=_Bad([])):
        assert sync_worker._list_upstream_folders(_account(), "p", None) == set()


def test_count_upstream_messages_returns_none_when_nothing_selectable():
    """A LIST that succeeds but has nothing selectable (every entry
    \\Noselect) must return None, the SAME as a failed LIST — not (0, 0).
    _list_upstream_folders returns an empty set for both cases by design
    (its docstring: an empty result must never read as "every folder was
    deleted"). Collapsing them back apart here so a transient LIST failure
    could write initial_sync_total_messages = 0 would be strictly worse: it
    would poison that account's progress denominator permanently, where
    None only degrades this one job's ETA. See the comment at the early
    return in _count_upstream_messages."""
    lines = [b'(\\Noselect \\HasChildren) "/" "[Gmail]"']
    account = _account()
    account.extra_config = None
    with patch("mailfallback.services.imap_check.connect_imap", return_value=_Conn(lines)):
        assert sync_worker._count_upstream_messages(account, "p", None) is None


def test_count_upstream_messages_opens_exactly_two_connections():
    """One connection serves _list_upstream_folders' LIST, a second,
    separate one serves the STATUS pass — a deliberate, accepted cost of
    the Task 5 refactor (see task-5-report.md), bounded because this pass
    runs at most once per account, ever (only while
    initial_sync_completed_at is None). Pinned here because the shared
    fake_conn used by test_sync_worker.py's STATUS tests never asserts
    call_count and would not notice a silent regression to more
    connections."""
    fake_conn = MagicMock()
    fake_conn.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
    fake_conn.status.return_value = ("OK", [b'"INBOX" (MESSAGES 5)'])
    account = _account()
    account.extra_config = None
    with patch(
        "mailfallback.services.imap_check.connect_imap", return_value=fake_conn
    ) as mock_connect:
        result = sync_worker._count_upstream_messages(account, "p", None)
    assert result == (5, 1)
    assert mock_connect.call_count == 2  # one LIST connection, one STATUS connection
