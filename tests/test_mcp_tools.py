"""The MCP tools: scope gating, mailbox scoping, and shapes."""

import os
from email.message import EmailMessage

import anyio
import pytest
from sqlalchemy.orm import sessionmaker

import mailfallback.config as cfg
from mailfallback.models import Account, MailIndexMessage, UserRole, account_owners
from mailfallback.services import index_service
from mailfallback.services.user_service import create_user


@pytest.fixture
def server(db_session, monkeypatch):
    """A built MCP server with the tools registered."""
    import mailfallback.mcp_server as ms

    monkeypatch.setattr(cfg.settings, "mcp_enabled", True, raising=False)
    monkeypatch.setattr(cfg.settings, "mcp_public_url", "http://127.0.0.1:8000", raising=False)
    monkeypatch.setattr(ms, "_server", None)  # force a rebuild per test
    # A factory on the test engine, not the shared session: _caller closes what
    # it opens, so handing over the fixture's session would close it underneath
    # the test's own later assertions. StaticPool means both sessions share the
    # one connection, so writes stay visible.
    monkeypatch.setattr(ms, "SessionLocal", sessionmaker(bind=db_session.bind))
    return ms.get_server(cfg.settings)


@pytest.fixture
def tool_user(db_session, default_store):
    return create_user(
        db_session, "tooluser", "toolpass123456", UserRole.user, store_id=default_store.id
    )


def _as(monkeypatch, scopes, user):
    """Pretend the SDK authenticated this caller for the current tool call."""
    from mcp.server.auth.provider import AccessToken

    import mailfallback.mcp_server as ms

    monkeypatch.setattr(
        ms,
        "get_access_token",
        lambda: AccessToken(token="t", client_id=user.username, scopes=scopes, subject=user.id),
    )


def _call(server, name, **kwargs):
    """Call a tool and return its JSON payload.

    ``call_tool`` returns a ``CallToolResult``, not the bare value. Every
    tool here returns `dict[str, Any]`, which the installed SDK (mcp 2.0.0 /
    mcp_types 2.0.0) turns into a RootModel and hands back through
    `structured_content` verbatim — no `{"result": ...}` wrapping. That
    wrapping only happens for a return annotation the SDK treats as a
    "generic type" (e.g. a bare `list[...]`), which is why every tool here
    is declared to return an object rather than an array.
    """
    result = anyio.run(lambda: server.call_tool(name, kwargs))
    return result.structured_content


def _indexed_account(db_session, store, tmp_path, owner, name="acc", subject="quarterly invoice"):
    acc = Account(
        name=name,
        imap_host="h",
        email_address=f"{name}@example.com",
        maildir_path=str(tmp_path / f"mail-{name}"),
        store_id=store.id,
    )
    db_session.add(acc)
    db_session.flush()
    db_session.execute(account_owners.insert().values(account_id=acc.id, user_id=owner.id))
    db_session.commit()
    msg = EmailMessage()
    msg["Message-Id"] = f"<{name}@x>"
    msg["From"] = "sender@example.com"
    msg["To"] = "dest@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Thu, 11 Jun 2026 10:00:00 +0200"
    msg.set_content("body text")
    cur = os.path.join(acc.maildir_path, "cur")
    os.makedirs(cur, exist_ok=True)
    with open(os.path.join(cur, f"100.{name}.host:2,S"), "wb") as f:
        f.write(msg.as_bytes())
    index_service.upsert_message_set(db_session, acc.id)
    row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
    return acc, row


class TestScopeGating:
    def test_an_imap_only_token_is_refused_by_every_read_tool(
        self, server, db_session, tool_user, monkeypatch
    ):
        """The IMAP skills' token authenticates but must do nothing here."""
        _as(monkeypatch, ["imap"], tool_user)

        for name, kwargs in [
            ("list_mailboxes", {}),
            ("search_mail", {"query": "x"}),
            ("search_attachments", {"query": "x"}),
            ("get_message", {"account_id": "a", "message_id_hash": "00"}),
        ]:
            with pytest.raises(Exception) as exc:
                _call(server, name, **kwargs)
            assert "mail:read" in str(exc.value), name

    def test_a_mail_read_token_is_allowed(self, server, db_session, tool_user, monkeypatch):
        _as(monkeypatch, ["mail:read"], tool_user)
        assert _call(server, "list_mailboxes") is not None


class TestListMailboxes:
    def test_lists_only_the_callers_mailboxes(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        _indexed_account(db_session, default_store, tmp_path, tool_user, name="mine")
        other = create_user(
            db_session, "other", "otherpass12345", UserRole.user, store_id=default_store.id
        )
        _indexed_account(db_session, default_store, tmp_path, other, name="theirs")
        _as(monkeypatch, ["mail:read"], tool_user)

        out = _call(server, "list_mailboxes")

        mailboxes = out["mailboxes"]
        names = [m["name"] for m in mailboxes]
        assert names == ["mine"]
        assert mailboxes[0]["indexed_messages"] == 1
        assert mailboxes[0]["folders"] == ["INBOX"]


class TestSearchMail:
    def test_finds_the_callers_mail(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        _indexed_account(db_session, default_store, tmp_path, tool_user)
        _as(monkeypatch, ["mail:read"], tool_user)

        out = _call(server, "search_mail", query="invoice")

        assert out["total"] == 1
        hit = out["results"][0]
        assert hit["subject"] == "quarterly invoice"
        # the pair that makes an attachment addressable downstream
        assert len(hit["message_id_hash"]) == 40
        assert "attachments" in hit

    def test_does_not_reach_another_users_mail(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        other = create_user(
            db_session, "other2", "otherpass12345", UserRole.user, store_id=default_store.id
        )
        _indexed_account(db_session, default_store, tmp_path, other, name="theirs2")
        _as(monkeypatch, ["mail:read"], tool_user)

        assert _call(server, "search_mail", query="")["total"] == 0

    def test_an_admins_token_still_sees_only_their_own(
        self, server, db_session, default_store, tmp_path, monkeypatch
    ):
        """Role does not travel with a token here either."""
        admin = create_user(
            db_session, "boss", "bosspass123456", UserRole.admin, store_id=default_store.id
        )
        victim = create_user(
            db_session, "victim", "victimpass12345", UserRole.user, store_id=default_store.id
        )
        _indexed_account(db_session, default_store, tmp_path, victim, name="notmine")
        _as(monkeypatch, ["mail:read"], admin)

        assert _call(server, "search_mail", query="")["total"] == 0
        assert _call(server, "list_mailboxes")["mailboxes"] == []


class TestGetMessage:
    def test_returns_the_message(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        acc, row = _indexed_account(db_session, default_store, tmp_path, tool_user)
        _as(monkeypatch, ["mail:read"], tool_user)

        out = _call(
            server, "get_message", account_id=acc.id, message_id_hash=row.message_id_hash.hex()
        )

        assert out["subject"] == "quarterly invoice"
        assert out["source"] == "live"
        assert "body_snippet" in out

    def test_another_users_message_is_refused_without_revealing_it_exists(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        other = create_user(
            db_session, "other3", "otherpass12345", UserRole.user, store_id=default_store.id
        )
        acc, row = _indexed_account(db_session, default_store, tmp_path, other, name="theirs3")
        _as(monkeypatch, ["mail:read"], tool_user)

        with pytest.raises(Exception) as exc:
            _call(
                server, "get_message", account_id=acc.id, message_id_hash=row.message_id_hash.hex()
            )
        assert "No such mailbox" in str(exc.value)

    def test_a_malformed_hash_is_refused(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        acc, _ = _indexed_account(db_session, default_store, tmp_path, tool_user)
        _as(monkeypatch, ["mail:read"], tool_user)

        with pytest.raises(Exception) as exc:
            _call(server, "get_message", account_id=acc.id, message_id_hash="nothex")
        assert "message_id_hash" in str(exc.value)


class TestToolAnnotations:
    def test_every_read_tool_is_annotated_read_only(self, server):
        """A client should be able to tell which tools change nothing."""
        tools = anyio.run(server.list_tools)
        by_name = {t.name: t for t in tools}
        for name in ("list_mailboxes", "search_mail", "search_attachments", "get_message"):
            assert by_name[name].annotations.read_only_hint is True, name


class TestDownloadAttachment:
    def _account_with_attachment(self, db_session, store, tmp_path, owner):
        acc = Account(
            name="withatt",
            imap_host="h",
            email_address="withatt@example.com",
            maildir_path=str(tmp_path / "mail-mcpatt"),
            store_id=store.id,
        )
        db_session.add(acc)
        db_session.flush()
        db_session.execute(account_owners.insert().values(account_id=acc.id, user_id=owner.id))
        db_session.commit()
        msg = EmailMessage()
        msg["Message-Id"] = "<mcpatt@x>"
        msg["From"] = "a@b.c"
        msg["To"] = "d@e.f"
        msg["Subject"] = "with attachment"
        msg["Date"] = "Thu, 11 Jun 2026 10:00:00 +0200"
        msg.set_content("see attached")
        msg.add_attachment(
            b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="invoice.pdf"
        )
        cur = os.path.join(acc.maildir_path, "cur")
        os.makedirs(cur, exist_ok=True)
        with open(os.path.join(cur, "200.mcpatt.host:2,S"), "wb") as f:
            f.write(msg.as_bytes())
        index_service.upsert_message_set(db_session, acc.id)
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        return acc, row

    def test_returns_the_bytes_base64_encoded(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        import base64

        acc, row = self._account_with_attachment(db_session, default_store, tmp_path, tool_user)
        _as(monkeypatch, ["mail:read"], tool_user)
        hit = _call(server, "search_mail", query="attachment")["results"][0]
        part = hit["attachments"][0]["part_index"]

        out = _call(
            server,
            "download_attachment",
            account_id=acc.id,
            message_id_hash=row.message_id_hash.hex(),
            part_index=part,
        )

        assert base64.b64decode(out["content_base64"]) == b"%PDF-1.4 fake"
        assert out["filename"] == "invoice.pdf"
        assert out["size_bytes"] == len(b"%PDF-1.4 fake")

    def test_an_oversized_part_names_the_imap_route_out(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        """The cap must redirect the agent, not dead-end it."""
        import mailfallback.mcp_server as ms

        acc, row = self._account_with_attachment(db_session, default_store, tmp_path, tool_user)
        monkeypatch.setattr(ms, "MCP_ATTACHMENT_MAX_BYTES", 1)
        _as(monkeypatch, ["mail:read"], tool_user)
        hit = _call(server, "search_mail", query="attachment")["results"][0]

        with pytest.raises(Exception) as exc:
            _call(
                server,
                "download_attachment",
                account_id=acc.id,
                message_id_hash=row.message_id_hash.hex(),
                part_index=hit["attachments"][0]["part_index"],
            )

        message = str(exc.value)
        assert "imap_coords" in message
        assert "INBOX" in message

    def test_an_oversized_indexed_size_is_refused_without_reading_the_bytes(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        """The indexed size_bytes is a hint that should short-circuit the
        disk (or snapshot) read entirely — proven here by patching
        extract_attachment_bytes and asserting it was never called."""
        import mailfallback.mcp_server as ms
        from mailfallback.routers import restore

        acc, row = self._account_with_attachment(db_session, default_store, tmp_path, tool_user)
        monkeypatch.setattr(ms, "MCP_ATTACHMENT_MAX_BYTES", 1)
        _as(monkeypatch, ["mail:read"], tool_user)
        hit = _call(server, "search_mail", query="attachment")["results"][0]

        called = []
        monkeypatch.setattr(
            restore,
            "extract_attachment_bytes",
            lambda *a, **k: called.append(1) or (b"unused", "unused", "live"),
        )

        with pytest.raises(Exception) as exc:
            _call(
                server,
                "download_attachment",
                account_id=acc.id,
                message_id_hash=row.message_id_hash.hex(),
                part_index=hit["attachments"][0]["part_index"],
            )

        assert called == [], "extraction must not run when the index already says over-cap"
        message = str(exc.value)
        assert "imap_coords" in message
        assert "INBOX" in message


class TestSyncTools:
    def test_a_read_only_token_cannot_trigger_a_sync(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        acc, _ = _indexed_account(db_session, default_store, tmp_path, tool_user, name="s1")
        _as(monkeypatch, ["mail:read"], tool_user)

        with pytest.raises(Exception) as exc:
            _call(server, "sync_now", account_id=acc.id)
        assert "sync:trigger" in str(exc.value)

    def test_a_sync_token_queues_and_submits(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        """Queueing without submitting leaves a row that wedges the account."""
        import mailfallback.mcp_server as ms

        acc, _ = _indexed_account(db_session, default_store, tmp_path, tool_user, name="s2")
        submitted = []
        monkeypatch.setattr(ms, "submit_sync_job", lambda job_id: submitted.append(job_id))
        _as(monkeypatch, ["sync:trigger"], tool_user)

        out = _call(server, "sync_now", account_id=acc.id)

        assert out["status"] == "pending"
        assert out["already_queued"] is False
        assert submitted == [out["job_id"]]

    def test_a_second_trigger_reports_already_queued_and_does_not_resubmit(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        import mailfallback.mcp_server as ms

        acc, _ = _indexed_account(db_session, default_store, tmp_path, tool_user, name="s3")
        submitted = []
        monkeypatch.setattr(ms, "submit_sync_job", lambda job_id: submitted.append(job_id))
        _as(monkeypatch, ["sync:trigger"], tool_user)

        first = _call(server, "sync_now", account_id=acc.id)
        second = _call(server, "sync_now", account_id=acc.id)

        assert second["already_queued"] is True
        assert second["job_id"] == first["job_id"]
        assert submitted == [first["job_id"]]

    def test_a_suspended_account_is_refused_and_queues_nothing(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        from mailfallback.models import SyncJob

        acc, _ = _indexed_account(db_session, default_store, tmp_path, tool_user, name="s4")
        acc.suspended = True
        db_session.commit()
        _as(monkeypatch, ["sync:trigger"], tool_user)

        with pytest.raises(Exception) as exc:
            _call(server, "sync_now", account_id=acc.id)

        assert "suspended" in str(exc.value)
        assert db_session.query(SyncJob).count() == 0

    def test_a_migrating_account_is_refused_and_queues_nothing(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        from mailfallback.models import SyncJob

        acc, _ = _indexed_account(db_session, default_store, tmp_path, tool_user, name="s4a")
        acc.migrating = True
        db_session.commit()
        _as(monkeypatch, ["sync:trigger"], tool_user)

        with pytest.raises(Exception) as exc:
            _call(server, "sync_now", account_id=acc.id)

        assert "migration" in str(exc.value)
        assert db_session.query(SyncJob).count() == 0

    def test_a_migrating_owner_is_refused_and_queues_nothing(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        """A CO-owner migrating, not the token's own user — the token owner's
        own migrating flag is caught by the guard above; a shared account
        with another owner mid-migration is what this guard protects."""
        from mailfallback.models import SyncJob

        acc, _ = _indexed_account(db_session, default_store, tmp_path, tool_user, name="s4b")
        co_owner = create_user(
            db_session,
            "mcp-co-owner",
            "co-ownerpass12345",
            UserRole.user,
            store_id=default_store.id,
        )
        co_owner.migrating = True
        db_session.execute(account_owners.insert().values(account_id=acc.id, user_id=co_owner.id))
        db_session.commit()
        _as(monkeypatch, ["sync:trigger"], tool_user)

        with pytest.raises(Exception) as exc:
            _call(server, "sync_now", account_id=acc.id)

        assert "user migration" in str(exc.value)
        assert db_session.query(SyncJob).count() == 0

    def test_a_paused_account_is_refused_with_its_reason(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        """A human may override a pause; an agent cannot weigh the quota cost."""
        from mailfallback.models import SyncJob

        acc, _ = _indexed_account(db_session, default_store, tmp_path, tool_user, name="s5")
        acc.pause_reason = "budget"
        db_session.commit()
        _as(monkeypatch, ["sync:trigger"], tool_user)

        with pytest.raises(Exception) as exc:
            _call(server, "sync_now", account_id=acc.id)

        assert "budget" in str(exc.value)
        assert db_session.query(SyncJob).count() == 0

    def test_status_is_readable_and_scoped(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        import mailfallback.mcp_server as ms

        acc, _ = _indexed_account(db_session, default_store, tmp_path, tool_user, name="s6")
        monkeypatch.setattr(ms, "submit_sync_job", lambda job_id: None)
        _as(monkeypatch, ["sync:trigger"], tool_user)
        job_id = _call(server, "sync_now", account_id=acc.id)["job_id"]

        out = _call(server, "sync_status", job_id=job_id)

        assert out["job_id"] == job_id
        assert out["account_id"] == acc.id

    def test_another_users_job_is_refused(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        from mailfallback.services import sync_service

        other = create_user(
            db_session, "other4", "otherpass12345", UserRole.user, store_id=default_store.id
        )
        acc, _ = _indexed_account(db_session, default_store, tmp_path, other, name="s7")
        job = sync_service.create_sync_job(db_session, acc.id, source="api")
        _as(monkeypatch, ["sync:trigger"], tool_user)

        with pytest.raises(Exception) as exc:
            _call(server, "sync_status", job_id=job.id)
        assert "No such" in str(exc.value)

    def test_an_unknown_job_and_someone_elses_job_are_refused_identically(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        """No oracle: the wording must not let a caller tell "never existed"
        apart from "exists, but isn't yours"."""
        from mailfallback.services import sync_service

        other = create_user(
            db_session, "other5", "otherpass12345", UserRole.user, store_id=default_store.id
        )
        acc, _ = _indexed_account(db_session, default_store, tmp_path, other, name="s8")
        job = sync_service.create_sync_job(db_session, acc.id, source="api")
        _as(monkeypatch, ["sync:trigger"], tool_user)

        with pytest.raises(Exception) as unknown_exc:
            _call(server, "sync_status", job_id="not-a-real-job-id")
        with pytest.raises(Exception) as other_users_exc:
            _call(server, "sync_status", job_id=job.id)

        assert str(unknown_exc.value) == str(other_users_exc.value)


class TestSyncAnnotation:
    def test_sync_now_is_the_only_tool_not_marked_read_only(self, server):
        tools = anyio.run(server.list_tools)
        not_read_only = [
            t.name for t in tools if not getattr(t.annotations, "read_only_hint", False)
        ]
        assert not_read_only == ["sync_now"]


class TestPagination:
    """search_mail/search_attachments must reject what the REST models reject.

    The REST request models pin ``page: Field(1, ge=1)`` and
    ``page_size: Field(50, ge=1, le=200)`` — before this fix, the MCP tools
    took bare ``int`` for both, so a zero-based agent's first guess
    (``page=0``) or an unbounded ``page_size`` reached ``search_service``
    unclamped, which builds ``OFFSET (page - 1) * page_size`` straight into
    the query.
    """

    @pytest.mark.parametrize("tool_name", ["search_mail", "search_attachments"])
    def test_page_zero_is_refused(self, server, tool_user, monkeypatch, tool_name):
        _as(monkeypatch, ["mail:read"], tool_user)
        with pytest.raises(Exception) as exc:
            _call(server, tool_name, query="", page=0)
        assert "page" in str(exc.value)

    @pytest.mark.parametrize("tool_name", ["search_mail", "search_attachments"])
    def test_an_oversized_page_size_is_refused(self, server, tool_user, monkeypatch, tool_name):
        _as(monkeypatch, ["mail:read"], tool_user)
        with pytest.raises(Exception) as exc:
            _call(server, tool_name, query="", page_size=100000)
        assert "page_size" in str(exc.value)

    @pytest.mark.parametrize("tool_name", ["search_mail", "search_attachments"])
    def test_the_bounds_are_published_in_the_input_schema(self, server, tool_name):
        """An agent should be able to read the limit, not discover it by
        crashing — so the schema itself must carry these bounds, not just the
        runtime check. Dumping the schema means a future signature change
        that drops the constraint fails this test rather than going silent.
        """
        tools = anyio.run(server.list_tools)
        by_name = {t.name: t for t in tools}
        properties = by_name[tool_name].input_schema["properties"]

        assert properties["page"]["minimum"] == 1
        assert properties["page_size"]["minimum"] == 1
        assert properties["page_size"]["maximum"] == 200


class TestRefusalsAreNotProtocolErrors:
    """A refusal must be a tool-execution failure the SDK can wrap into an
    ``is_error`` result, not an ``MCPError`` the SDK re-raises as a top-level
    JSON-RPC protocol error — the latter never reaches a client's normal
    "read the tool result" path. ``scripts/verify_mcp.py`` proves the
    end-to-end shape of this over real JSON-RPC; these assert the underlying
    condition that makes that possible: none of these refusals raise
    ``MCPError`` anymore. "Not authenticated" (no token at all) is the one
    exception left as ``MCPError``, and it is not reachable through a tool
    call at all — the auth middleware refuses it first.
    """

    def test_a_missing_scope_is_not_an_mcperror(self, server, tool_user, monkeypatch):
        from mcp.shared.exceptions import MCPError

        _as(monkeypatch, ["imap"], tool_user)
        with pytest.raises(Exception) as exc:
            _call(server, "list_mailboxes")
        assert not isinstance(exc.value.__cause__ or exc.value, MCPError)

    def test_an_inaccessible_mailbox_is_not_an_mcperror(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        from mcp.shared.exceptions import MCPError

        other = create_user(
            db_session, "refusal-other", "otherpass12345", UserRole.user, store_id=default_store.id
        )
        acc, row = _indexed_account(
            db_session, default_store, tmp_path, other, name="refusaltheirs"
        )
        _as(monkeypatch, ["mail:read"], tool_user)

        with pytest.raises(Exception) as exc:
            _call(
                server,
                "get_message",
                account_id=acc.id,
                message_id_hash=row.message_id_hash.hex(),
            )
        assert not isinstance(exc.value.__cause__ or exc.value, MCPError)

    def test_the_over_cap_attachment_redirect_is_not_an_mcperror(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        """The over-cap message's entire purpose is to redirect the caller to
        imap_coords — that redirect only works if the model actually sees the
        text, which requires it to travel as an is_error result, not a
        protocol error."""
        from mcp.shared.exceptions import MCPError

        import mailfallback.mcp_server as ms

        acc, row = TestDownloadAttachment()._account_with_attachment(
            db_session, default_store, tmp_path, tool_user
        )
        monkeypatch.setattr(ms, "MCP_ATTACHMENT_MAX_BYTES", 1)
        _as(monkeypatch, ["mail:read"], tool_user)
        hit = _call(server, "search_mail", query="attachment")["results"][0]

        with pytest.raises(Exception) as exc:
            _call(
                server,
                "download_attachment",
                account_id=acc.id,
                message_id_hash=row.message_id_hash.hex(),
                part_index=hit["attachments"][0]["part_index"],
            )
        assert not isinstance(exc.value.__cause__ or exc.value, MCPError)

    def test_sync_now_refusal_is_not_an_mcperror(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        from mcp.shared.exceptions import MCPError

        acc, _ = _indexed_account(
            db_session, default_store, tmp_path, tool_user, name="refusalsync"
        )
        acc.suspended = True
        db_session.commit()
        _as(monkeypatch, ["sync:trigger"], tool_user)

        with pytest.raises(Exception) as exc:
            _call(server, "sync_now", account_id=acc.id)
        assert not isinstance(exc.value.__cause__ or exc.value, MCPError)

    def test_sync_status_no_such_job_is_not_an_mcperror(self, server, tool_user, monkeypatch):
        from mcp.shared.exceptions import MCPError

        _as(monkeypatch, ["sync:trigger"], tool_user)
        with pytest.raises(Exception) as exc:
            _call(server, "sync_status", job_id="not-a-real-job-id")
        assert not isinstance(exc.value.__cause__ or exc.value, MCPError)


class TestImapCoordsTool:
    """``imap_coords`` had no test anywhere before this fix, even though it
    is not a thin pass-through: ``_mcp_account`` scoping and the
    ``[:RESOLVE_COORDS_MAX_IDS]`` truncation are MCP-side code. Mirrors
    ``tests/test_agent_api.py::TestImapCoords``.
    """

    def test_resolves_for_the_callers_own_mailbox(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        """No Dovecot to connect to in this test environment, so this is the
        unreachable case by construction: the id must fold into ``missing``
        AND ``imap_unavailable`` must be True, surfaced as-is from
        ``restore.resolve_uids_for_account`` rather than swallowed."""
        acc, row = _indexed_account(db_session, default_store, tmp_path, tool_user)
        _as(monkeypatch, ["mail:read"], tool_user)

        out = _call(server, "imap_coords", account_id=acc.id, message_ids=[row.message_id])

        assert set(out) == {"resolved", "missing", "imap_unavailable"}
        assert out["resolved"] == {}
        assert out["missing"] == [row.message_id]
        assert out["imap_unavailable"] is True

    def test_refuses_another_users_account_without_revealing_it_exists(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        other = create_user(
            db_session, "coords-other", "otherpass12345", UserRole.user, store_id=default_store.id
        )
        acc, row = _indexed_account(db_session, default_store, tmp_path, other, name="coordstheirs")
        _as(monkeypatch, ["mail:read"], tool_user)

        with pytest.raises(Exception) as exc:
            _call(server, "imap_coords", account_id=acc.id, message_ids=[row.message_id])

        # Same non-oracle wording every other tool uses for an inaccessible
        # account (get_message, download_attachment, sync_now, ...).
        assert "No such mailbox" in str(exc.value)

    def test_ids_beyond_the_cap_are_ignored_entirely(
        self, server, db_session, default_store, tmp_path, tool_user, monkeypatch
    ):
        """Ids past the cap must land in NEITHER `resolved` nor `missing` — a
        naive truncation-less implementation instead reports them as
        missing, which is the bug this test exists to catch."""
        from mailfallback.routers.agent import RESOLVE_COORDS_MAX_IDS

        acc, _ = _indexed_account(db_session, default_store, tmp_path, tool_user, name="coordscap")
        ids = [f"<bogus-{i}@x>" for i in range(RESOLVE_COORDS_MAX_IDS + 100)]
        _as(monkeypatch, ["mail:read"], tool_user)

        out = _call(server, "imap_coords", account_id=acc.id, message_ids=ids)

        assert out["resolved"] == {}
        assert len(out["missing"]) == RESOLVE_COORDS_MAX_IDS
        for beyond_cap in ids[RESOLVE_COORDS_MAX_IDS:]:
            assert beyond_cap not in out["missing"]
