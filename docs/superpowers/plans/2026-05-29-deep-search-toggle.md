# Deep Search Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the incomplete "Body" survivor-filter with a single "Deep search" toggle that unions full-folder IMAP `SEARCH BODY` results into the index search, closing the body-match completeness gap (live mail only).

**Architecture:** When `deep=True`, `search_service` runs a full-folder Dovecot `UID SEARCH BODY` across every live folder of the in-scope accounts, hashes the matched messages' Message-Ids, and folds them into the Phase-1 index query as `tsv_match OR message_id_hash IN body_hashes`. The union/order/pagination all stay in one SQL query. A soft timeout bounds the IMAP loop and reports `partial=true`.

**Tech Stack:** Python, SQLAlchemy, FastAPI, imaplib (Dovecot), Alpine.js UI, pytest (run with `-n auto`).

**Spec:** `docs/superpowers/specs/2026-05-29-deep-search-toggle-design.md`

**Test command (ALWAYS parallel):** `uv run pytest -n auto`

---

## File Structure

- `src/mailfallback/config.py` — add `deep_search_timeout_seconds` setting.
- `src/mailfallback/services/search_service.py` — replace `body` param with `deep`; replace `_dovecot_filter_body` (survivor filter) with `_dovecot_body_search` (full-folder) + `_parse_message_id_from_fetch`; SQL union; `partial` in result.
- `src/mailfallback/routers/restore.py` — `RestoreSearchRequest.body`→`deep`; `WorkspaceSearchRequest.search_body`→`deep`; pass `deep`; add `partial` to wrapper response.
- `src/mailfallback/templates/restore_workspace.html` — remove Body checkbox; add Deep search toggle + live-only note + partial banner.
- `src/mailfallback/static/js/restore_workspace.js` — `filters.body`→`deepSearch`; payload `deep`; render partial banner.
- Tests: `tests/test_search_service.py`, `tests/test_restore_workspace_router.py`.

---

## Task 1: Add `deep_search_timeout_seconds` config

**Files:**
- Modify: `src/mailfallback/config.py:58`
- Test: `tests/test_config_deep_search.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_deep_search.py`:

```python
from mailfallback.config import Settings


def test_deep_search_timeout_default():
    s = Settings()
    assert s.deep_search_timeout_seconds == 10


def test_deep_search_timeout_env_override(monkeypatch):
    monkeypatch.setenv("MAILFALLBACK_DEEP_SEARCH_TIMEOUT_SECONDS", "3")
    s = Settings()
    assert s.deep_search_timeout_seconds == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config_deep_search.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'deep_search_timeout_seconds'`

- [ ] **Step 3: Add the setting**

In `src/mailfallback/config.py`, after the line `search_body_candidate_cap: int = 500` (line 58), add:

```python
    deep_search_timeout_seconds: int = 10
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config_deep_search.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/config.py tests/test_config_deep_search.py
git commit -m "feat(config): deep_search_timeout_seconds setting (default 10s)"
```

---

## Task 2: `_parse_message_id_from_fetch` helper

A Dovecot `UID FETCH ... (BODY[HEADER.FIELDS (MESSAGE-ID)])` reply is a list whose
matched items are `(metadata_bytes, payload_bytes)` tuples; the payload holds the
raw `Message-Id:` header. This helper extracts the bare Message-Id from one item.

**Files:**
- Modify: `src/mailfallback/services/search_service.py` (add helper near bottom)
- Test: `tests/test_search_service.py` (add test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_search_service.py`:

```python
from mailfallback.services.search_service import _parse_message_id_from_fetch


def test_parse_message_id_from_fetch_tuple():
    item = (
        b"1 (UID 7 BODY[HEADER.FIELDS (MESSAGE-ID)] {38}",
        b"Message-ID: <abc@example.com>\r\n\r\n",
    )
    assert _parse_message_id_from_fetch(item) == "<abc@example.com>"


def test_parse_message_id_from_fetch_case_insensitive():
    item = (b"meta", b"message-id:   <X@y>\r\n")
    assert _parse_message_id_from_fetch(item) == "<X@y>"


def test_parse_message_id_from_fetch_non_tuple_returns_none():
    assert _parse_message_id_from_fetch(b")") is None


def test_parse_message_id_from_fetch_missing_header_returns_none():
    assert _parse_message_id_from_fetch((b"meta", b"Subject: hi\r\n")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_search_service.py -k parse_message_id -v`
Expected: FAIL — `ImportError: cannot import name '_parse_message_id_from_fetch'`

- [ ] **Step 3: Implement the helper**

Add to the top imports of `src/mailfallback/services/search_service.py` (after `import logging`):

```python
import re
import time
```

Add this function at the end of `src/mailfallback/services/search_service.py`:

```python
_MESSAGE_ID_RE = re.compile(rb"message-id:\s*(<[^>\r\n]*>)", re.IGNORECASE)


def _parse_message_id_from_fetch(item: Any) -> str | None:
    """Extract the bare Message-Id from one imaplib FETCH response item.

    Matched items are (metadata, payload) tuples; separators (e.g. b')') are not.
    """
    if not isinstance(item, tuple) or len(item) < 2:
        return None
    payload = item[1]
    if not isinstance(payload, (bytes, bytearray)):
        return None
    m = _MESSAGE_ID_RE.search(payload)
    return m.group(1).decode("ascii", errors="replace") if m else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_search_service.py -k parse_message_id -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/search_service.py tests/test_search_service.py
git commit -m "feat(search): _parse_message_id_from_fetch helper"
```

---

## Task 3: `_dovecot_body_search` — full-folder body search (live only)

Replaces `_dovecot_filter_body`. For each in-scope account: list the account's
**live** folders from the index (`deleted_at IS NULL`, distinct `folder_path`),
then per folder `UID SEARCH BODY "<kw>"` + `UID FETCH ... (MESSAGE-ID)`, hashing
each Message-Id. A monotonic deadline bounds the loop → `partial`.

**Files:**
- Modify: `src/mailfallback/services/search_service.py` (replace `_dovecot_filter_body`, lines 182-248)
- Test: `tests/test_search_service.py` (add tests)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_search_service.py`:

```python
import time as _time

from mailfallback.services.search_service import _dovecot_body_search


def _install_fake_dovecot(monkeypatch, conn, selected_folders):
    def fake_connect(db, account):
        return conn, "_restore_test"

    def fake_delete_temp(db, username):
        pass

    monkeypatch.setattr(
        "mailfallback.routers.restore._connect_dovecot_for_account", fake_connect
    )
    monkeypatch.setattr(
        "mailfallback.services.dovecot_auth.delete_temp_imap_user", fake_delete_temp
    )
    monkeypatch.setattr(
        "mailfallback.routers.restore.account_namespace_prefix", lambda a: ""
    )


def test_dovecot_body_search_returns_hashes_for_matched_uids(
    db_session, search_setup, monkeypatch
):
    from mailfallback.services.index_service import _hash_message_id

    selected = []

    class FakeConn:
        def select(self, target, readonly=True):
            selected.append(target)
            return ("OK", [b"3"])

        def uid(self, *args):
            if args[0] == "SEARCH":
                return ("OK", [b"7"])
            if args[0] == "FETCH":
                return (
                    "OK",
                    [(b"1 (UID 7 ...", b"Message-ID: <2@h>\r\n"), b")"],
                )
            return ("NO", [b""])

        def logout(self):
            pass

    _install_fake_dovecot(monkeypatch, FakeConn(), selected)
    acct = search_setup["account"]
    deadline = _time.monotonic() + 10
    matched, partial = _dovecot_body_search(db_session, [acct.id], "hello", deadline)

    assert _hash_message_id("<2@h>") in matched
    assert partial is False


def test_dovecot_body_search_only_selects_live_folders(
    db_session, search_setup, monkeypatch
):
    """A folder that exists only via a deleted (snapshot-only) message must NOT
    be body-searched — deep search is live-only."""
    from mailfallback.models import MailIndexMessage

    acct = search_setup["account"]
    db_session.add(
        MailIndexMessage(
            account_id=acct.id,
            message_id_hash=b"\x08" * 20,
            message_id="<8@h>",
            subject="deleted only",
            folder_path="Trash",
            maildir_filename="8",
            deleted_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    selected = []

    class FakeConn:
        def select(self, target, readonly=True):
            selected.append(target)
            return ("OK", [b"0"])

        def uid(self, *args):
            return ("OK", [b""])

        def logout(self):
            pass

    _install_fake_dovecot(monkeypatch, FakeConn(), selected)
    deadline = _time.monotonic() + 10
    _dovecot_body_search(db_session, [acct.id], "x", deadline)

    assert '"Trash"' not in selected
    assert '"INBOX"' in selected


def test_dovecot_body_search_timeout_sets_partial(
    db_session, search_setup, monkeypatch
):
    class FakeConn:
        def select(self, target, readonly=True):
            return ("OK", [b"0"])

        def uid(self, *args):
            return ("OK", [b""])

        def logout(self):
            pass

    _install_fake_dovecot(monkeypatch, FakeConn(), [])
    acct = search_setup["account"]
    deadline = _time.monotonic() - 1  # already expired
    matched, partial = _dovecot_body_search(db_session, [acct.id], "x", deadline)

    assert matched == set()
    assert partial is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_search_service.py -k dovecot_body_search -v`
Expected: FAIL — `ImportError: cannot import name '_dovecot_body_search'`

- [ ] **Step 3: Replace `_dovecot_filter_body` with `_dovecot_body_search`**

In `src/mailfallback/services/search_service.py`, delete the entire
`_dovecot_filter_body` function (lines 182-248) and replace it with:

```python
def _dovecot_body_search(
    db: Session,
    account_ids: list[str],
    keyword: str,
    deadline: float,
) -> tuple[set[bytes], bool]:
    """Full-folder Dovecot body search across the live folders of each account.

    Returns (matched_message_id_hashes, partial). `partial` is True when the
    monotonic `deadline` was reached before all folders were searched.

    Live-only: folders are taken from index rows with deleted_at IS NULL, so
    snapshot-only mail is never body-searched (Dovecot does not serve it).
    Errors per account/folder are swallowed — they must never fail the search.
    """
    from mailfallback.routers.restore import (
        _connect_dovecot_for_account,
        _sanitize_imap_string,
        account_namespace_prefix,
    )
    from mailfallback.services.dovecot_auth import delete_temp_imap_user
    from mailfallback.services.index_service import _hash_message_id

    matched: set[bytes] = set()
    quoted_kw = _sanitize_imap_string(keyword)
    if not quoted_kw:
        return matched, False

    for account_id in account_ids:
        if time.monotonic() > deadline:
            return matched, True
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            continue
        folders = [
            f[0]
            for f in db.query(MailIndexMessage.folder_path)
            .filter(
                MailIndexMessage.account_id == account_id,
                MailIndexMessage.deleted_at.is_(None),
            )
            .distinct()
            .all()
        ]
        if not folders:
            continue
        try:
            conn, temp_user = _connect_dovecot_for_account(db, account)
        except Exception:
            logger.warning(
                "Deep search: Dovecot connect failed for %s", account_id, exc_info=True
            )
            continue
        try:
            ns = account_namespace_prefix(account)
            for folder in folders:
                if time.monotonic() > deadline:
                    return matched, True
                target = f'"{ns}{_sanitize_imap_string(folder)}"'
                typ, _ = conn.select(target, readonly=True)
                if typ != "OK":
                    continue
                typ, data = conn.uid("SEARCH", "BODY", f'"{quoted_kw}"')
                if typ != "OK" or not data or not data[0]:
                    continue
                uids = data[0].decode().split()
                if not uids:
                    continue
                typ, fdata = conn.uid(
                    "FETCH", ",".join(uids), "(BODY[HEADER.FIELDS (MESSAGE-ID)])"
                )
                if typ != "OK" or not fdata:
                    continue
                for item in fdata:
                    msgid = _parse_message_id_from_fetch(item)
                    if msgid:
                        matched.add(_hash_message_id(msgid))
        finally:
            with contextlib.suppress(Exception):
                conn.logout()
            with contextlib.suppress(Exception):
                delete_temp_imap_user(db, temp_user)
    return matched, False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_search_service.py -k dovecot_body_search -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/search_service.py tests/test_search_service.py
git commit -m "feat(search): _dovecot_body_search — full-folder live body search with deadline"
```

---

## Task 4: Wire `deep` into `search_messages` (SQL union + body_matched + partial)

Replace the `body` parameter with `deep`. When `deep` and `query`: run
`_dovecot_body_search` first, then add `message_id_hash IN body_hashes` to the
query as a union with the tsv match. `body_matched` reflects membership;
`partial` propagates. Drop `phase2_skipped_count`.

**Files:**
- Modify: `src/mailfallback/services/search_service.py:40-179`
- Test: `tests/test_search_service.py` (update existing + add)

- [ ] **Step 1: Update/add the tests**

In `tests/test_search_service.py`, **replace** `test_phase2_body_filter_marks_survivors`
(the `@patch("...._dovecot_filter_body")` test) with:

```python
@patch("mailfallback.services.search_service._dovecot_body_search")
def test_deep_search_unions_body_only_matches(mock_body, db_session, search_setup):
    """deep=True folds body-only matches (whose subject does NOT match the query)
    into the result set and flags them body_matched=True."""
    # "hello world" (hash \x02) does not match query "fattura" by subject,
    # but the body search returns it.
    mock_body.return_value = ({b"\x02" * 20}, False)

    result = search_service.search_messages(
        db_session,
        user=search_setup["user"],
        query="fattura",
        deep=True,
    )
    by_subject = {r["subject"]: r for r in result["results"]}
    # tsv matches + body-only union
    assert "fattura marzo" in by_subject
    assert "old fattura" in by_subject
    assert "hello world" in by_subject  # body-only, unioned in
    assert by_subject["hello world"]["body_matched"] is True
    assert by_subject["fattura marzo"]["body_matched"] is False
    assert result["partial"] is False


@patch("mailfallback.services.search_service._dovecot_body_search")
def test_deep_search_propagates_partial(mock_body, db_session, search_setup):
    mock_body.return_value = (set(), True)
    result = search_service.search_messages(
        db_session, user=search_setup["user"], query="fattura", deep=True
    )
    assert result["partial"] is True


def test_default_search_excludes_body_only_matches(db_session, search_setup):
    """deep defaults to False: a message that only matches by body is NOT
    returned and body_matched is None."""
    result = search_service.search_messages(
        db_session, user=search_setup["user"], query="fattura"
    )
    by_subject = {r["subject"]: r for r in result["results"]}
    assert "hello world" not in by_subject
    assert by_subject["fattura marzo"]["body_matched"] is None
    assert "phase2_skipped_count" not in result
    assert result["partial"] is False
```

Also update `test_phase2_sanitises_crlf_in_keyword`: change the call argument
`body=True` to `deep=True`, and change its `FakeConn.uid` to also accept the
`FETCH` verb returning `("OK", [b""])`. Replace that test body with:

```python
def test_deep_sanitises_crlf_in_keyword(db_session, search_setup, monkeypatch):
    """Deep search keyword sanitisation strips control chars (CRLF) so a
    malicious input can't break out of the IMAP quoted string."""
    captured_searches: list[tuple] = []

    class FakeConn:
        def select(self, *args, **kwargs):
            return ("OK", [b"0"])

        def uid(self, *args):
            captured_searches.append(args)
            return ("OK", [b""])

        def logout(self):
            pass

    def fake_connect(db, account):
        return FakeConn(), "_restore_test"

    def fake_delete_temp(db, username):
        pass

    monkeypatch.setattr("mailfallback.routers.restore._connect_dovecot_for_account", fake_connect)
    monkeypatch.setattr("mailfallback.routers.restore.account_namespace_prefix", lambda a: "")
    monkeypatch.setattr(
        "mailfallback.services.dovecot_auth.delete_temp_imap_user", fake_delete_temp
    )

    result = search_service.search_messages(
        db_session,
        user=search_setup["user"],
        query='evil"\r\nLOGOUT',
        deep=True,
    )
    assert result["total"] >= 0
    for args in captured_searches:
        joined = " ".join(str(a) for a in args)
        assert "\r" not in joined
        assert "\n" not in joined
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_search_service.py -v`
Expected: FAIL — `TypeError: search_messages() got an unexpected keyword argument 'deep'` (and the body-only / partial assertions fail).

- [ ] **Step 3: Rewrite `search_messages`**

In `src/mailfallback/services/search_service.py`, replace the `search_messages`
function (lines 40-179) with:

```python
def search_messages(
    db: Session,
    *,
    user: User,
    query: str,
    account_ids: list[str] | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    include_deleted: bool = True,
    snapshot_id: str | None = None,
    deep: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """Phase 1 (always): Postgres index query over subject/from/to.

    Deep search (deep=True): also run a full-folder Dovecot body search over the
    in-scope accounts' live folders and union the matches into the query via
    `message_id_hash IN body_hashes`. Live-only; bounded by a soft timeout that
    surfaces as `partial`.

    Returns: {results, total, page, page_size, partial}
    """
    empty = {"results": [], "total": 0, "page": page, "page_size": page_size, "partial": False}
    visible = _accessible_account_ids(db, user)
    if not visible:
        return empty
    scope = [a for a in account_ids if a in visible] if account_ids else visible
    if not scope:
        return empty

    body_hashes: set[bytes] = set()
    partial = False
    if deep and query:
        from mailfallback.config import settings

        deadline = time.monotonic() + getattr(settings, "deep_search_timeout_seconds", 10)
        body_hashes, partial = _dovecot_body_search(db, scope, query, deadline)

    q = db.query(MailIndexMessage).filter(MailIndexMessage.account_id.in_(scope))
    if not include_deleted:
        q = q.filter(MailIndexMessage.deleted_at.is_(None))
    # NULL date_sent is treated as "unknown date" and kept in the result set
    # for any range — otherwise messages whose Date: header didn't parse
    # disappear from any date-filtered search.
    if range_start:
        q = q.filter(
            (MailIndexMessage.date_sent >= range_start) | MailIndexMessage.date_sent.is_(None)
        )
    if range_end:
        q = q.filter(
            (MailIndexMessage.date_sent <= range_end) | MailIndexMessage.date_sent.is_(None)
        )
    if snapshot_id:
        q = q.join(
            SnapshotMessage,
            (SnapshotMessage.account_id == MailIndexMessage.account_id)
            & (SnapshotMessage.message_id_hash == MailIndexMessage.message_id_hash),
        ).filter(SnapshotMessage.snapshot_id == snapshot_id)
    if query:
        if db.bind.dialect.name == "postgresql":
            text_match = MailIndexMessage.tsv.op("@@")(func.plainto_tsquery("simple", query))
        else:
            pat = f"%{query}%"
            text_match = (
                (MailIndexMessage.subject.ilike(pat))
                | (MailIndexMessage.from_addr.ilike(pat))
                | (MailIndexMessage.from_name.ilike(pat))
            )
        if body_hashes:
            q = q.filter(text_match | MailIndexMessage.message_id_hash.in_(body_hashes))
        else:
            q = q.filter(text_match)
    q = q.order_by(MailIndexMessage.date_sent.desc().nullslast())

    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()

    if rows:
        hashes = [r.message_id_hash for r in rows]
        snap_rows = (
            db.query(
                SnapshotMessage.account_id,
                SnapshotMessage.message_id_hash,
                SnapshotMessage.snapshot_id,
            )
            .filter(SnapshotMessage.message_id_hash.in_(hashes))
            .all()
        )
        snap_by_msg: dict[tuple[str, bytes], list[str]] = {}
        for acc, h, sid in snap_rows:
            snap_by_msg.setdefault((acc, h), []).append(sid)
    else:
        snap_by_msg = {}

    results = []
    for r in rows:
        results.append(
            {
                "message_id": r.message_id,
                "account_id": r.account_id,
                "subject": r.subject,
                "from_addr": r.from_addr,
                "from_name": r.from_name,
                "to_addrs": r.to_addrs or [],
                "date_sent": r.date_sent.isoformat() if r.date_sent else None,
                "folder_path": r.folder_path,
                "alive_in_live": r.deleted_at is None,
                "snapshots": sorted(snap_by_msg.get((r.account_id, r.message_id_hash), [])),
                "body_matched": (r.message_id_hash in body_hashes) if deep else None,
            }
        )

    return {
        "results": results,
        "total": total,
        "page": page,
        "page_size": page_size,
        "partial": partial,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_search_service.py -v`
Expected: PASS (all search_service tests)

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/search_service.py tests/test_search_service.py
git commit -m "feat(search): deep search unions body matches into index query (SQL), drops survivor filter"
```

---

## Task 5: API — `deep` param on both endpoints + `partial` in wrapper response

**Files:**
- Modify: `src/mailfallback/routers/restore.py:565-577` (WorkspaceSearchRequest), `:597-618` (wrapper call), `:697` (wrapper return), `:798-829` (RestoreSearchRequest + api_restore_search)
- Test: `tests/test_restore_workspace_router.py` (add)

- [ ] **Step 1: Write the failing test**

First inspect existing patterns: `uv run pytest tests/test_restore_workspace_router.py -v` to see how the client/login fixtures are used. Then add this test (adapt fixture names to those already in the file — they follow `client` + `login_user` from conftest):

```python
from unittest.mock import patch


@patch("mailfallback.routers.restore.search_service.search_messages")
def test_workspace_search_passes_deep_and_returns_partial(
    mock_search, client, login_user, db_session, default_store
):
    from mailfallback.models import Account

    acct = Account(name="a", store=default_store, maildir_path="/x", imap_host="i")
    db_session.add(acct)
    db_session.flush()
    acct.owners.append(login_user)
    db_session.commit()

    mock_search.return_value = {
        "results": [],
        "total": 0,
        "page": 1,
        "page_size": 200,
        "partial": True,
    }

    resp = client.post(
        "/api/restore/workspace/search",
        json={
            "account_id": acct.id,
            "query": "google",
            "range_start": "2000-01-01T00:00:00Z",
            "range_end": "2030-01-01T00:00:00Z",
            "deep": True,
        },
    )
    assert resp.status_code == 200
    assert mock_search.call_args.kwargs["deep"] is True
    assert resp.json()["partial"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_restore_workspace_router.py -k passes_deep -v`
Expected: FAIL — the request model has no `deep` field (422) or the response lacks `partial`.

- [ ] **Step 3: Apply the API changes**

In `src/mailfallback/routers/restore.py`:

(a) In `WorkspaceSearchRequest` (lines 565-577), replace the line
`    search_body: bool = False` with:

```python
    deep: bool = False  # full-folder body search (replaces legacy search_body)
```

(b) In `workspace_search`, replace lines 597-618 (the criteria block + the
`search_service.search_messages(...)` call) with:

```python
    new_result = search_service.search_messages(
        db,
        user=user,
        query=req.query,
        account_ids=[req.account_id],
        range_start=req.range_start,
        range_end=req.range_end,
        include_deleted=req.include_snapshots,
        deep=req.deep,
        page=1,
        page_size=200,
    )
```

(c) Replace the wrapper's final return (line 697)
`    return {"results": legacy_results, "mounted_snapshots": []}` with:

```python
    return {
        "results": legacy_results,
        "mounted_snapshots": [],
        "partial": new_result.get("partial", False),
    }
```

(d) In `RestoreSearchRequest` (lines 798-807), replace `    body: bool = False`
with:

```python
    deep: bool = False
```

(e) In `api_restore_search` (lines 817-829), replace `        body=req.body,`
with:

```python
        deep=req.deep,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_restore_workspace_router.py -k passes_deep -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite to catch regressions**

Run: `uv run pytest -n auto`
Expected: all green (the previous 490 + new tests). Fix any reference to the
removed `body`/`search_body`/`phase2_skipped_count` names if a test surfaces them.

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/routers/restore.py tests/test_restore_workspace_router.py
git commit -m "feat(api): deep search param on search endpoints + partial in wrapper response"
```

---

## Task 6: UI — Deep search toggle, live-only note, partial banner

UI behavior is verified manually in the browser (see Step 4); no unit test.

**Files:**
- Modify: `src/mailfallback/templates/restore_workspace.html:86-108`
- Modify: `src/mailfallback/static/js/restore_workspace.js:23` and `:214-247`

- [ ] **Step 1: Template — replace Body checkbox + add toggle/note/banner**

In `src/mailfallback/templates/restore_workspace.html`:

(a) Remove the Body checkbox line (line 94):
`                <label class="ws-inline"><input type="checkbox" x-model="filters.body"> Body</label>`

(b) Add the Deep search toggle and live-only note immediately after the closing
`</form>` (after line 84), before the `<details class="ws-filters" ...>`:

```html
          <label class="ws-inline ws-deep-toggle">
            <input type="checkbox" x-model="deepSearch">
            Deep search <span class="text-muted text-xsmall">— also search message bodies (active mail only, slower)</span>
          </label>
```

(c) Add a partial-results banner right after the status line (after line 108
`<p class="ws-status ..."></p>`):

```html
          <p class="ws-status text-warning text-xsmall" x-show="partial">
            Partial results — the deep search timed out. Narrow the date range for completeness.
          </p>
```

- [ ] **Step 2: JS — state + payload + partial handling**

In `src/mailfallback/static/js/restore_workspace.js`:

(a) Replace the filters/state line (line 23) `    filters: {subject: true, from: false, to: false, body: false, type: 'all'},`
with:

```javascript
    filters: {subject: true, from: false, to: false, type: 'all'},
    deepSearch: false,
    partial: false,
```

(b) In `runSearch()`, replace the payload line (line 236)
`            search_body: this.filters.body,` with:

```javascript
            deep: this.deepSearch,
```

(c) In `runSearch()`, at the start of the try (right after `this.results = [];`
near line 219) add `this.partial = false;`. Then after
`this.results = body.results || [];` (line 246) add:

```javascript
        this.partial = !!body.partial;
```

- [ ] **Step 3: Rebuild the container so the new static assets/templates load**

```bash
docker compose up -d --build mailfallback
```

Expected: container rebuilds and starts. (Per memory: never `docker compose down -v`.)

- [ ] **Step 4: Manual browser verification**

Open the restore workspace, select the account, and verify:
- The "Body" checkbox is gone; a "Deep search" toggle is visible near the search box with the live-only note.
- Searching "google" on `andrea.cervesato@live.it` with Deep search **off** returns the Phase-1 count; with Deep search **on** returns more (the body-only matches), approaching the Roundcube count (~9 vs 5).
- Forcing a timeout (set `MAILFALLBACK_DEEP_SEARCH_TIMEOUT_SECONDS=0`, rebuild) shows the partial-results banner.

State explicitly in the task notes what you observed. If the browser can't be driven, say so rather than claiming success.

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/templates/restore_workspace.html src/mailfallback/static/js/restore_workspace.js
git commit -m "feat(ui): replace Body checkbox with Deep search toggle + partial banner"
```

---

## Final verification

- [ ] Run the full suite in parallel: `uv run pytest -n auto` → all green.
- [ ] `git grep -n "search_body\|phase2_skipped_count\|_dovecot_filter_body\|\bbody=req\." src/ tests/` returns nothing (all old names removed).
- [ ] Confirm the deep search end-to-end in the browser (Task 6 Step 4).
