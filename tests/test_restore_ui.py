from unittest.mock import AsyncMock, MagicMock, patch

from mailfallback.models import Account, AuthType, User, UserRole
from mailfallback.services.user_service import create_user


def test_restore_page_redirects_unauthenticated(client):
    resp = client.get("/restore", follow_redirects=False)
    assert resp.status_code == 307


def test_restore_page_renders(client, db_session, default_store):
    create_user(db_session, "uitest", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "uitest", "password": "pass"})
    resp = client.get("/restore")
    assert resp.status_code == 200
    assert "Restore" in resp.text


def test_restore_mailbox_select_lists_accounts_without_backup_policy(
    client, db_session, default_store
):
    """Every account select must list every owned account, not just the ones
    with a BackupPolicy — otherwise searches/restores silently target
    account_id="" and always come up empty."""
    acct = _setup_separator_test(db_session, default_store, client)
    resp = client.get("/restore")
    assert resp.status_code == 200
    # The account id must appear in the sidebar Mailbox select plus the
    # "Another mailbox" select of the destination panel partial (included by
    # the folder AND full presets). The search scope select renders its
    # options from the data island via Alpine x-for, so it adds no
    # Jinja-rendered value.
    assert resp.text.count(f'value="{acct.id}"') == 3
    # ...and in the data island that feeds the scope select and maps account
    # ids to display names.
    assert f'"id": "{acct.id}"' in resp.text


def _extract_island(text, island_id):
    """Parse one JSON data island out of the rendered page."""
    import json
    import re

    m = re.search(
        rf'<script type="application/json" id="{island_id}">(.*?)</script>',
        text,
        re.S,
    )
    return json.loads(m.group(1)) if m else None


def _mk_owned_and_foreign_accounts(db_session, default_store, me):
    """One account owned by `me` + one owned by somebody else."""
    other = User(
        username="someoneelse",
        password_hash="x",
        store_id=default_store.id,
        role=UserRole.user,
    )
    db_session.add(other)
    mine = Account(
        name="mine",
        email_address="mine@example.com",
        imap_host="imap.example.com",
        maildir_path="/data/mailboxes/mine",
        store_id=default_store.id,
    )
    foreign = Account(
        name="foreign",
        email_address="foreign@example.com",
        imap_host="imap.example.com",
        maildir_path="/data/mailboxes/foreign",
        store_id=default_store.id,
    )
    db_session.add_all([mine, foreign])
    db_session.flush()
    mine.owners.append(me)
    foreign.owners.append(other)
    db_session.commit()
    return mine, foreign


def test_restore_page_admin_has_audited_toggle_and_both_islands(client, db_session, default_store):
    """Admins get the audited 'All users' mailboxes' switch plus a second data
    island with every account; the default island stays accessible-only."""
    admin = create_user(db_session, "wsadmin", "pass", UserRole.admin, store_id=default_store.id)
    mine, foreign = _mk_owned_and_foreign_accounts(db_session, default_store, admin)
    client.post("/api/auth/login", json={"username": "wsadmin", "password": "pass"})

    resp = client.get("/restore")

    assert resp.status_code == 200
    assert "ws-admin-toggle" in resp.text
    accessible = _extract_island(resp.text, "ws-accounts-data")
    everything = _extract_island(resp.text, "ws-accounts-all-data")
    assert accessible is not None and everything is not None
    accessible_ids = {a["id"] for a in accessible}
    all_ids = {a["id"] for a in everything}
    assert mine.id in accessible_ids
    assert foreign.id not in accessible_ids
    assert {mine.id, foreign.id} <= all_ids


def test_restore_page_non_admin_has_no_toggle_and_no_all_island(client, db_session, default_store):
    user = create_user(db_session, "wsuser", "pass", UserRole.user, store_id=default_store.id)
    mine, foreign = _mk_owned_and_foreign_accounts(db_session, default_store, user)
    client.post("/api/auth/login", json={"username": "wsuser", "password": "pass"})

    resp = client.get("/restore")

    assert resp.status_code == 200
    assert "ws-admin-toggle" not in resp.text
    assert "ws-accounts-all-data" not in resp.text
    accessible = _extract_island(resp.text, "ws-accounts-data")
    accessible_ids = {a["id"] for a in accessible}
    assert accessible_ids == {mine.id}
    # The foreign account must not leak anywhere in the page.
    assert foreign.id not in resp.text


def test_restore_page_renders_staging_ui(client, db_session, default_store):
    """The workspace ships the staging bar, push panel and both add-to-staging
    entry points (all Alpine-gated client-side — hidden until staging exists,
    but the markup must be on the page)."""
    create_user(db_session, "stguser", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "stguser", "password": "pass"})

    resp = client.get("/restore")

    assert resp.status_code == 200
    assert "ws-staging-bar" in resp.text
    assert "ws-push-panel" in resp.text
    # Push options: destination radios (origin/override) + folder-mode radios.
    assert 'x-model="pushDestination"' in resp.text
    assert 'value="origin"' in resp.text
    assert 'value="override"' in resp.text
    assert 'x-model="pushFolderMode"' in resp.text
    assert 'value="original"' in resp.text
    assert 'value="restored"' in resp.text
    # Add-to-staging entry points: the shared preview pane partial (included
    # once per search preset: single-mail + attachment), the selection action
    # bar, and the attachment table's per-row button.
    assert resp.text.count("Add to staging") == 4
    assert "addToStaging([previewRef])" in resp.text
    assert "addSelectedToStaging()" in resp.text
    # Bar actions.
    assert "emptyStaging()" in resp.text
    assert "pushStaging()" in resp.text
    # Staging feedback slot lives in the bar — statusText only renders inside
    # the two search presets, the bar works in every preset.
    assert 'x-text="stagingStatus"' in resp.text
    # No pre-Alpine flash: bar + panel are cloaked until Alpine boots.
    assert resp.text.count("x-cloak") >= 2
    # The push panel must not float without its bar.
    assert 'x-show="pushPanelOpen && staging.exists"' in resp.text


def test_restore_staging_bar_no_webmail_link_by_default(client, db_session, default_store):
    """Webmail is disabled in the test settings — the bar must not link to
    the Staging mailbox in Roundcube."""
    create_user(db_session, "stgnoweb", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "stgnoweb", "password": "pass"})

    resp = client.get("/restore")

    assert resp.status_code == 200
    assert "_mbox=Staging" not in resp.text
    assert "Open in webmail" not in resp.text


def test_restore_staging_bar_webmail_link_when_enabled(
    client, db_session, default_store, monkeypatch
):
    """Webmail on → the bar links straight to the Staging mailbox. The flags
    are Jinja env globals captured at import, so patch those (setitem
    restores them after the test — they are shared module state)."""
    from mailfallback.routers import ui

    monkeypatch.setitem(ui.templates.env.globals, "webmail_enabled", True)
    monkeypatch.setitem(ui.templates.env.globals, "webmail_url", "http://localhost:8001")
    create_user(db_session, "stgweb", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "stgweb", "password": "pass"})

    resp = client.get("/restore")

    assert resp.status_code == 200
    assert "http://localhost:8001?_task=mail&amp;_mbox=Staging" in resp.text
    assert "Open in webmail" in resp.text


def test_restore_page_renders_attachment_preset(client, db_session, default_store):
    """The 'An attachment' preset ships its panel: type/size filter chips,
    results table with download links and XSS-safe snippet rendering, and
    per-row Preview / Add-to-staging actions wired to the shared plumbing."""
    create_user(db_session, "attui", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "attui", "password": "pass"})

    resp = client.get("/restore")

    assert resp.status_code == 200
    # Panel template + filter chips + results table markup.
    assert "preset === 'attachment'" in resp.text
    assert "ws-fchip" in resp.text
    assert "ws-att-table" in resp.text
    # Filename is a real anchor to the download endpoint (native download).
    assert "attDownloadUrl(a)" in resp.text
    # Snippet renders via the marker-split contract — text nodes only,
    # NEVER x-html (ts_headline output is hostile attachment text).
    assert "attSnippetParts(a.content_snippet)" in resp.text
    assert "ws-snip-mark" in resp.text
    assert "x-html" not in resp.text
    # Row actions delegate to the existing preview/staging methods. The
    # button says "Preview email" — bare "Preview" in an attachment list
    # reads as previewing the FILE.
    assert "openPreview(a)" in resp.text
    assert ">Preview email</button>" in resp.text
    assert ">Preview</button>" not in resp.text
    assert "addToStaging([a])" in resp.text
    # Shared search row routes by preset (message vs attachment search).
    assert "submitSearch()" in resp.text
    # Empty state copy.
    assert "No attachments match" in resp.text


def test_restore_page_preview_pane_close_and_attachment_actions(client, db_session, default_store):
    """The shared preview pane carries an explicit close button, shows which
    attachment it was opened from (selected line + table-row highlight), and
    swaps 'Open webmail' for a 'Download attachment' anchor in the attachment
    context. Both results grids stay full-width until a preview opens."""
    create_user(db_session, "pvui", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "pvui", "password": "pass"})

    resp = client.get("/restore")

    assert resp.status_code == 200
    # Close affordance (real button, in both included copies of the partial).
    assert resp.text.count("ws-preview-close") == 2
    assert "closePreview()" in resp.text
    # Attachment-context feedback: selected-attachment line + row highlight.
    assert "ws-preview-attsel" in resp.text
    assert "previewIsAttachment" in resp.text
    assert "attIsSelected(a)" in resp.text
    # Attachment-context action: native download of the selected attachment.
    assert "attDownloadUrl(previewRef)" in resp.text
    assert "Download attachment" in resp.text
    # The pane's attachment chips are real download anchors (both included
    # copies), built from previewRef ids + the payload's part_index — so
    # attachments are downloadable from the message-preview context too.
    assert resp.text.count('<a class="ws-att-chip" title="Download"') == 2
    assert (
        "attachmentDownloadUrl(previewRef.account_id, previewRef.message_id_hash, a.part_index)"
        in resp.text
    )
    # Both results grids expand/split dynamically with the preview pane.
    assert resp.text.count("'has-preview': previewOpen") == 2


def test_restore_page_folder_preset_multiselect(client, db_session, default_store):
    """The 'A folder / subset' preset offers a checkbox list of source folders
    (multi-select) with select-all/none affordances; the submit button stays
    disabled until at least one folder is checked."""
    create_user(db_session, "folderui", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "folderui", "password": "pass"})

    resp = client.get("/restore")

    assert resp.status_code == 200
    assert "ws-folder-list" in resp.text
    assert 'x-model="selectedFolders"' in resp.text
    assert "selectAllFolders(true)" in resp.text
    assert "selectAllFolders(false)" in resp.text
    # Submit gating on the checked array.
    assert "!selectedFolders.length" in resp.text
    # The old single-folder select is gone.
    assert 'x-model="selectedFolder"' not in resp.text


def test_restore_page_unified_destination_panels(client, db_session, default_store):
    """Folder + full presets share the destination panel partial (rest* state);
    the push panel carries the same pattern on push* state. All three offer
    the Custom folder mode: free-text input (source of truth) + a picker of
    the destination's existing folders that fills it."""
    create_user(db_session, "destui", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "destui", "password": "pass"})

    resp = client.get("/restore")

    assert resp.status_code == 200
    # The partial renders once per preset (folder + full).
    assert resp.text.count('class="ws-dest-panel"') == 2
    assert resp.text.count('x-model="restDestMode"') == 4  # 2 radios x 2 includes
    assert resp.text.count('x-model="restFolderMode"') == 6  # 3 radios x 2 includes
    assert resp.text.count('x-model="restCustomFolder"') == 2
    # Push panel: third folder radio + its own custom input.
    assert 'x-model="pushCustomFolder"' in resp.text
    # Custom radio present in all three panels (2 partial includes + push).
    assert resp.text.count('value="custom"') == 3
    # Existing-folders pickers fill the input (input stays editable) and KEEP
    # their selection via their own model (no snap-back-to-placeholder).
    assert "pickRestFolder()" in resp.text
    assert "pickPushFolder()" in resp.text
    assert resp.text.count('x-model="restPickedFolder"') == 2
    assert 'x-model="pushPickedFolder"' in resp.text
    # Picker FIRST ("Existing folders"), typed path second — in every panel.
    assert resp.text.count("Existing folders") == 3
    assert resp.text.count("or type a new path (created if missing)") == 3
    assert resp.text.index("Existing folders") < resp.text.index(
        "or type a new path (created if missing)"
    )
    # The fill pulses the input as visible confirmation.
    assert "'ws-input-pulse': restFolderPulse" in resp.text
    assert "'ws-input-pulse': pushFolderPulse" in resp.text
    # The sidebar Destination select is gone — destination lives in the panels.
    assert 'x-model="destinationId"' not in resp.text


def test_restore_page_time_controls_row(client, db_session, default_store):
    """The single-mail preset loses its sidebar entirely: Time range + Sources
    live in ONE compact control row under the shared search row — WHEN chips,
    a Custom… popover hosting the always-alive inline flatpickr (x-show, never
    x-if), inline Sources/Deep toggles, status right-aligned. The attachment
    preset reuses the same row (its Type/Size chips queue after WHEN)."""
    create_user(db_session, "whenui", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "whenui", "password": "pass"})

    resp = client.get("/restore")

    assert resp.status_code == 200
    text = resp.text
    # The old sidebar fields are gone — the aside only serves folder/full.
    assert "Time range" not in text
    assert "x-show=\"preset === 'folder' || preset === 'full'\"" in text
    assert "'ws-grid-nosidebar': preset === 'attachment' || preset === 'single-mail'" in text
    # WHEN chips render from the timeChips array; exactly-one-active styling.
    assert 'class="ws-controls"' in text
    assert '@click="setTimePreset(t.id)"' in text
    assert "'is-on': timePreset === t.id" in text
    # Custom… popover: chip label swaps to the applied compact range; the
    # inline calendar input moved INSIDE; dots hint + Clear/Apply footer;
    # click-outside closes.
    assert "ws-when-popover" in text
    assert 'id="ws-calendar-input"' in text
    assert text.index('class="ws-when-popover"') < text.index('id="ws-calendar-input"')
    assert "customLabel" in text
    assert '@click.outside="customPopoverOpen = false"' in text
    assert "= snapshot day" in text
    assert '@click="clearCustomRange()"' in text
    assert '@click="applyCustomRange()"' in text
    # Apply is visibly disabled until a pick exists (not a silent no-op).
    assert ':disabled="!rangeStart"' in text
    # The popover must never be torn down (flatpickr instance) — no x-if.
    assert 'x-if="customPopoverOpen"' not in text
    # Sources + Deep search inline in the row (single-mail only); the deep
    # hint is a tooltip now — honest copy, compact row. All three are LIVE
    # like the chips beside them: changing one re-runs the active search.
    assert 'x-model="includeLive" @change="_requeryActive()"' in text
    assert 'x-model="includeSnapshots" @change="_requeryActive()"' in text
    assert 'x-model="deepSearch" @change="_requeryActive()"' in text
    assert 'title="Also searches message bodies — active mail only, slower"' in text
    assert "also search message bodies (active mail only, slower)" not in text
    # Status text right-aligned at the row's edge.
    assert "ws-controls-status" in text


def test_workspace_js_time_chips_default_all_time(client):
    """All time is the DEFAULT (the 7-day trap is gone); the fixed chips
    compute range_start client-side with an open end; ONE helper feeds both
    search payloads; chip switches re-run the active search through the
    seq-guarded paths."""
    resp = client.get("/static/js/restore_workspace.js")
    assert resp.status_code == 200
    js = resp.text
    assert "timePreset: 'all'" in js
    assert "{id: 'all', label: 'All time'}" in js
    # One range source of truth, sent by BOTH searches (message + attachment).
    assert js.count("const range = this.currentRange();") == 2
    assert js.count("range_start: range.start,") == 2
    assert js.count("range_end: range.end,") == 2
    # The old per-preset default ranges and the Iso getters are gone.
    assert "'single-mail': 7" not in js
    assert "rangeStartIso" not in js
    assert "rangeEndIso" not in js
    # Chip clicks re-query whichever search already ran.
    assert "_requeryActive()" in js
    # Re-querying with an emptied query box clears stale message results
    # instead of leaving them under a newly-clicked chip (the attachment
    # guard's mirror): the clear happens BEFORE any fetch.
    run_search = js[js.index("async runSearch()") :]
    assert "_clearMsgState()" in run_search[: run_search.index("await fetch")]
    # The flatpickr hook only records the PENDING selection; Apply commits.
    assert "applyCustomRange()" in js
    assert "clearCustomRange()" in js
    # Snapshot dots wiring untouched: scope-driven fetch + stale-response guard.
    assert "fetchSnapshotDates()" in js
    assert "_datesSeq" in js


def test_workspace_js_restore_confirms_share_reassurance(client):
    """Every restore entry point confirms first, closing with the shared
    non-destructive reassurance line (frozen copy); the staging Empty confirm
    keeps its own destructive wording (that one DOES delete staged copies)."""
    resp = client.get("/static/js/restore_workspace.js")
    assert resp.status_code == 200
    js = resp.text
    assert (
        "Restores never delete anything: existing messages are kept "
        "and duplicates are skipped." in js
    )
    # All four restore ops gate on a confirm BEFORE their first request.
    for method in [
        "async restoreSelected()",
        "async pushStaging()",
        "async restoreFolder()",
        "async restoreFull()",
    ]:
        body = js[js.index(method) :]
        assert "confirm(" in body[: body.index("await fetch")], method
        assert "RESTORE_REASSURANCE" in body[: body.index("await fetch")], method
    assert "Staged copies are removed" in js  # Empty's destructive confirm, unchanged
    # Hygiene 400s surface their detail in the panels, not a bare status code.
    assert "`Push failed: ${await this._errDetail(resp)}`" in js
    assert js.count("`Failed: ${await this._errDetail(resp)}`") == 2  # folder + full panels


def test_workspace_js_attachment_preset_chip_second(client):
    """The 'An attachment' chip sits SECOND in the presets array (frozen
    visual contract) with the paperclip icon."""
    resp = client.get("/static/js/restore_workspace.js")
    assert resp.status_code == 200
    js = resp.text
    assert "'An attachment'" in js
    assert "paperclip" in js
    single = js.index("id: 'single-mail'")
    attachment = js.index("id: 'attachment'")
    folder = js.index("id: 'folder'")
    assert single < attachment < folder


def test_restore_page_content_toggle_absent_without_tika(client, db_session, default_store):
    """Tika off (the test default) → no 'Search inside attachments' toggle
    (copy-must-match-behavior) and the data attribute reads falsy so the JS
    state stays include_content=False."""
    create_user(db_session, "attnotika", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "attnotika", "password": "pass"})

    resp = client.get("/restore")

    assert resp.status_code == 200
    assert "Search inside attachments" not in resp.text
    assert 'x-model="attIncludeContent"' not in resp.text
    assert 'data-tika-enabled=""' in resp.text


def test_restore_page_content_toggle_present_with_tika(
    client, db_session, default_store, monkeypatch
):
    """Tika on → the content toggle renders (default ON via the data
    attribute the JS init reads)."""
    from mailfallback.config import settings

    monkeypatch.setattr(settings, "tika_enabled", True)
    create_user(db_session, "atttika", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "atttika", "password": "pass"})

    resp = client.get("/restore")

    assert resp.status_code == 200
    assert "Search inside attachments" in resp.text
    assert "text extracted via Tika" in resp.text
    assert 'x-model="attIncludeContent"' in resp.text
    assert 'data-tika-enabled="1"' in resp.text


def _setup_separator_test(db_session, default_store, client):
    """Create a user and target account, login, and return the account."""
    user = User(
        username="sepuser",
        password_hash="x",
        store_id=default_store.id,
        role=UserRole.admin,
    )
    db_session.add(user)
    db_session.flush()
    acct = Account(
        name="target",
        email_address="tgt@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        maildir_path="/data/mailboxes/tgt",
        store_id=default_store.id,
        credentials="encrypted-creds",
    )
    db_session.add(acct)
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(acct)
    acct.owners.append(user)
    db_session.commit()
    with patch("mailfallback.routers.auth.authenticate_user", return_value=user):
        client.post("/api/auth/login", json={"username": "sepuser", "password": "x"})
    return acct


def test_separator_warning_no_target(client, db_session, default_store):
    create_user(db_session, "sepuser2", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "sepuser2", "password": "pass"})
    resp = client.get("/restore/partials/separator-warning?target_account_id=")
    assert resp.status_code == 200
    assert "hidden" in resp.text


def test_separator_warning_dot_separator(client, db_session, default_store):
    acct = _setup_separator_test(db_session, default_store, client)
    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [b'(\\NoSelect) "." ""'])
    mock_conn.logout.return_value = None

    with (
        patch(
            "mailfallback.routers.ui_restore.decrypt_credentials",
            return_value="plainpass",
        ),
        patch(
            "mailfallback.services.imap_check.connect_imap",
            return_value=mock_conn,
        ),
    ):
        resp = client.get(f"/restore/partials/separator-warning?target_account_id={acct.id}")
    assert resp.status_code == 200
    assert "Dot separator detected" in resp.text
    assert "warning-box" in resp.text
    assert "My.Archive" in resp.text
    assert "My_Archive" in resp.text


def test_separator_warning_slash_separator(client, db_session, default_store):
    acct = _setup_separator_test(db_session, default_store, client)
    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [b'(\\NoSelect) "/" ""'])
    mock_conn.logout.return_value = None

    with (
        patch(
            "mailfallback.routers.ui_restore.decrypt_credentials",
            return_value="plainpass",
        ),
        patch(
            "mailfallback.services.imap_check.connect_imap",
            return_value=mock_conn,
        ),
    ):
        resp = client.get(f"/restore/partials/separator-warning?target_account_id={acct.id}")
    assert resp.status_code == 200
    assert "hidden" in resp.text
    assert "warning-box" not in resp.text


def test_separator_warning_oauth2_destination_uses_xoauth2(client, db_session, default_store):
    """The destination probe must connect with XOAUTH2 for oauth2 accounts —
    Gmail/Microsoft reject the refreshed access token via plain LOGIN, which
    silently degraded this warning to the 'Could not connect' info box."""
    acct = _setup_separator_test(db_session, default_store, client)
    acct.auth_type = AuthType.oauth2
    db_session.commit()

    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [b'(\\NoSelect) "/" ""'])
    mock_conn.logout.return_value = None

    with (
        patch(
            "mailfallback.routers.ui_restore.decrypt_credentials",
            return_value='{"provider": "google", "refresh_token": "rt"}',
        ),
        patch(
            "mailfallback.services.oauth2.refresh_google_token",
            new=AsyncMock(return_value="ya29.sep-token"),
        ),
        patch(
            "mailfallback.services.imap_check.connect_imap",
            return_value=mock_conn,
        ) as mock_connect,
    ):
        resp = client.get(f"/restore/partials/separator-warning?target_account_id={acct.id}")

    assert resp.status_code == 200
    assert "Could not connect" not in resp.text
    mock_connect.assert_called_once()
    call = mock_connect.call_args
    assert call.kwargs.get("auth_method") == "xoauth2", call
    password = call.args[4] if len(call.args) > 4 else call.kwargs.get("password")
    assert password == "ya29.sep-token", call


def test_separator_warning_connection_error(client, db_session, default_store):
    acct = _setup_separator_test(db_session, default_store, client)

    with (
        patch(
            "mailfallback.routers.ui_restore.decrypt_credentials",
            return_value="plainpass",
        ),
        patch(
            "mailfallback.services.imap_check.connect_imap",
            side_effect=OSError("Connection refused"),
        ),
    ):
        resp = client.get(f"/restore/partials/separator-warning?target_account_id={acct.id}")
    assert resp.status_code == 200
    assert "Could not connect" in resp.text
    assert "info-box" in resp.text
