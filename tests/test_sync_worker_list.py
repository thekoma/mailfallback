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
    with patch.object(sync_worker, "connect_imap", return_value=_Conn(lines), create=True):
        assert sync_worker._list_upstream_folders(_account(), "p", None) == {
            "INBOX",
            "push-dixie",
        }


def test_noselect_placeholders_are_dropped():
    lines = [
        b'(\\Noselect \\HasChildren) "/" "[Gmail]"',
        b'(\\HasNoChildren) "/" "[Gmail]/All Mail"',
    ]
    with patch.object(sync_worker, "connect_imap", return_value=_Conn(lines), create=True):
        assert sync_worker._list_upstream_folders(_account(), "p", None) == {"[Gmail]/All Mail"}


def test_a_literal_encoded_name_is_decoded_not_stringified():
    # imaplib yields (prefix_with_flags, name_bytes) for names needing a
    # literal; str(tuple) would garble the name and silently drop the folder.
    lines = [(b'(\\HasNoChildren) "/" {7}', b"Fattur\xc3\xa8")]
    with patch.object(sync_worker, "connect_imap", return_value=_Conn(lines), create=True):
        assert sync_worker._list_upstream_folders(_account(), "p", None) == {"Fatturè"}


def test_a_failed_list_returns_an_empty_set():
    class _Bad(_Conn):
        def list(self):
            return "NO", None

    with patch.object(sync_worker, "connect_imap", return_value=_Bad([]), create=True):
        assert sync_worker._list_upstream_folders(_account(), "p", None) == set()
