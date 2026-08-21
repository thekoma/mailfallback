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

    ``call_tool`` returns a ``CallToolResult``, not the bare value the brief
    assumed. The installed SDK (mcp 2.0.0 / mcp_types 2.0.0) auto-derives
    structured output from the return annotation, and it does not wrap
    consistently: a `dict[str, Any]` return (search_mail, search_attachments,
    get_message) comes back as `structured_content` verbatim via a RootModel,
    while a `list[...]` return (list_mailboxes) is a "generic type" in the
    SDK's own terms and gets wrapped as `structured_content["result"]`. Both
    are auto-detected from the annotation, not something a tool body chooses
    per call, so this if/else keys off the one tool whose return type is a
    list rather than trying to special-case by content.
    """
    result = anyio.run(lambda: server.call_tool(name, kwargs))
    if name == "list_mailboxes":
        return result.structured_content["result"]
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

        names = [m["name"] for m in out]
        assert names == ["mine"]
        assert out[0]["indexed_messages"] == 1
        assert out[0]["folders"] == ["INBOX"]


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
        assert _call(server, "list_mailboxes") == []


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
