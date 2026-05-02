"""Tests for mbsync output parser."""

from mailfallback.services.sync_progress import parse_mbsync_lines

# --- Real production output (Gmail, OAuth, debug mode) ---

GMAIL_FULL_SYNC = """\
isync 1.5.1 called with: '-Dm' '-c' '/tmp/mbsync/ff87ad67.rc' '-a'
Reading configuration file /tmp/mbsync/ff87ad67.rc
Channel cervesato.it
Opening far side store cervesato.it-remote...
Resolving imap.gmail.com...
Opening near side store cervesato.it-local...
Connecting to imap.gmail.com (142.251.168.109:993)...
Connection is now encrypted
Logging in...
[SASL-XOAUTH2] - Requesting authID!
[SASL-XOAUTH2] - Requesting token!
[SASL-XOAUTH2] - filling prompts!
Authenticating with SASL mechanism XOAUTH2...
[SASL-XOAUTH2] - Requesting authID!
[SASL-XOAUTH2] - Requesting token!
Opening far side box INBOX...
Opening near side box INBOX...
Loading far side box...
Loading near side box...
near side: 679 messages, 0 recent
far side: 679 messages, 0 recent
Synchronizing...
Opening far side box [Gmail]/Bozze...
Opening near side box [Gmail]/Bozze...
Loading far side box...
far side: 0 messages, 0 recent
Loading near side box...
near side: 0 messages, 0 recent
Synchronizing...
Opening far side box [Gmail]/Cestino...
Opening near side box [Gmail]/Cestino...
Loading far side box...
far side: 0 messages, 0 recent
Loading near side box...
near side: 0 messages, 0 recent
Synchronizing...
Opening far side box [Gmail]/Importanti...
Opening near side box [Gmail]/Importanti...
Loading far side box...
Loading near side box...
near side: 381 messages, 0 recent
far side: 381 messages, 0 recent
Synchronizing...
Opening far side box [Gmail]/Posta inviata...
Opening near side box [Gmail]/Posta inviata...
Loading far side box...
Loading near side box...
near side: 1888 messages, 0 recent
far side: 1888 messages, 0 recent
Synchronizing...
Opening far side box [Gmail]/Speciali...
Opening near side box [Gmail]/Speciali...
Loading far side box...
far side: 0 messages, 0 recent
Loading near side box...
near side: 0 messages, 0 recent
Synchronizing...
Opening far side box [Gmail]/Tutti i messaggi...
Opening near side box [Gmail]/Tutti i messaggi...
Loading far side box...
Loading near side box...
near side: 2735 messages, 0 recent
far side: 2735 messages, 0 recent
Synchronizing...
Channels: 1    Boxes: 7    Far: +0 *0 #0 -0    Near: +0 *0 #0 -0
""".strip().splitlines()


OUTLOOK_FULL_SYNC = """\
isync 1.5.1 called with: '-Dm' '-c' '/tmp/mbsync/43100370.rc' '-a'
Reading configuration file /tmp/mbsync/43100370.rc
Channel live
Opening far side store live-remote...
Resolving outlook.office365.com...
Opening near side store live-local...
Connecting to outlook.office365.com (40.99.150.66:993)...
Connection is now encrypted
Logging in...
[SASL-XOAUTH2] - Requesting authID!
[SASL-XOAUTH2] - Requesting token!
[SASL-XOAUTH2] - filling prompts!
Authenticating with SASL mechanism XOAUTH2...
[SASL-XOAUTH2] - Requesting authID!
[SASL-XOAUTH2] - Requesting token!
Opening far side box INBOX...
Opening near side box INBOX...
Loading far side box...
Loading near side box...
near side: 10 messages, 0 recent
far side: 10 messages, 0 recent
Synchronizing...
Opening far side box Archive...
Opening near side box Archive...
Loading far side box...
Loading near side box...
near side: 214 messages, 0 recent
far side: 214 messages, 0 recent
Synchronizing...
Opening far side box Drafts...
Opening near side box Drafts...
Loading far side box...
far side: 0 messages, 0 recent
Loading near side box...
near side: 0 messages, 0 recent
Synchronizing...
Channels: 1    Boxes: 3    Far: +0 *0 #0 -0    Near: +0 *0 #0 -0
""".strip().splitlines()


class TestParseEmpty:
    def test_empty_lines(self):
        snap = parse_mbsync_lines([])
        assert snap.phase == "queued"
        assert snap.per_folder == []
        assert snap.raw_tail == []

    def test_blank_lines(self):
        snap = parse_mbsync_lines(["", "  ", ""])
        assert snap.phase == "starting"


class TestPhaseTransitions:
    def test_version_only(self):
        snap = parse_mbsync_lines(["isync 1.5.1 called with: '-a'"])
        assert snap.phase == "starting"
        assert snap.mbsync_version == "1.5.1"

    def test_connecting_phase(self):
        lines = [
            "isync 1.5.1 called with: '-a'",
            "Resolving imap.gmail.com...",
        ]
        snap = parse_mbsync_lines(lines)
        assert snap.phase == "connecting"
        assert snap.connection_host == "imap.gmail.com"

    def test_connecting_with_ip(self):
        lines = [
            "Connecting to imap.gmail.com (142.251.168.109:993)...",
        ]
        snap = parse_mbsync_lines(lines)
        assert snap.phase == "connecting"
        assert snap.connection_host == "imap.gmail.com"
        assert snap.connection_ip == "142.251.168.109"
        assert snap.connection_port == 993

    def test_authenticating_phase(self):
        lines = [
            "Connecting to imap.gmail.com (1.2.3.4:993)...",
            "Connection is now encrypted",
            "Logging in...",
        ]
        snap = parse_mbsync_lines(lines)
        assert snap.phase == "authenticating"
        assert snap.tls_info == "encrypted"

    def test_auth_sasl_method(self):
        lines = [
            "Logging in...",
            "Authenticating with SASL mechanism XOAUTH2...",
        ]
        snap = parse_mbsync_lines(lines)
        assert snap.phase == "authenticating"
        assert snap.auth_method == "XOAUTH2"

    def test_listing_phase(self):
        lines = [
            "Authenticating with SASL mechanism XOAUTH2...",
            "Opening far side box INBOX...",
            "Loading far side box...",
        ]
        snap = parse_mbsync_lines(lines)
        assert snap.phase == "listing"

    def test_syncing_phase(self):
        lines = [
            "Opening far side box INBOX...",
            "near side: 10 messages, 0 recent",
            "far side: 10 messages, 0 recent",
            "Synchronizing...",
        ]
        snap = parse_mbsync_lines(lines)
        assert snap.phase == "syncing"

    def test_done_phase(self):
        lines = [
            "Synchronizing...",
            "Channels: 1    Boxes: 3    Far: +0 *0 #0 -0    Near: +0 *0 #0 -0",
        ]
        snap = parse_mbsync_lines(lines)
        assert snap.phase == "done"


class TestGmailFullSync:
    def test_version(self):
        snap = parse_mbsync_lines(GMAIL_FULL_SYNC)
        assert snap.mbsync_version == "1.5.1"

    def test_channel(self):
        snap = parse_mbsync_lines(GMAIL_FULL_SYNC)
        assert snap.current_channel == "cervesato.it"

    def test_connection(self):
        snap = parse_mbsync_lines(GMAIL_FULL_SYNC)
        assert snap.connection_host == "imap.gmail.com"
        assert snap.connection_ip == "142.251.168.109"
        assert snap.connection_port == 993
        assert snap.tls_info == "encrypted"

    def test_auth(self):
        snap = parse_mbsync_lines(GMAIL_FULL_SYNC)
        assert snap.auth_method == "XOAUTH2"

    def test_phase_done(self):
        snap = parse_mbsync_lines(GMAIL_FULL_SYNC)
        assert snap.phase == "done"

    def test_folder_count(self):
        snap = parse_mbsync_lines(GMAIL_FULL_SYNC)
        assert len(snap.per_folder) == 7

    def test_folder_names(self):
        snap = parse_mbsync_lines(GMAIL_FULL_SYNC)
        names = [f.name for f in snap.per_folder]
        assert names == [
            "INBOX",
            "[Gmail]/Bozze",
            "[Gmail]/Cestino",
            "[Gmail]/Importanti",
            "[Gmail]/Posta inviata",
            "[Gmail]/Speciali",
            "[Gmail]/Tutti i messaggi",
        ]

    def test_folder_message_counts(self):
        snap = parse_mbsync_lines(GMAIL_FULL_SYNC)
        inbox = snap.per_folder[0]
        assert inbox.near == 679
        assert inbox.far == 679
        sent = snap.per_folder[4]
        assert sent.near == 1888
        assert sent.far == 1888

    def test_empty_folder_counts(self):
        snap = parse_mbsync_lines(GMAIL_FULL_SYNC)
        bozze = snap.per_folder[1]
        assert bozze.near == 0
        assert bozze.far == 0

    def test_all_folders_done(self):
        snap = parse_mbsync_lines(GMAIL_FULL_SYNC)
        for f in snap.per_folder:
            assert f.phase == "done"

    def test_summary(self):
        snap = parse_mbsync_lines(GMAIL_FULL_SYNC)
        assert snap.summary is not None
        assert snap.summary["channels"] == 1
        assert snap.summary["boxes"] == 7
        assert snap.summary["far_added"] == 0
        assert snap.summary["near_added"] == 0

    def test_folder_total_from_summary(self):
        snap = parse_mbsync_lines(GMAIL_FULL_SYNC)
        assert snap.folder_total_estimate == 7
        assert snap.folder_total_estimate_source == "summary"

    def test_no_errors(self):
        snap = parse_mbsync_lines(GMAIL_FULL_SYNC)
        assert snap.errors == []
        assert snap.warnings == []

    def test_raw_tail(self):
        snap = parse_mbsync_lines(GMAIL_FULL_SYNC)
        assert len(snap.raw_tail) == 20
        assert "Channels:" in snap.raw_tail[-1]

    def test_folder_index_at_end(self):
        snap = parse_mbsync_lines(GMAIL_FULL_SYNC)
        assert snap.folder_index == 7


class TestOutlookFullSync:
    def test_connection(self):
        snap = parse_mbsync_lines(OUTLOOK_FULL_SYNC)
        assert snap.connection_host == "outlook.office365.com"
        assert snap.connection_ip == "40.99.150.66"

    def test_folder_count(self):
        snap = parse_mbsync_lines(OUTLOOK_FULL_SYNC)
        assert len(snap.per_folder) == 3
        assert snap.summary["boxes"] == 3


class TestTransferProgress:
    def test_far_side_progress(self):
        lines = [
            "Opening far side box INBOX...",
            "near side: 0 messages, 0 recent",
            "far side: 5302 messages, 0 recent",
            "Synchronizing...",
            "F: +142/5302 *0/0 #0/0",
        ]
        snap = parse_mbsync_lines(lines)
        inbox = snap.per_folder[0]
        assert inbox.added_done == 142
        assert inbox.added_total == 5302
        assert inbox.phase == "pulling"

    def test_near_side_progress(self):
        lines = [
            "Opening far side box INBOX...",
            "Synchronizing...",
            "N: +50/200 *3/10 #1/5",
        ]
        snap = parse_mbsync_lines(lines)
        inbox = snap.per_folder[0]
        assert inbox.added_done == 50
        assert inbox.added_total == 200

    def test_verbose_pulling(self):
        lines = [
            "Opening far side box INBOX...",
            "Synchronizing...",
            "Pulling new message 42/1000 (uid 12345)",
        ]
        snap = parse_mbsync_lines(lines)
        inbox = snap.per_folder[0]
        assert inbox.added_done == 42
        assert inbox.added_total == 1000
        assert inbox.phase == "pulling"

    def test_multi_folder_progress(self):
        lines = [
            "Opening far side box INBOX...",
            "far side: 100 messages, 0 recent",
            "Synchronizing...",
            "F: +100/100 *0/0 #0/0",
            "Opening far side box Sent...",
            "far side: 500 messages, 0 recent",
            "Synchronizing...",
            "F: +200/500 *0/0 #0/0",
        ]
        snap = parse_mbsync_lines(lines)
        assert len(snap.per_folder) == 2
        assert snap.per_folder[0].added_done == 100
        assert snap.per_folder[0].phase == "pulling"
        assert snap.per_folder[1].added_done == 200
        assert snap.per_folder[1].phase == "pulling"
        assert snap.current_folder == "Sent"
        assert snap.folder_index == 2

    def test_flagged_and_expunged(self):
        lines = [
            "Opening far side box INBOX...",
            "Synchronizing...",
            "F: +10/100 *5/20 #3/8",
        ]
        snap = parse_mbsync_lines(lines)
        inbox = snap.per_folder[0]
        assert inbox.flagged_done == 5
        assert inbox.flagged_total == 20
        assert inbox.expunged_done == 3
        assert inbox.expunged_total == 8


class TestPriorFolderCount:
    def test_prior_count_used_when_no_summary(self):
        lines = [
            "Opening far side box INBOX...",
            "Synchronizing...",
        ]
        snap = parse_mbsync_lines(lines, prior_folder_count=7)
        assert snap.folder_total_estimate == 7
        assert snap.folder_total_estimate_source == "previous_sync"

    def test_summary_overrides_prior_count(self):
        lines = [
            "Opening far side box INBOX...",
            "Synchronizing...",
            "Channels: 1    Boxes: 5    Far: +0 *0 #0 -0    Near: +0 *0 #0 -0",
        ]
        snap = parse_mbsync_lines(lines, prior_folder_count=7)
        assert snap.folder_total_estimate == 5
        assert snap.folder_total_estimate_source == "summary"


class TestErrorDetection:
    def test_auth_error(self):
        lines = [
            "Connecting to imap.gmail.com (1.2.3.4:993)...",
            "Logging in...",
            "IMAP error: AUTHENTICATIONFAILED",
        ]
        snap = parse_mbsync_lines(lines)
        assert snap.phase == "error"
        assert len(snap.errors) == 1
        err = snap.errors[0]
        assert err.category == "auth"
        assert err.user_message == "Sign-in needed"
        assert err.action == "reauth"
        assert err.actionable is True

    def test_network_error(self):
        lines = [
            "Resolving imap.example.com...",
            "Error: could not connect to imap.example.com",
        ]
        snap = parse_mbsync_lines(lines)
        assert snap.phase == "error"
        assert snap.errors[0].category == "network"
        assert snap.errors[0].action == "retry"

    def test_tls_error(self):
        lines = ["Error: TLS error on imap.example.com"]
        snap = parse_mbsync_lines(lines)
        assert snap.errors[0].category == "tls"

    def test_disk_error(self):
        lines = ["Error: Permission denied writing to /data/mailboxes"]
        snap = parse_mbsync_lines(lines)
        assert snap.errors[0].category == "disk"
        assert snap.errors[0].action == "admin"

    def test_rate_limit_error(self):
        lines = ["IMAP error: Too many connections from this IP"]
        snap = parse_mbsync_lines(lines)
        assert snap.errors[0].category == "rate_limit"

    def test_config_error(self):
        lines = ["Error: Channel not configured for this account"]
        snap = parse_mbsync_lines(lines)
        assert snap.errors[0].category == "config"

    def test_unknown_error(self):
        lines = ["Error: something completely unexpected happened"]
        snap = parse_mbsync_lines(lines)
        assert snap.errors[0].category == "unknown"
        assert snap.errors[0].user_message == "Backup failed — unknown error"
        assert snap.errors[0].action == "none"

    def test_error_line_number(self):
        lines = [
            "isync 1.5.1 called with: '-a'",
            "Resolving host...",
            "Error: Connection refused",
        ]
        snap = parse_mbsync_lines(lines)
        assert snap.errors[0].at_line == 2

    def test_error_preserves_technical_detail(self):
        lines = ["IMAP error: AUTHENTICATIONFAILED for user@gmail.com"]
        snap = parse_mbsync_lines(lines)
        assert "AUTHENTICATIONFAILED" in snap.errors[0].technical_detail

    def test_multiple_errors(self):
        lines = [
            "Error: Connection refused",
            "Error: could not connect to host",
        ]
        snap = parse_mbsync_lines(lines)
        assert len(snap.errors) == 2


class TestWarnings:
    def test_warning_detected(self):
        lines = ["Warning: stripping strippable strippedlist"]
        snap = parse_mbsync_lines(lines)
        assert len(snap.warnings) == 1
        assert "stripping" in snap.warnings[0]

    def test_maildir_error_as_warning(self):
        lines = ["Maildir error: skipping duplicate message"]
        snap = parse_mbsync_lines(lines)
        assert len(snap.warnings) == 1


class TestEdgeCases:
    def test_single_message_grammar(self):
        lines = [
            "Opening far side box INBOX...",
            "near side: 1 message, 0 recent",
            "far side: 1 message, 1 recent",
        ]
        snap = parse_mbsync_lines(lines)
        inbox = snap.per_folder[0]
        assert inbox.near == 1
        assert inbox.far == 1

    def test_folder_not_duplicated(self):
        lines = [
            "Opening far side box INBOX...",
            "Opening near side box INBOX...",
            "Opening far side box INBOX...",
        ]
        snap = parse_mbsync_lines(lines)
        assert len(snap.per_folder) == 1

    def test_summary_with_transfers(self):
        lines = [
            "Channels: 2    Boxes: 12    Far: +150 *30 #5 -2    Near: +0 *0 #0 -0",
        ]
        snap = parse_mbsync_lines(lines)
        assert snap.summary["far_added"] == 150
        assert snap.summary["far_flagged"] == 30
        assert snap.summary["far_expunged"] == 5
        assert snap.summary["far_deleted"] == 2

    def test_partial_output_mid_folder(self):
        lines = [
            "Connecting to imap.gmail.com (1.2.3.4:993)...",
            "Authenticating with SASL mechanism XOAUTH2...",
            "Opening far side box INBOX...",
            "Loading far side box...",
        ]
        snap = parse_mbsync_lines(lines)
        assert snap.phase == "listing"
        assert snap.current_folder == "INBOX"
        assert snap.folder_index == 1
        assert snap.per_folder[0].phase == "loading"
        assert snap.per_folder[0].far is None

    def test_ipv6_connection(self):
        lines = ["Connecting to imap.example.com (::1:993)..."]
        snap = parse_mbsync_lines(lines)
        assert snap.connection_host == "imap.example.com"
        assert snap.connection_ip == "::1"
        assert snap.connection_port == 993

    def test_resolving_strips_trailing_dot(self):
        lines = ["Resolving imap.gmail.com...."]
        snap = parse_mbsync_lines(lines)
        assert snap.connection_host == "imap.gmail.com"

    def test_no_debug_mode_output(self):
        lines = [
            "Channels: 1    Boxes: 3    Far: +50 *0 #0 -0    Near: +0 *0 #0 -0",
        ]
        snap = parse_mbsync_lines(lines)
        assert snap.phase == "done"
        assert snap.mbsync_version is None
        assert snap.summary["far_added"] == 50

    def test_current_folder_tracks_last_opened(self):
        lines = [
            "Opening far side box INBOX...",
            "Synchronizing...",
            "Opening far side box Sent...",
            "Synchronizing...",
            "Opening far side box Drafts...",
        ]
        snap = parse_mbsync_lines(lines)
        assert snap.current_folder == "Drafts"
        assert snap.folder_index == 3

    def test_auth_login_with_method(self):
        lines = ["Logging in as user@example.com (LOGIN)..."]
        snap = parse_mbsync_lines(lines)
        assert snap.auth_method == "LOGIN"
        assert snap.phase == "authenticating"
