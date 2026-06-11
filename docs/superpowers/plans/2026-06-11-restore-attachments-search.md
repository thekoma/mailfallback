# Restore Plan 1/3 — Attachment Index + Cross-Account Search + Preview

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Search all visible mailboxes by default, see attachments (name/ext/size) on every hit, and read any message (live or snapshot) in an in-app preview pane before restoring.

**Architecture:** New `mail_index.attachments` table populated during the existing index walk (MIME parse on first insert only; immutable Maildir files are never re-parsed) with a one-pass backfill CLI. The workspace UI switches from the legacy single-account wrapper to `POST /api/restore/search` (`account_ids: null` = all visible). A new preview service reads message bodies straight from the Maildir (live) or via `restic dump` (snapshot-only). Restore-selected goes **back to origin** per message (destination override arrives with the staging area in Plan 2/3).

**Tech Stack:** FastAPI, SQLAlchemy + Alembic (PostgreSQL; SQLite in tests), Alpine.js, restic CLI subprocess. Spec: `docs/superpowers/specs/2026-06-11-restore-staging-attachments-design.md`. Reference mockup (UI contract, minus the staging bar which is Plan 2): `.claude/mockup_restore_staging_reference.png`.

**Branch:** create `feat/restore-attachments-search` from current `feat/repo-access` HEAD (the two spec commits must be included; the dirty working tree must NOT — use a worktree via superpowers:using-git-worktrees).

**Conventions that bite (from CLAUDE.md / memory):** model + its migration must land in the SAME commit (a hook blocks splits); run tests with `uv run pytest tests/ -n auto`; all CSS in `static/css/style.css` (no inline styles), all JS in `static/js/` (no inline scripts); NOT NULL columns need `server_default`.

---

## File structure

- Create: `alembic/versions/018_mail_index_attachments.py` — attachments table + `messages.has_attachments` / `messages.attachments_indexed_at`
- Modify: `src/mailfallback/models.py` — `MailIndexAttachment`, two new `MailIndexMessage` columns
- Modify: `src/mailfallback/services/index_service.py` — MIME attachment parse on insert + `backfill_attachments`
- Modify: `src/mailfallback/cli/index.py` — `backfill-attachments` subcommand (+ register arg in the CLI parser where `index_cmd` choices are defined — find with `grep -rn "backfill-snapshots" src/mailfallback/cli/`)
- Modify: `src/mailfallback/services/search_service.py` — per-hit attachment enrichment + `message_id_hash` hex in results
- Modify: `src/mailfallback/services/restic_service.py` — `dump_file`
- Create: `src/mailfallback/services/preview_service.py` — message preview (live + snapshot)
- Modify: `src/mailfallback/routers/restore.py` — `GET /api/restore/preview/...`, `POST /api/restore/resolve-uids`
- Modify: `src/mailfallback/templates/restore_workspace.html` — scope select, result badges/chips, preview pane
- Modify: `src/mailfallback/static/js/restore_workspace.js` — new search endpoint, preview, restore-to-origin
- Modify: `src/mailfallback/static/css/style.css` — `.ws-preview*`, `.ws-att*` classes
- Tests: `tests/test_index_attachments.py`, `tests/test_preview_service.py`, extend `tests/test_search_service.py` (exists — check name with `ls tests/ | grep search`), `tests/test_restore_preview_api.py`

---

### Task 1: Models + migration 018 (attachments table)

**Files:**
- Modify: `src/mailfallback/models.py` (after `MailIndexMessage`, ~line 535)
- Create: `alembic/versions/018_mail_index_attachments.py`

- [ ] **Step 1: Add the model and columns**

In `models.py`, add to `MailIndexMessage` (after `tsv = Column(...)`, line 533):

```python
    has_attachments = Column(Boolean, nullable=False, server_default=text("false"))
    attachments_indexed_at = Column(DateTime(timezone=True))
```

(`Boolean` and `text` are already imported in models.py — verify with `grep -n "^from sqlalchemy" src/mailfallback/models.py` and extend the import only if missing.)

After the `MailIndexMessage` class, add:

```python
class MailIndexAttachment(Base):
    """One row per attachment MIME part (a leaf part with a filename).

    `part_index` counts non-multipart leaves in msg.walk() order — the
    download endpoint (Plan 3) re-walks with the same algorithm, so the
    numbering is part of the contract with `index_service._parse_attachments`.
    `content_text` stays NULL until the Tika extraction phase (Plan 3).
    """

    __tablename__ = "attachments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "message_id_hash"],
            ["mail_index.messages.account_id", "mail_index.messages.message_id_hash"],
            ondelete="CASCADE",
        ),
        {"schema": "mail_index"},
    )

    account_id = Column(String, primary_key=True)
    message_id_hash = Column(LargeBinary(20), primary_key=True)
    part_index = Column(Integer, primary_key=True)
    filename = Column(Text, nullable=False)
    ext = Column(Text, nullable=False, server_default="")
    size_bytes = Column(Integer)
    content_type = Column(Text)
    content_text = Column(Text)
```

(`ForeignKeyConstraint` import: check `grep -n "ForeignKeyConstraint" src/mailfallback/models.py`; add to the existing sqlalchemy import if absent. NOTE: migration 014 skips cross-schema FKs on SQLite — mirror that: on SQLite the FK constraint in the migration is omitted; keeping it in the model is fine because tests create tables via the same ATTACH trick used in 014.)

- [ ] **Step 2: Write the migration**

`alembic/versions/018_mail_index_attachments.py`:

```python
"""mail_index.attachments + messages.has_attachments/attachments_indexed_at

Revision ID: 018
Revises: 017
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "018"
down_revision: str | Sequence[str] | None = "017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    cols = [
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("message_id_hash", sa.LargeBinary(20), nullable=False),
        sa.Column("part_index", sa.Integer(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("ext", sa.Text(), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
    ]
    constraints = [sa.PrimaryKeyConstraint("account_id", "message_id_hash", "part_index")]
    if not is_sqlite:
        # SQLite cannot model cross-schema FKs to ATTACHed DBs (same as 014).
        constraints.append(
            sa.ForeignKeyConstraint(
                ["account_id", "message_id_hash"],
                ["mail_index.messages.account_id", "mail_index.messages.message_id_hash"],
                ondelete="CASCADE",
            )
        )
    op.create_table("attachments", *cols, *constraints, schema="mail_index")

    op.add_column(
        "messages",
        sa.Column(
            "has_attachments", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        schema="mail_index",
    )
    op.add_column(
        "messages",
        sa.Column("attachments_indexed_at", sa.DateTime(timezone=True), nullable=True),
        schema="mail_index",
    )

    if not is_sqlite:
        # Combined name+content search index (content_text used from Plan 3 on).
        op.execute(
            "CREATE INDEX idx_attachments_fts ON mail_index.attachments "
            "USING gin (to_tsvector('simple', coalesce(filename, '') || ' ' "
            "|| coalesce(content_text, '')))"
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if not is_sqlite:
        op.execute("DROP INDEX IF EXISTS mail_index.idx_attachments_fts")
    op.drop_table("attachments", schema="mail_index")
    op.drop_column("messages", "attachments_indexed_at", schema="mail_index")
    op.drop_column("messages", "has_attachments", schema="mail_index")
```

- [ ] **Step 3: Verify the migration applies and the suite still passes**

Run: `uv run alembic upgrade head` (against the local dev DB via docker compose env if configured; otherwise rely on tests) and `uv run pytest tests/ -n auto -q`
Expected: upgrade OK, all tests pass (conftest creates tables from models — a model/migration mismatch shows up here or in the drift hook at commit time).

- [ ] **Step 4: Commit (model + migration together — the drift hook requires it)**

```bash
git add src/mailfallback/models.py alembic/versions/018_mail_index_attachments.py
git commit -m "feat(index): mail_index.attachments table + has_attachments flag"
```

---

### Task 2: Parse attachments during the index walk

**Files:**
- Modify: `src/mailfallback/services/index_service.py`
- Create: `tests/test_index_attachments.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_index_attachments.py`:

```python
"""Attachment extraction during the live Maildir index walk."""

import os
from email.message import EmailMessage

from mailfallback.models import Account, MailIndexAttachment, MailIndexMessage
from mailfallback.services import index_service


def _write_maildir_message(maildir_root, filename, msg):
    cur = os.path.join(maildir_root, "cur")
    os.makedirs(cur, exist_ok=True)
    with open(os.path.join(cur, filename), "wb") as f:
        f.write(msg.as_bytes())


def _msg(msgid, subject="hello", attachments=()):
    msg = EmailMessage()
    msg["Message-Id"] = msgid
    msg["From"] = "Mittente <sender@example.com>"
    msg["To"] = "dest@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Thu, 11 Jun 2026 10:00:00 +0200"
    msg.set_content("body text")
    for name, payload in attachments:
        msg.add_attachment(
            payload, maintype="application", subtype="pdf", filename=name
        )
    return msg


def _mk_account(db_session, default_store, tmp_path):
    acc = Account(
        name="acc1",
        imap_host="h",
        maildir_path=str(tmp_path / "mail"),
        store_id=default_store.id,
    )
    db_session.add(acc)
    db_session.commit()
    return acc


class TestAttachmentParse:
    def test_attachments_indexed_on_insert(self, db_session, default_store, tmp_path):
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "100.m1.host:2,S",
            _msg("<a1@x>", attachments=[("fattura-113.pdf", b"%PDF-fake")]),
        )

        index_service.upsert_message_set(db_session, acc.id)

        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        assert row.has_attachments is True
        assert row.attachments_indexed_at is not None
        att = db_session.query(MailIndexAttachment).filter_by(account_id=acc.id).one()
        assert att.filename == "fattura-113.pdf"
        assert att.ext == "pdf"
        assert att.size_bytes == len(b"%PDF-fake")
        assert att.content_type == "application/pdf"

    def test_message_without_attachments(self, db_session, default_store, tmp_path):
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(acc.maildir_path, "101.m1.host:2,S", _msg("<a2@x>"))

        index_service.upsert_message_set(db_session, acc.id)

        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        assert row.has_attachments is False
        assert row.attachments_indexed_at is not None
        assert db_session.query(MailIndexAttachment).count() == 0

    def test_existing_rows_not_reparsed(self, db_session, default_store, tmp_path):
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "102.m1.host:2,S",
            _msg("<a3@x>", attachments=[("doc.pdf", b"x")]),
        )
        index_service.upsert_message_set(db_session, acc.id)
        db_session.query(MailIndexAttachment).delete()
        db_session.commit()

        # Second walk: row exists, attachments must NOT be re-created
        index_service.upsert_message_set(db_session, acc.id)
        assert db_session.query(MailIndexAttachment).count() == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_index_attachments.py -v`
Expected: FAIL (`has_attachments is False` / no `MailIndexAttachment` rows — parsing not implemented).

- [ ] **Step 3: Implement**

In `index_service.py`: change the import line `from email.parser import BytesHeaderParser` to `from email.parser import BytesHeaderParser, BytesParser`, add `MailIndexAttachment` to the models import, and add after `_parse_headers`:

```python
def _parse_attachments(path: str) -> list[dict] | None:
    """Full MIME walk of one Maildir file. Returns attachment metadata rows.

    An attachment is a non-multipart leaf with a filename (Content-Disposition
    or Content-Type name param — policy.default decodes RFC 2047/2231).
    `part_index` numbers ALL non-multipart leaves in walk order, so a later
    re-walk can address the same part without ambiguity.
    Returns None on unreadable/unparsable files.
    """
    parser = BytesParser(policy=policy.default)
    try:
        with open(path, "rb") as f:
            msg = parser.parse(f)
    except (OSError, ValueError):
        return None
    out: list[dict] = []
    part_index = 0
    try:
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            part_index += 1
            filename = part.get_filename()
            if not filename:
                continue
            try:
                payload = part.get_payload(decode=True) or b""
            except Exception:  # malformed CTE — keep the row, size unknown
                payload = b""
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            out.append(
                {
                    "part_index": part_index,
                    "filename": filename,
                    "ext": ext,
                    "size_bytes": len(payload),
                    "content_type": part.get_content_type(),
                }
            )
    except Exception:
        logger.warning("Attachment parse failed for %s", path, exc_info=True)
        return None
    return out
```

In `upsert_message_set`, replace the insert branch (the `else:` adding `MailIndexMessage`) with:

```python
            else:
                atts = _parse_attachments(full_path)
                db.add(
                    MailIndexMessage(
                        account_id=account_id,
                        folder_path=folder,
                        maildir_filename=filename,
                        has_attachments=bool(atts),
                        attachments_indexed_at=now,
                        **parsed,
                    )
                )
                for a in atts or []:
                    db.add(
                        MailIndexAttachment(
                            account_id=account_id,
                            message_id_hash=parsed["message_id_hash"],
                            **a,
                        )
                    )
```

(Existing rows are untouched: Maildir message files are immutable, so attachments cannot change; pre-existing rows are covered by Task 3's backfill.)

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_index_attachments.py tests/ -n auto -q`
Expected: new tests PASS, full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/index_service.py tests/test_index_attachments.py
git commit -m "feat(index): parse attachment metadata on first index of each message"
```

---

### Task 3: Backfill CLI for pre-existing messages

**Files:**
- Modify: `src/mailfallback/services/index_service.py`
- Modify: `src/mailfallback/cli/index.py` (+ the argparse registration — locate with `grep -rn "backfill-snapshots" src/mailfallback/cli/` and mirror it)
- Test: extend `tests/test_index_attachments.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_index_attachments.py`:

```python
class TestBackfillAttachments:
    def test_backfill_fills_old_rows_and_resumes(self, db_session, default_store, tmp_path):
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "103.m1.host:2,S",
            _msg("<b1@x>", attachments=[("a.pdf", b"xx")]),
        )
        index_service.upsert_message_set(db_session, acc.id)
        # Simulate a pre-attachment-era row
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        row.attachments_indexed_at = None
        row.has_attachments = False
        db_session.query(MailIndexAttachment).delete()
        db_session.commit()

        n = index_service.backfill_attachments(db_session, acc.id)
        assert n == 1
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        assert row.has_attachments is True
        assert db_session.query(MailIndexAttachment).count() == 1

        # Resume: nothing left to do
        assert index_service.backfill_attachments(db_session, acc.id) == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_index_attachments.py::TestBackfillAttachments -v`
Expected: FAIL — `backfill_attachments` doesn't exist.

- [ ] **Step 3: Implement**

Append to `index_service.py`:

```python
def backfill_attachments(db: Session, account_id: str) -> int:
    """Parse attachments for alive rows that pre-date the attachment index.

    Resumable: only rows with attachments_indexed_at IS NULL are processed
    (the marker is set even when parsing fails, so one bad file cannot wedge
    the backfill — it just stays without attachment rows).
    Idempotent per message: delete-and-reinsert its attachment rows.
    Returns the number of messages processed.
    """
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise ValueError(f"Account {account_id} not found")

    pending = (
        db.query(MailIndexMessage)
        .filter(
            MailIndexMessage.account_id == account_id,
            MailIndexMessage.deleted_at.is_(None),
            MailIndexMessage.attachments_indexed_at.is_(None),
        )
        .all()
    )
    processed = 0
    now = datetime.now(UTC)
    for row in pending:
        base = (
            account.maildir_path
            if row.folder_path == "INBOX"
            else os.path.join(account.maildir_path, row.folder_path)
        )
        path = None
        for sub in ("cur", "new"):
            candidate = os.path.join(base, sub, row.maildir_filename)
            if os.path.exists(candidate):
                path = candidate
                break
        atts = _parse_attachments(path) if path else None
        db.query(MailIndexAttachment).filter(
            MailIndexAttachment.account_id == account_id,
            MailIndexAttachment.message_id_hash == row.message_id_hash,
        ).delete(synchronize_session=False)
        for a in atts or []:
            db.add(
                MailIndexAttachment(
                    account_id=account_id,
                    message_id_hash=row.message_id_hash,
                    **a,
                )
            )
        row.has_attachments = bool(atts)
        row.attachments_indexed_at = now
        processed += 1
        if processed % BATCH_SIZE == 0:
            db.commit()
    db.commit()
    return processed
```

In `cli/index.py`, add the dispatch branch and handler (mirror `_backfill_snapshots`):

```python
    if args.index_cmd == "backfill-attachments":
        return _backfill_attachments(args.account_id)
```

```python
def _backfill_attachments(account_id: str) -> int:
    from mailfallback.services import index_service

    db = SessionLocal()
    try:
        n = index_service.backfill_attachments(db, account_id)
        print(f"Backfilled attachments for {n} message(s).")
        return 0
    finally:
        db.close()
```

Register the subcommand in the CLI argparse setup exactly like `backfill-snapshots` (same file that defines `index_cmd` choices; it takes an `account_id` positional).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_index_attachments.py tests/ -n auto -q`
Expected: PASS, suite green.

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/index_service.py src/mailfallback/cli/
git add tests/test_index_attachments.py
git commit -m "feat(index): backfill-attachments CLI, resumable per-message"
```

---

### Task 4: Enrich search results with attachments

**Files:**
- Modify: `src/mailfallback/services/search_service.py:120-153`
- Test: extend the existing search-service test file (`ls tests/ | grep -i search` — add the test class there)

- [ ] **Step 1: Write the failing test** (adapt fixture helpers to that file's existing style — it already builds `MailIndexMessage` rows directly)

```python
def test_results_include_attachments(db_session, default_store):
    # ... create account + owner user via the file's existing helpers ...
    # add one indexed message with has_attachments=True
    # plus a MailIndexAttachment(filename="a.pdf", ext="pdf", size_bytes=10, part_index=2)
    out = search_service.search_messages(db, user=user, query="subject-term")
    hit = out["results"][0]
    assert hit["has_attachments"] is True
    assert hit["attachments"] == [{"filename": "a.pdf", "ext": "pdf", "size_bytes": 10}]
    assert hit["message_id_hash"]  # hex string, needed by the preview pane
```

(Write it as a real test using that file's existing account/user/index-row helpers — copy the setup of the nearest existing test in the same file. The helpers already exist; do not invent new fixture machinery.)

- [ ] **Step 2: Run to verify failure** — `uv run pytest <that file> -v -k attachments` → FAIL (KeyError).

- [ ] **Step 3: Implement**

In `search_messages`, alongside the existing `snap_by_msg` block (`if rows:` at line ~120), add:

```python
        att_rows = (
            db.query(MailIndexAttachment)
            .filter(
                MailIndexAttachment.account_id.in_(scope),
                MailIndexAttachment.message_id_hash.in_(hashes),
            )
            .order_by(MailIndexAttachment.part_index)
            .all()
        )
        atts_by_msg: dict[tuple[str, bytes], list[dict]] = {}
        for a in att_rows:
            atts_by_msg.setdefault((a.account_id, a.message_id_hash), []).append(
                {"filename": a.filename, "ext": a.ext, "size_bytes": a.size_bytes}
            )
```

(initialize `atts_by_msg = {}` in the `else:` branch too), import `MailIndexAttachment` from models, and extend each result dict:

```python
                "message_id_hash": r.message_id_hash.hex(),
                "has_attachments": r.has_attachments,
                "attachments": atts_by_msg.get((r.account_id, r.message_id_hash), []),
```

- [ ] **Step 4: Run** — that test file + full suite (`-n auto`) → PASS.

- [ ] **Step 5: Commit** — `git add src/mailfallback/services/search_service.py tests/ && git commit -m "feat(search): attachment metadata and hash in search results"`

---

### Task 5: `restic_service.dump_file`

**Files:**
- Modify: `src/mailfallback/services/restic_service.py` (after `list_files`, ~line 273)
- Test: extend the restic service test file (`ls tests/ | grep -i restic`)

- [ ] **Step 1: Write the failing test** (in the restic test file, following its `subprocess.run` mock pattern — check how existing `list_files` tests mock):

```python
def test_dump_file_returns_bytes(monkeypatch, ...):
    # mock subprocess.run to return returncode=0, stdout=b"RAW BYTES"
    out = restic_service.dump_file(dest, "acct-id", "ab12", "/data/m/acct/cur/x:2,S")
    assert out == b"RAW BYTES"

def test_dump_file_failure_returns_none(...):
    # returncode=1 -> None
```

- [ ] **Step 2: Run to verify failure** — AttributeError: no `dump_file`.

- [ ] **Step 3: Implement**

```python
def dump_file(
    destination: Repository,
    account_id: str,
    snapshot_id: str,
    path: str,
    max_bytes: int = 26_214_400,
) -> bytes | None:
    """Extract one file's raw bytes from a snapshot via `restic dump`.

    Binary subprocess call (NOT _run_restic, which is text-mode). Output is
    truncated to max_bytes — callers preview/parse, they don't archive.
    Returns None on any restic failure.
    """
    env = build_env(destination, account_id)
    cmd = ["restic"]
    if _is_insecure(destination):
        cmd.append("--insecure-tls")
    cmd.extend(["dump", snapshot_id, path])
    full_env = {**os.environ, **env}
    result = subprocess.run(cmd, capture_output=True, env=full_env)
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout[:max_bytes]
```

- [ ] **Step 4: Run** — restic tests + suite → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(restic): dump_file for single-file snapshot extraction"`

---

### Task 6: Preview service + endpoint

**Files:**
- Create: `src/mailfallback/services/preview_service.py`
- Modify: `src/mailfallback/routers/restore.py` (new GET route near `api_restore_search`, line ~804)
- Create: `tests/test_preview_service.py`, `tests/test_restore_preview_api.py`

- [ ] **Step 1: Write the failing service tests**

`tests/test_preview_service.py` (reuse `_write_maildir_message`/`_msg`/`_mk_account` — import them from `tests.test_index_attachments` or copy; copying is fine, they're 30 lines):

```python
from mailfallback.services import index_service, preview_service


class TestPreviewLive:
    def test_live_preview_returns_body_and_attachments(
        self, db_session, default_store, tmp_path
    ):
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "200.m1.host:2,S",
            _msg("<p1@x>", subject="Fattura marzo", attachments=[("f.pdf", b"%PDF")]),
        )
        index_service.upsert_message_set(db_session, acc.id)
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()

        out = preview_service.get_preview(db_session, acc, row.message_id_hash)

        assert out["subject"] == "Fattura marzo"
        assert out["source"] == "live"
        assert "body text" in out["body_snippet"]
        assert out["attachments"][0]["filename"] == "f.pdf"

    def test_missing_message_returns_none(self, db_session, default_store, tmp_path):
        acc = _mk_account(db_session, default_store, tmp_path)
        assert preview_service.get_preview(db_session, acc, b"\x00" * 20) is None


class TestPreviewSnapshot:
    def test_snapshot_preview_uses_restic_dump(
        self, db_session, default_store, tmp_path, monkeypatch
    ):
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path, "201.m1.host:2,S", _msg("<p2@x>", subject="Old mail")
        )
        index_service.upsert_message_set(db_session, acc.id)
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        raw = open(
            os.path.join(acc.maildir_path, "cur", "201.m1.host:2,S"), "rb"
        ).read()
        # Make it snapshot-only
        row.deleted_at = datetime.now(UTC)
        db_session.add(SnapshotMessage(
            snapshot_id="ab12", account_id=acc.id, message_id_hash=row.message_id_hash
        ))
        # Minimal BackupPolicy via existing helper in tests/test_ui_backup_admin.py
        # style: create Repository row directly + BackupPolicy(account_id, destination_id)
        ...
        db_session.commit()
        monkeypatch.setattr(
            preview_service.restic_service, "dump_file", lambda *a, **k: raw
        )
        monkeypatch.setattr(
            preview_service.restic_service,
            "list_snapshots",
            lambda *a, **k: [{"short_id": "ab12", "time": "2026-06-01T00:00:00Z"}],
        )

        out = preview_service.get_preview(db_session, acc, row.message_id_hash)

        assert out["source"] == "snapshot:ab12"
        assert out["subject"] == "Old mail"
```

(Fill the `...` with the same Repository/BackupPolicy construction used by `tests/test_ui_backup_admin.py::_mk_repo` — model rows added directly to `db_session`, no HTTP. This is a concrete instruction to copy that exact pattern, not an open design question.)

- [ ] **Step 2: Run to verify failure** — module doesn't exist.

- [ ] **Step 3: Implement `preview_service.py`**

```python
"""Message preview — headers + body snippet, from live Maildir or snapshot.

No IMAP session: live files are read straight from disk via the index
locator (folder_path + maildir_filename); snapshot-only messages come out
of restic via dump_file. Snippets are capped — this is a peek, not a reader.
"""

import logging
import os
import re
from email import policy
from email.parser import BytesParser

from sqlalchemy.orm import Session

from mailfallback.models import (
    Account,
    BackupPolicy,
    MailIndexAttachment,
    MailIndexMessage,
    SnapshotMessage,
)
from mailfallback.services import restic_service

logger = logging.getLogger(__name__)

SNIPPET_CHARS = 2048
_TAG_RE = re.compile(r"<[^>]+>")


def _locate_live_file(account: Account, row: MailIndexMessage) -> str | None:
    base = (
        account.maildir_path
        if row.folder_path == "INBOX"
        else os.path.join(account.maildir_path, row.folder_path)
    )
    for sub in ("cur", "new"):
        candidate = os.path.join(base, sub, row.maildir_filename)
        if os.path.exists(candidate):
            return candidate
    # Flags may have changed the suffix since the last index walk — match on
    # the stable prefix instead.
    prefix = row.maildir_filename.split(":2,")[0]
    for sub in ("cur", "new"):
        d = os.path.join(base, sub)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.split(":2,")[0] == prefix:
                return os.path.join(d, fn)
    return None


def _snapshot_bytes(db: Session, account: Account, row: MailIndexMessage) -> tuple[bytes, str] | None:
    policy_row = (
        db.query(BackupPolicy).filter(BackupPolicy.account_id == account.id).first()
    )
    if not policy_row:
        return None
    snap_ids = {
        sid
        for (sid,) in db.query(SnapshotMessage.snapshot_id).filter(
            SnapshotMessage.account_id == account.id,
            SnapshotMessage.message_id_hash == row.message_id_hash,
        )
    }
    if not snap_ids:
        return None
    snaps = restic_service.list_snapshots(policy_row.destination, account.id)
    # newest first; list_snapshots returns dicts with "time" and "short_id"
    for s in sorted(snaps, key=lambda s: s.get("time", ""), reverse=True):
        sid = s.get("short_id") or s.get("id", "")[:8]
        if sid not in snap_ids:
            continue
        base = (
            account.maildir_path
            if row.folder_path == "INBOX"
            else os.path.join(account.maildir_path, row.folder_path)
        )
        for sub in ("cur", "new"):
            raw = restic_service.dump_file(
                policy_row.destination,
                account.id,
                sid,
                os.path.join(base, sub, row.maildir_filename),
            )
            if raw:
                return raw, sid
    return None


def _body_snippet(msg) -> str:
    text_part = msg.get_body(preferencelist=("plain", "html"))
    if text_part is None:
        return ""
    try:
        content = text_part.get_content()
    except Exception:
        return ""
    if text_part.get_content_type() == "text/html":
        content = _TAG_RE.sub(" ", content)
    return " ".join(content.split())[:SNIPPET_CHARS]


def get_preview(db: Session, account: Account, message_id_hash: bytes) -> dict | None:
    row = (
        db.query(MailIndexMessage)
        .filter(
            MailIndexMessage.account_id == account.id,
            MailIndexMessage.message_id_hash == message_id_hash,
        )
        .first()
    )
    if not row:
        return None

    raw: bytes | None = None
    source = "live"
    if row.deleted_at is None:
        path = _locate_live_file(account, row)
        if path:
            try:
                with open(path, "rb") as f:
                    raw = f.read()
            except OSError:
                raw = None
    if raw is None:
        found = _snapshot_bytes(db, account, row)
        if found:
            raw, sid = found
            source = f"snapshot:{sid}"
    if raw is None:
        return None

    msg = BytesParser(policy=policy.default).parse(__import__("io").BytesIO(raw))
    atts = (
        db.query(MailIndexAttachment)
        .filter(
            MailIndexAttachment.account_id == account.id,
            MailIndexAttachment.message_id_hash == message_id_hash,
        )
        .order_by(MailIndexAttachment.part_index)
        .all()
    )
    return {
        "subject": row.subject,
        "from_addr": row.from_addr,
        "from_name": row.from_name,
        "to_addrs": row.to_addrs or [],
        "date_sent": row.date_sent.isoformat() if row.date_sent else None,
        "folder_path": row.folder_path,
        "alive_in_live": row.deleted_at is None,
        "source": source,
        "body_snippet": _body_snippet(msg),
        "attachments": [
            {"filename": a.filename, "ext": a.ext, "size_bytes": a.size_bytes}
            for a in atts
        ],
    }
```

(Replace the `__import__("io")` inline with a proper `import io` at the top — written here inline only to keep the snippet single-block; the real file uses the normal import.)

- [ ] **Step 4: Add the API route** in `routers/restore.py` (after `api_restore_search`):

```python
@router.get("/preview/{account_id}/{message_id_hash_hex}")
def api_restore_preview(
    account_id: str,
    message_id_hash_hex: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = account_service.get_account(db, account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        message_id_hash = bytes.fromhex(message_id_hash_hex)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid hash") from None
    from mailfallback.services import preview_service

    out = preview_service.get_preview(db, account, message_id_hash)
    if out is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return out
```

- [ ] **Step 5: Write the failing API tests** — `tests/test_restore_preview_api.py`: login as a non-owner user → 404; owner → 200 with subject (build the live-file fixture exactly as in `tests/test_preview_service.py`; login pattern: `client.post("/api/auth/login", json={...})` as in `tests/test_ui_backup_admin.py`; ownership via `account_owners` insert or the account_service helper used by other router tests — copy from the existing restore router test file, find it with `ls tests/ | grep -i restore`).

- [ ] **Step 6: Run everything** — `uv run pytest tests/test_preview_service.py tests/test_restore_preview_api.py tests/ -n auto -q` → PASS.

- [ ] **Step 7: Commit** — `git add src/mailfallback/services/preview_service.py src/mailfallback/routers/restore.py tests/ && git commit -m "feat(restore): in-app message preview from live maildir or snapshot"`

---

### Task 7: UID resolver endpoint (restore-to-origin backend)

**Files:**
- Modify: `src/mailfallback/routers/restore.py`
- Test: extend `tests/test_restore_preview_api.py` (same fixtures) or the existing restore router test file

- [ ] **Step 1: Failing test** — POST `/api/restore/resolve-uids` with `{account_id, message_ids: ["<p1@x>"]}` as owner: mock `_connect_dovecot_for_account` (patch at `mailfallback.routers.restore._connect_dovecot_for_account`) to return a fake imaplib-like object whose `select` returns `("OK", ...)` and `uid("SEARCH", ...)` returns `("OK", [b"7"])`; also patch `delete_temp_imap_user`. Expect `{"resolved": {"INBOX": ["7"]}, "missing": []}`. Non-owner → 404.

- [ ] **Step 2: Implement** (after `api_restore_preview`):

```python
class ResolveUidsRequest(BaseModel):
    account_id: str
    message_ids: list[str]


@router.post("/resolve-uids")
def api_resolve_uids(
    req: ResolveUidsRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Message-Id -> live IMAP UID, grouped by folder. Used by the workspace's
    restore-to-origin flow (mirrors the legacy wrapper's resolution, but
    folder-grouped server-side instead of per-location)."""
    account = account_service.get_account(db, req.account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    rows = (
        db.query(MailIndexMessage)
        .filter(
            MailIndexMessage.account_id == account.id,
            MailIndexMessage.message_id.in_(req.message_ids[:200]),
            MailIndexMessage.deleted_at.is_(None),
        )
        .all()
    )
    by_folder: dict[str, list[str]] = {}
    for r in rows:
        by_folder.setdefault(r.folder_path, []).append(r.message_id)

    resolved: dict[str, list[str]] = {}
    found_msgids: set[str] = set()
    if by_folder:
        conn, temp_user = _connect_dovecot_for_account(db, account)
        try:
            ns = account_namespace_prefix(account)
            for folder, msgids in by_folder.items():
                target = f'"{ns}{_sanitize_imap_string(folder)}"'
                typ, _ = conn.select(target, readonly=True)
                if typ != "OK":
                    continue
                for msgid in msgids:
                    quoted = _sanitize_imap_string(msgid)
                    typ, data = conn.uid("SEARCH", "HEADER", "Message-Id", f'"{quoted}"')
                    if typ == "OK" and data and data[0]:
                        uids = data[0].decode().split()
                        if uids:
                            resolved.setdefault(folder, []).append(uids[0])
                            found_msgids.add(msgid)
        finally:
            with contextlib.suppress(Exception):
                conn.logout()
            with contextlib.suppress(Exception):
                delete_temp_imap_user(db, temp_user)

    missing = [m for m in req.message_ids if m not in found_msgids]
    return {"resolved": resolved, "missing": missing}
```

(`MailIndexMessage` import: check the file's models import; `contextlib`, `_sanitize_imap_string`, `account_namespace_prefix`, `_connect_dovecot_for_account`, `delete_temp_imap_user` are already used in this module.)

- [ ] **Step 3: Run** — tests PASS, suite green.
- [ ] **Step 4: Commit** — `git commit -m "feat(restore): resolve-uids endpoint for restore-to-origin"`

---

### Task 8: Workspace UI — cross-account scope, badges, chips, preview pane, restore-to-origin

**Files:**
- Modify: `src/mailfallback/templates/restore_workspace.html`
- Modify: `src/mailfallback/static/js/restore_workspace.js`
- Modify: `src/mailfallback/static/css/style.css`
- Modify: `src/mailfallback/routers/ui_restore.py:53-118` (context: add `accounts_json`)

This task is UI-heavy; the contract is `.claude/mockup_restore_staging_reference.png` **minus the staging bar and "Add to staging" button** (Plan 2). Key changes:

- [ ] **Step 1: Route context** — in `ui_restore.restore_page`, add to the context:

```python
            "accounts_json": json.dumps(
                [{"id": a.id, "name": a.name, "email": a.email_address or ""} for a in all_accounts]
            ),
```

(`import json` at top if missing.)

- [ ] **Step 2: Template — scope select replaces the Mailbox select for the single-mail preset only**

In `restore_workspace.html` sidebar, change the Mailbox field to show on non-single presets, and add the scope select into the search row. Concretely:

1. Wrap the existing `Mailbox` `<label class="ws-field">` with `x-show="preset !== 'single-mail'"` (folder/full still need a single source).
2. Same for the `Destination` field: `x-show="preset !== 'single-mail'"` (single-mail restores to origin from this plan on).
3. In the `ws-search-row` form, before the query input:

```html
            <select x-model="scopeAccountId" class="ws-scope">
              <option value="">All mailboxes ({{ all_accounts | length }})</option>
              {% for acct in all_accounts %}
              <option value="{{ acct.id }}">{{ acct.name }} ({{ acct.email_address or "—" }})</option>
              {% endfor %}
            </select>
```

4. Result row: inside `.ws-result-body`, after the subject div add badges + chips:

```html
                    <div class="ws-result-subject">
                      <span x-text="r.subject || '(no subject)'"></span>
                      <span class="ws-badge ws-badge-acct" x-text="accountName(r.account_id)"></span>
                      <i data-lucide="paperclip" class="icon-sm ws-att-mark" x-show="r.has_attachments"></i>
                    </div>
                    <div class="ws-result-meta text-xsmall text-muted">
                      <span x-text="(r.from_addr || '?') + ' · ' + (r.folder_path || '') + (r.date_sent ? ' · ' + r.date_sent.slice(0,10) : '')"></span>
                      <span class="ws-badge ws-badge-live" x-show="r.alive_in_live">live</span>
                      <span class="ws-badge ws-badge-snap" x-show="r.snapshots.length" x-text="'snap ×' + r.snapshots.length"></span>
                    </div>
                    <div class="ws-atts" x-show="r.attachments && r.attachments.length">
                      <template x-for="a in r.attachments" :key="a.filename + a.size_bytes">
                        <span class="ws-att-chip">
                          <i data-lucide="paperclip" class="icon-sm icon-inline"></i>
                          <span x-text="a.filename"></span>
                          <span class="text-muted" x-text="' · ' + fmtSize(a.size_bytes)"></span>
                        </span>
                      </template>
                    </div>
```

   Replace the old `ws-badges`/`sources` template block (sources are now the live/snap badges above). Add `@click="openPreview(r)"` on the `.ws-result` label (the checkbox keeps `@click.stop`).

5. Wrap the results column and a new preview panel in a two-column grid (matching the mock): results `<div class="ws-results">` and:

```html
          <div class="ws-preview" x-show="preview" x-transition>
            <h4 class="ws-field-label">Preview</h4>
            <template x-if="previewLoading"><p class="text-muted text-small">Loading…</p></template>
            <template x-if="preview && !previewLoading">
              <div>
                <div class="ws-preview-subject" x-text="preview.subject || '(no subject)'"></div>
                <p class="ws-preview-kv">
                  <strong>Da</strong> <span x-text="preview.from_addr || '?'"></span>
                  <strong>A</strong> <span x-text="(preview.to_addrs || []).join(', ')"></span><br>
                  <strong>Data</strong> <span x-text="preview.date_sent ? preview.date_sent.slice(0,16).replace('T',' ') : '—'"></span>
                  <strong>Cartella</strong> <span x-text="preview.folder_path"></span>
                  <strong>Fonte</strong> <span x-text="preview.source"></span>
                </p>
                <div class="ws-preview-body" x-text="preview.body_snippet || '(empty)'"></div>
                <div class="ws-preview-atts" x-show="preview.attachments.length">
                  <span class="ws-field-label">Allegati</span>
                  <template x-for="a in preview.attachments" :key="a.filename">
                    <span class="ws-att-chip"><span x-text="a.filename"></span><span class="text-muted" x-text="' · ' + fmtSize(a.size_bytes)"></span></span>
                  </template>
                </div>
                <a class="ws-btn-ghost" :href="webmailUrl" target="_blank" x-show="preview.alive_in_live && webmailUrl">Open full in webmail</a>
              </div>
            </template>
          </div>
```

   `webmailUrl` comes from the existing `webmail_url` template global: `webmailUrl: {{ webmail_url | tojson }},` in the component init data (see Step 3).

- [ ] **Step 3: JS — `restore_workspace.js`**

1. Component state: add `scopeAccountId: ''`, `preview: null`, `previewLoading: false`, `accounts: JSON.parse(document.getElementById('ws-accounts-data').textContent)` — and in the template add `<script type="application/json" id="ws-accounts-data">{{ accounts_json | safe }}</script>` (data island, not executable — complies with the no-inline-JS rule).
2. Helpers:

```javascript
    accountName(id) {
      const a = this.accounts.find(x => x.id === id);
      return a ? a.name : '?';
    },
    fmtSize(n) {
      if (n == null) return '?';
      if (n < 1024) return n + ' B';
      if (n < 1048576) return (n / 1024).toFixed(0) + ' KB';
      return (n / 1048576).toFixed(1) + ' MB';
    },
    async openPreview(r) {
      this.previewLoading = true;
      this.preview = {};
      try {
        const resp = await fetch(`/api/restore/preview/${r.account_id}/${r.message_id_hash}`);
        this.preview = resp.ok ? await resp.json() : null;
        if (!resp.ok) this.statusText = `Preview failed: ${resp.status}`;
      } catch (e) {
        this.preview = null;
      } finally {
        this.previewLoading = false;
        this.refreshIcons();
      }
    },
```

3. `runSearch()` — switch to the new endpoint:

```javascript
        const resp = await fetch('/api/restore/search', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            query: this.query,
            account_ids: this.scopeAccountId ? [this.scopeAccountId] : null,
            range_start: this.rangeStartIso,
            range_end: this.rangeEndIso,
            include_deleted: this.includeSnapshots,
            deep: this.deepSearch,
            page: 1,
            page_size: 100,
          }),
        });
```

   Result handling: `this.results = body.results`; status line `` `${body.total} risultati · pagina 1` ``; partial banner unchanged. Selection key becomes composite: `selected` stores `r.account_id + ':' + r.message_id`; update `toggleSelectAll` and the checkbox `:value` accordingly.
4. `restoreSelected()` — restore to origin, grouped per account:

```javascript
    async restoreSelected() {
      if (this.selected.length === 0) return;
      this.restoring = true;
      try {
        const byKey = Object.fromEntries(this.results.map(r => [r.account_id + ':' + r.message_id, r]));
        const byAccount = {};
        for (const key of this.selected) {
          const r = byKey[key];
          if (r) (byAccount[r.account_id] ||= []).push(r.message_id);
        }
        const jobs = [];
        for (const [accountId, messageIds] of Object.entries(byAccount)) {
          const res = await fetch('/api/restore/resolve-uids', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({account_id: accountId, message_ids: messageIds}),
          });
          if (!res.ok) { this.statusText = `Resolve failed: ${res.status}`; return; }
          const {resolved, missing} = await res.json();
          if (Object.keys(resolved).length === 0) continue;
          const r = await fetch('/api/restore', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              source_account_id: accountId,
              target_account_id: accountId,
              restore_mode: 'selection',
              selected_uids: resolved,
            }),
          });
          if (r.ok) jobs.push((await r.json()).job_id);
          else { this.statusText = `Restore failed for ${this.accountName(accountId)}: ${r.status}`; return; }
          if (missing.length) this.statusText = `${missing.length} message(s) not in live mail — skipped (snapshot-only restore arrives with the staging area)`;
        }
        if (jobs.length) this.statusText = `Started ${jobs.length} restore job(s): ${jobs.join(', ')}`;
        this.selected = [];
      } finally {
        this.restoring = false;
        this.refreshIcons();
      }
    },
```

   (Check the exact legacy `selected_uids` shape consumed by `/api/restore` selection mode before finishing: the old JS posted a `{folderKey: [uids]}` object — `resolved` matches it, but folderKey there included the namespace prefix. Verify against `create_restore_job`'s handling of `selected_uids` in `services/restore_service.py` and prepend `account_namespace_prefix` server-side in resolve-uids if the worker expects prefixed folders. This is the one integration seam to confirm by reading the code, not guessing.)
5. Remove now-dead pieces: `search_subject/from/to` checkboxes still drive nothing in the new endpoint — keep the Filters UI but mark "Search in" group as applying to the indexed headers note, or drop the three checkboxes entirely (mock has no them; drop, and delete `filters.subject/from/to` state). `type_filter` has no equivalent in the new API — drop the Type select too (mock has neither). Update the template accordingly.

- [ ] **Step 4: CSS** — add to `style.css` in the workspace section (after `.ws-result` rules):

```css
/* === Workspace: cross-account results + preview pane === */
.ws-results-grid { display: grid; grid-template-columns: 1.1fr 1fr; gap: 1rem; align-items: start; }
.ws-badge-acct { background: var(--mfb-badge-syncing-bg); color: var(--mfb-badge-syncing-color); border: 1px solid var(--mfb-badge-syncing-border); }
.ws-att-mark { color: var(--ws-text-muted); }
.ws-atts { display: flex; gap: 0.3rem; flex-wrap: wrap; margin-top: 0.25rem; }
.ws-att-chip { background: var(--ws-input-bg); border: 1px solid var(--ws-border); border-radius: 6px; padding: 0 0.5rem; font-size: 0.72rem; color: var(--ws-text-muted); white-space: nowrap; }
.ws-preview { background: var(--ws-card); border: 1px solid var(--ws-border); border-radius: 10px; padding: 0.6rem 1rem 1rem; position: sticky; top: 1rem; }
.ws-preview-subject { font-weight: 650; margin-bottom: 0.3rem; }
.ws-preview-kv { font-size: 0.8rem; color: var(--ws-text-muted); line-height: 1.5; }
.ws-preview-kv strong { color: var(--ws-text); font-weight: 600; }
.ws-preview-body { font-size: 0.85rem; line-height: 1.55; max-height: 16rem; overflow: hidden; position: relative; }
.ws-preview-body::after { content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 2.2rem; background: linear-gradient(transparent, var(--ws-card)); }
.ws-preview-atts { border: 1px dashed var(--ws-border); border-radius: 8px; padding: 0.5rem 0.7rem; margin-top: 0.7rem; display: flex; gap: 0.3rem; flex-wrap: wrap; }
.ws-scope { max-width: 230px; }
@media (max-width: 768px) {
  .ws-results-grid { grid-template-columns: 1fr; }
  .ws-preview { position: static; }
}
```

Wrap results+preview in `<div class="ws-results-grid">` in the template.

- [ ] **Step 5: Run the full suite + lint** — `uv run pytest tests/ -n auto -q && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/` → green. (UI smoke-level template tests: if the existing test suite asserts on restore page markup — `grep -rn "ws-search" tests/` — update those assertions.)

- [ ] **Step 6: Commit** — `git add -A src/mailfallback/templates src/mailfallback/static src/mailfallback/routers/ui_restore.py && git commit -m "feat(ui): cross-account search, attachment chips, preview pane in restore workspace"`

---

### Task 9: Live verification against the mockup + critic

- [ ] **Step 1:** `docker compose up -d --build mailfallback`; wait for `/healthz` 200.
- [ ] **Step 2:** Log in via temp admin (create with `create_user` in a container one-off as done previously; delete afterwards), open `/restore` in the MCP Chrome at 1440×900, force-reload ignoring cache (baked assets — known gotcha).
- [ ] **Step 3:** Run a real search ("fattura" or any term with hits in koma-link/Live), open a preview, screenshot dark+light at 1440 and 420 → `.claude/plan1_restore_*.png`.
- [ ] **Step 4:** Side-by-side compare with `.claude/mockup_restore_staging_reference.png` (mockup-is-contract): scope select position, badge styling, chips, preview layout. Staging bar and "Add to staging" are EXPECTED to be absent (Plan 2). Fix deviations before proceeding.
- [ ] **Step 5:** Gemini critic on the final screenshots (`gemini -m gemini-2.5-pro -p "@…"`), apply only findings consistent with the app's design system.
- [ ] **Step 6:** Run `uv run pytest tests/ -n auto -q` one final time; verify a real end-to-end restore-to-origin of one message from search → job completes (watch `/api/restore/{job_id}` or the UI status strip).
- [ ] **Step 7:** Final commit of any verification fixes; report completion with screenshots.

---

## Self-review notes (run before handoff)

- Spec coverage for Plan 1 scope: phase 1 (Tasks 1–4) ✓, phase 2 (Tasks 5–8) ✓, verification (Task 9) ✓. Staging/push/attachment-view/Tika are Plans 2–3 by design.
- Type consistency: `message_id_hash` is bytes in DB, hex string in API results (Task 4) and URL paths (Task 6) — JS treats it as opaque string. `selected_uids` shape `{folder: [uid]}` — confirm prefix expectation in Task 8 Step 3.4 note.
- The one open integration seam is flagged inline (namespace prefix in resolve-uids vs restore worker expectations) with the exact file to read.
