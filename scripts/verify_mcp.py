#!/usr/bin/env python3
"""Drive the mounted MCP server over real HTTP with raw JSON-RPC.

``tests/test_mcp_tools.py`` calls tools through the server object directly —
that proves tool behaviour but nothing about the transport: the
session-manager lifespan, the host allowlist, the bearer auth middleware, or
the streamable-HTTP/JSON-RPC framing a real client actually speaks. This
script drives ``/mcp/`` with plain ``httpx``, no MCP client SDK, because the
SDK's client would prove nothing extra here (this runs inside the container,
next to the server) and because raw requests are what a third-party client
will send on the wire.

Run INSIDE the mailfallback container. ``scripts/`` is deliberately not baked
into the image, so the host copy has to be piped in on stdin:

    docker compose exec -T -w /app mailfallback \
        uv run --no-sync python - < scripts/verify_mcp.py

Deliberately NOT tested here: calling ``sync_now`` with a token that would
succeed. That would queue and run a real mbsync against a real mailbox and
burn the provider's daily IMAP quota for no reason a live check needs. Only
the refusal path is probed — a read-only (``mail:read``) token reaching
``sync_now`` and being turned away by its own scope check.

Every refusal probed here is asserted through ``result.isError``/``content``,
never through a top-level JSON-RPC ``error`` object: the SDK re-raises only a
genuine protocol-level condition (no/garbage/revoked token, before a session
even opens) as the latter, and wraps every tool-execution refusal — a missing
scope, an over-cap attachment redirecting to ``imap_coords`` — into the
former, which is the only path a client's normal "read the tool result" flow
is guaranteed to see. A one-off account with one real oversized attachment is
created (and torn down in ``finally``, alongside the probe user) purely to
drive that over-cap redirect through the real extraction path rather than
asserting it from a unit test's monkeypatched cap.

The streamable-HTTP transport is session-based: ``initialize`` returns an
``Mcp-Session-Id`` header, every later call on that session must repeat it,
and a session is bound to the identity that opened it — so each token used
here gets its own ``initialize`` round-trip rather than reusing one session
across tokens. Responses are Server-Sent Events, not bare JSON (the server is
mounted with the SDK's default ``json_response=False``), so every response
here is read as SSE and the JSON payload is pulled off its ``data:`` line.

Tool count: eight tools, seven read-only, ``sync_now`` the only one not.
Task 2's ``ping`` — scaffolding to prove the mount before any real tool
existed — has been removed now that this script proves the transport
properly and ``list_mailboxes`` already answers "is the server up and is my
token good" with something useful.
"""
# ruff: noqa: T201

import json
import os
import shutil
import sys
from email.message import EmailMessage

import httpx

from mailfallback.db import SessionLocal
from mailfallback.mcp_server import MCP_ATTACHMENT_MAX_BYTES
from mailfallback.models import Account, MailStore, User, UserRole, account_owners
from mailfallback.services import app_credential_service as svc
from mailfallback.services import index_service, user_service

HTTP_BASE = "http://localhost:8000"
MCP_PATH = "/mcp/"  # trailing slash: the app is mounted here, "/mcp" 307s to it

USERNAME = "mcpprobe"
PASSWORD = "probepass123456"  # pragma: allowlist secret
ACCEPT = "application/json, text/event-stream"

# The eight tools the live server registers, and which of them are
# read-only. Kept as an explicit set (rather than "whatever tools/list says")
# so a tool silently added or removed is caught as a failed expectation.
EXPECTED_TOOLS = {
    "list_mailboxes",
    "search_mail",
    "search_attachments",
    "get_message",
    "download_attachment",
    "imap_coords",
    "sync_now",
    "sync_status",
}
EXPECTED_NOT_READ_ONLY = {"sync_now"}

failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"  {label:38} {actual!r:40} {'OK' if ok else f'FAIL (want {expected!r})'}")
    if not ok:
        failures.append(label)


def check_true(label, ok, detail=""):
    print(f"  {label:38} {'OK' if ok else f'FAIL {detail}'}")
    if not ok:
        failures.append(label)


def _read_rpc(resp):
    """Pull the JSON-RPC payload out of a response, SSE or plain JSON alike.

    A successful call over the (default, non-json_response) streamable-HTTP
    transport comes back as ``text/event-stream``; an auth failure that never
    reaches the transport comes back as plain ``application/json``. Both are
    read here so callers don't have to care which one a given request took.
    """
    ct = resp.headers.get("content-type", "")
    if "text/event-stream" in ct:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())
        return None
    return json.loads(resp.text) if resp.text else None


def _initialize(client, token, header_name="Authorization"):
    """Open a new MCP session with this token. Returns (status, session_id, payload)."""
    resp = client.post(
        MCP_PATH,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "verify_mcp", "version": "0"},
            },
        },
        headers={"Accept": ACCEPT, header_name: f"Bearer {token}"},
    )
    session_id = resp.headers.get("mcp-session-id")
    payload = _read_rpc(resp) if resp.status_code == 200 else None
    if resp.status_code == 200:
        # Real clients send this before anything else; the session is
        # already usable without it, but stay in character as one.
        client.post(
            MCP_PATH,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={
                "Accept": ACCEPT,
                header_name: f"Bearer {token}",
                "Mcp-Session-Id": session_id,
            },
        )
    return resp.status_code, session_id, payload


def _call_tool(client, token, session_id, name, arguments=None):
    """Call one tool in an already-open session. Returns the RPC payload."""
    resp = client.post(
        MCP_PATH,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
        headers={
            "Accept": ACCEPT,
            "Authorization": f"Bearer {token}",
            "Mcp-Session-Id": session_id,
        },
    )
    check(f"tools/call {name} HTTP status", resp.status_code, 200)
    return _read_rpc(resp)


def _result_text(payload):
    """The text of a ``result``'s content blocks, or "" if there is no result.

    A refusal that reaches the model at all does so as ``result.isError``
    with its message in ``content`` — never as a top-level JSON-RPC ``error``
    object, which the SDK reserves for a genuinely protocol-level condition.
    """
    content = payload.get("result", {}).get("content", [])
    return " ".join(block.get("text", "") for block in content)


def main():
    db = SessionLocal()
    old = db.query(User).filter(User.username == USERNAME).first()
    if old:
        db.delete(old)
        db.commit()
    old_acc = db.query(Account).filter(Account.name == "mcpprobeacc").first()
    if old_acc:
        shutil.rmtree(old_acc.maildir_path, ignore_errors=True)
        db.delete(old_acc)
        db.commit()
    acc = None

    store = db.query(MailStore).first()
    user = user_service.create_user(db, USERNAME, PASSWORD, UserRole.user, store_id=store.id)
    # Read before the user row is deleted below, same reasoning as
    # verify_access_token_login.py: the home dir lives under the store the
    # probe user was created on, not a hardcoded path.
    home_dir = os.path.join(
        store.path, ".dovecot-home", user_service._sanitize_path_component(USERNAME)
    )
    try:
        read_cred, read_token = svc.create_credential(
            db, user, name="mcp-read", scopes=[svc.SCOPE_MAIL_READ]
        )
        _, imap_token = svc.create_credential(db, user, name="mcp-imap", scopes=[svc.SCOPE_IMAP])
        revoked_cred, revoked_token = svc.create_credential(
            db, user, name="mcp-revoked", scopes=[svc.SCOPE_MAIL_READ]
        )
        svc.revoke_credential(db, user, revoked_cred.id)

        # One account with one real over-cap attachment, written straight to
        # its Maildir the way mbsync would — the point is to drive
        # download_attachment's redirect through the actual extraction path,
        # not a monkeypatched cap.
        acc = Account(
            name="mcpprobeacc",
            imap_host="h",
            email_address=f"{USERNAME}-acc@example.com",
            maildir_path=os.path.join(store.path, "mcpprobeacc-placeholder"),
            store_id=store.id,
        )
        db.add(acc)
        db.flush()
        acc.maildir_path = os.path.join(store.path, acc.id)
        db.execute(account_owners.insert().values(account_id=acc.id, user_id=user.id))
        db.commit()

        msg = EmailMessage()
        msg["Message-Id"] = "<mcpprobe-attachment@x>"
        msg["From"] = "a@b.c"
        msg["To"] = "d@e.f"
        msg["Subject"] = "mcp probe over-cap attachment"
        msg["Date"] = "Thu, 11 Jun 2026 10:00:00 +0200"
        msg.set_content("see attached")
        msg.add_attachment(
            b"x" * (MCP_ATTACHMENT_MAX_BYTES + 1024),
            maintype="application",
            subtype="octet-stream",
            filename="big.bin",
        )
        cur_dir = os.path.join(acc.maildir_path, "cur")
        os.makedirs(cur_dir, exist_ok=True)
        with open(os.path.join(cur_dir, "1.mcpprobe.host:2,S"), "wb") as f:
            f.write(msg.as_bytes())
        index_service.upsert_message_set(db, acc.id)

        with httpx.Client(base_url=HTTP_BASE, timeout=10) as client:
            print("Bearer auth surface")
            resp = client.post(
                MCP_PATH,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Accept": ACCEPT},
            )
            check("no token: status", resp.status_code, 401)
            check_true(
                "no token: WWW-Authenticate names bearer",
                "bearer" in resp.headers.get("www-authenticate", "").lower(),
            )

            resp = client.post(
                MCP_PATH,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Accept": ACCEPT, "Authorization": "Bearer mfb_garbage_nope"},
            )
            check("garbage token: status", resp.status_code, 401)

            resp = client.post(
                MCP_PATH,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Accept": ACCEPT, "Authorization": f"Bearer {revoked_token}"},
            )
            check("revoked token: status", resp.status_code, 401)

            print("\ninitialize (mail:read token)")
            status, session_id, payload = _initialize(client, read_token)
            check("initialize: status", status, 200)
            result = (payload or {}).get("result", {})
            check(
                "initialize: server name", result.get("serverInfo", {}).get("name"), "MailFallBack"
            )
            check_true(
                "initialize: protocolVersion present",
                bool(result.get("protocolVersion")),
                f"(got {result.get('protocolVersion')!r})",
            )

            print("\ntools/list")
            resp = client.post(
                MCP_PATH,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                headers={
                    "Accept": ACCEPT,
                    "Authorization": f"Bearer {read_token}",
                    "Mcp-Session-Id": session_id,
                },
            )
            check("tools/list: status", resp.status_code, 200)
            tools = _read_rpc(resp)["result"]["tools"]
            names = {t["name"] for t in tools}
            check("tools/list: tool count", len(tools), len(EXPECTED_TOOLS))
            check("tools/list: tool names", names, EXPECTED_TOOLS)
            not_read_only = {
                t["name"] for t in tools if not t.get("annotations", {}).get("readOnlyHint")
            }
            check("tools/list: non-read-only tools", not_read_only, EXPECTED_NOT_READ_ONLY)

            print("\ntools/call: read tools succeed")
            payload = _call_tool(client, read_token, session_id, "list_mailboxes")
            check_true(
                "list_mailboxes: no error",
                "result" in payload,
                f"(payload={payload})",
            )

            payload = _call_tool(client, read_token, session_id, "search_mail", {"query": ""})
            structured = payload.get("result", {}).get("structuredContent", {})
            check_true(
                "search_mail: has results/total",
                {"results", "total"} <= structured.keys(),
                f"(keys={list(structured.keys())})",
            )

            print("\ntools/call: imap_coords round trip")
            payload = _call_tool(
                client,
                read_token,
                session_id,
                "imap_coords",
                {"account_id": acc.id, "message_ids": ["<mcpprobe-attachment@x>"]},
            )
            structured = payload.get("result", {}).get("structuredContent", {})
            check_true(
                "imap_coords: no error",
                "result" in payload and not payload["result"].get("isError"),
                f"(payload={payload})",
            )
            check_true(
                "imap_coords: has resolved/missing/imap_unavailable",
                {"resolved", "missing", "imap_unavailable"} <= structured.keys(),
                f"(keys={list(structured.keys())})",
            )

            print("\ntools/call: refusals arrive as tool results, not JSON-RPC errors")
            payload = _call_tool(
                client,
                read_token,
                session_id,
                "sync_now",
                {"account_id": "00000000-0000-0000-0000-000000000000"},
            )
            check_true(
                "sync_now (read-only token): no top-level error object",
                "error" not in payload,
                f"(payload={payload})",
            )
            check_true(
                "sync_now (read-only token): result.isError",
                payload.get("result", {}).get("isError") is True,
                f"(payload={payload})",
            )
            check_true(
                "sync_now (read-only token): message names sync:trigger",
                "sync:trigger" in _result_text(payload),
                f"(text={_result_text(payload)!r})",
            )

            status, session_id2, _ = _initialize(client, imap_token)
            check("initialize (imap-only token): status", status, 200)
            payload = _call_tool(client, imap_token, session_id2, "list_mailboxes")
            check_true(
                "list_mailboxes (imap-only token): result.isError",
                payload.get("result", {}).get("isError") is True,
                f"(payload={payload})",
            )
            check_true(
                "list_mailboxes (imap-only token): message names mail:read",
                "mail:read" in _result_text(payload),
                f"(text={_result_text(payload)!r})",
            )

            print("\ntools/call: over-cap attachment redirects, as a tool result")
            hit_payload = _call_tool(
                client, read_token, session_id, "search_mail", {"query": "over-cap attachment"}
            )
            hits = hit_payload.get("result", {}).get("structuredContent", {}).get("results", [])
            check_true("over-cap fixture: search finds it", len(hits) == 1, f"(hits={hits})")
            part_index = hits[0]["attachments"][0]["part_index"] if hits else None

            payload = _call_tool(
                client,
                read_token,
                session_id,
                "download_attachment",
                {
                    "account_id": acc.id,
                    "message_id_hash": hits[0]["message_id_hash"] if hits else "",
                    "part_index": part_index,
                },
            )
            check_true(
                "download_attachment (over-cap): no top-level error object",
                "error" not in payload,
                f"(payload={payload})",
            )
            check_true(
                "download_attachment (over-cap): result.isError",
                payload.get("result", {}).get("isError") is True,
                f"(payload={payload})",
            )
            check_true(
                "download_attachment (over-cap): message redirects to imap_coords",
                "imap_coords" in _result_text(payload),
                f"(text={_result_text(payload)!r})",
            )

            print("\nCase-insensitive header")
            status, _, payload = _initialize(client, read_token, header_name="authorization")
            check("lowercase authorization header: status", status, 200)
            check(
                "lowercase authorization header: server name",
                (payload or {}).get("result", {}).get("serverInfo", {}).get("name"),
                "MailFallBack",
            )

        print("\nUsage recorded")
        db.refresh(read_cred)
        check("last_used_kind on the used token", read_cred.last_used_kind, "mcp")
    finally:
        # Guard each delete on the row still being there: db.delete(None)
        # raises UnmappedInstanceError, and this runs in a `finally` — an
        # already-gone row (e.g. a prior run's teardown half-completed)
        # would then replace whatever real failure this block exists to
        # let through with a teardown crash instead.
        user_row = db.query(User).filter(User.username == USERNAME).first()
        if user_row is not None:
            db.delete(user_row)
        if acc is not None:
            acc_row = db.query(Account).filter(Account.id == acc.id).first()
            if acc_row is not None:
                db.delete(acc_row)
        db.commit()
        shutil.rmtree(home_dir, ignore_errors=True)
        if acc is not None:
            shutil.rmtree(acc.maildir_path, ignore_errors=True)
        print(f"\nRemoved {home_dir}")

    print()
    if failures:
        print(f"FAILED: {len(failures)} expectation(s): {', '.join(failures)}")
        return 1
    print("All expectations met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
