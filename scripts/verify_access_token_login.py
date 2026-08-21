#!/usr/bin/env python3
"""Prove an access token authenticates against Dovecot IMAP and the agent
HTTP API, and nothing else broke.

Run INSIDE the mailfallback container, which can reach dovecot by name and
serves its own HTTP API on localhost. ``scripts/`` is deliberately not baked
into the image, so the host copy has to be piped in on stdin:

    docker compose exec -T -w /app mailfallback \
        uv run --no-sync python - < scripts/verify_access_token_login.py

One script, both surfaces, so a future change to either is caught by the
same command. Exits non-zero on the first failed expectation.
"""
# ruff: noqa: T201

import imaplib
import os
import shutil
import sys

import httpx

from mailfallback.db import SessionLocal
from mailfallback.models import Account, MailStore, User, UserRole
from mailfallback.services import account_service, staging_service, user_service
from mailfallback.services import app_credential_service as svc

HTTP_BASE = "http://localhost:8000"

USERNAME = "tokenprobe"
PASSWORD = "probepass123"  # pragma: allowlist secret
failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"  {label:34} {actual!r:28} {'OK' if ok else f'FAIL (want {expected!r})'}")
    if not ok:
        failures.append(label)


def login(password):
    """Return 'ok' or 'fail' — never raise, the failure IS the observation."""
    try:
        conn = imaplib.IMAP4("dovecot", 31143)
        conn.login(USERNAME, password)
        conn.logout()
        return "ok"
    except Exception:
        return "fail"


def main():
    db = SessionLocal()
    old = db.query(User).filter(User.username == USERNAME).first()
    if old:
        db.delete(old)
        db.commit()
    store = db.query(MailStore).first()
    user = user_service.create_user(db, USERNAME, PASSWORD, UserRole.user, store_id=store.id)
    # Read before the user row is deleted below — the home dir lives under the
    # store the probe user was created on, not a hardcoded path.
    home_dir = os.path.join(
        store.path, ".dovecot-home", user_service._sanitize_path_component(USERNAME)
    )
    probe_account = None
    try:
        _, token = svc.create_credential(db, user, name="probe", scopes=[svc.SCOPE_IMAP])
        read_cred, read_only = svc.create_credential(
            db, user, name="no-imap", scopes=[svc.SCOPE_MAIL_READ]
        )
        _, sync_only = svc.create_credential(
            db, user, name="sync-only", scopes=[svc.SCOPE_SYNC_TRIGGER]
        )
        revoked_cred, revoked = svc.create_credential(
            db, user, name="revoked", scopes=[svc.SCOPE_IMAP]
        )
        svc.revoke_credential(db, user, revoked_cred.id)

        # An existing account, borrowed just long enough for a meaningful
        # mailboxes/sync assertion. Ownership only — zero file operations —
        # and detached again in the finally below, never deleted.
        probe_account = db.query(Account).first()
        if probe_account is not None:
            account_service.assign_owner(db, probe_account.id, user.id)

        print("Access-token IMAP login")
        check("valid token", login(token), "ok")
        check("real password still works", login(PASSWORD), "ok")
        check("wrong password", login("nope"), "fail")
        check("token without imap scope", login(read_only), "fail")
        check("revoked token", login(revoked), "fail")
        check("garbage token", login("mfb_nosuch_secret"), "fail")

        db.refresh(revoked_cred)
        print("\nUsage recorded")
        creds = svc.list_credentials(db, user)
        probe = next(c for c in creds if c.name == "probe")
        check("last_used_kind on the used one", probe.last_used_kind, "imap")
        check("last_used_at set", probe.last_used_at is not None, True)

        print("\nRead-only still enforced")
        conn = imaplib.IMAP4("dovecot", 31143)
        conn.login(USERNAME, token)
        check("INBOX rights", conn.myrights("INBOX")[1][0].decode().split()[-1], "lrs")
        check(
            "APPEND to INBOX", conn.append("INBOX", "", None, b"From: a@b.c\r\n\r\nx\r\n")[0], "NO"
        )
        check("CREATE top-level", conn.create("ProbeTop")[0], "NO")
        conn.logout()

        # acl_defaults_from_inbox (this phase) and the staging fix in 365d8c5 touch
        # the same generated ACL file, so prove the curation surface is still
        # writable rather than assuming the explicit `mailbox Staging` filter won.
        print("\nStaging still writable")
        staging_service._get_or_create_area(db, user)
        sdir = staging_service.staging_dir(user)
        for sub in ("cur", "new", "tmp"):
            os.makedirs(os.path.join(sdir, sub), exist_ok=True)
        conn = imaplib.IMAP4("dovecot", 31143)
        conn.login(USERNAME, token)
        rights = conn.myrights("Staging")[1][0].decode().split()[-1]
        check("MYRIGHTS Staging", rights, "lrwstied")
        check(
            "APPEND to Staging",
            conn.append("Staging", "", None, b"From: a@b.c\r\nSubject: s\r\n\r\nx\r\n")[0],
            "OK",
        )
        conn.logout()

        print("\nHTTP bearer surface")
        with httpx.Client(base_url=HTTP_BASE, timeout=10) as client:
            # Lowercase header name: HTTP header names are case-insensitive
            # and the scheme match is deliberately case-insensitive too, so
            # this must behave identically to "Authorization".
            resp = client.get(
                "/api/v1/agent/mailboxes", headers={"authorization": f"Bearer {read_only}"}
            )
            check("GET mailboxes (read token)", resp.status_code, 200)
            mailboxes = resp.json()
            check("mailboxes body is a list", isinstance(mailboxes, list), True)
            if probe_account is not None:
                check("mailboxes not empty", len(mailboxes) > 0, True)
            else:
                print("  (no existing account to attach; mailboxes is legitimately empty)")

            resp = client.get(
                "/api/v1/agent/mailboxes", headers={"Authorization": f"Bearer {token}"}
            )
            check("GET mailboxes (imap-only token)", resp.status_code, 403)

            resp = client.get(
                "/api/v1/agent/mailboxes", headers={"Authorization": f"Bearer {sync_only}"}
            )
            check("GET mailboxes (sync-only token)", resp.status_code, 403)

            resp = client.get("/api/v1/agent/mailboxes")
            check("GET mailboxes (no header)", resp.status_code, 401)

            resp = client.post(
                "/api/v1/agent/search",
                json={"query": ""},
                headers={"Authorization": f"Bearer {read_only}"},
            )
            check("POST search status", resp.status_code, 200)
            check(
                "search response keys",
                set(resp.json().keys()),
                {"results", "total", "page", "page_size", "partial"},
            )

            sync_account_id = (
                probe_account.id if probe_account else "00000000-0000-0000-0000-000000000000"
            )
            resp = client.post(
                f"/api/v1/agent/sync/{sync_account_id}",
                headers={"Authorization": f"Bearer {read_only}"},
            )
            check("POST sync (read-only token)", resp.status_code, 403)

            resp = client.get(
                "/api/v1/agent/mailboxes", headers={"Authorization": f"Bearer {revoked}"}
            )
            check("GET mailboxes (revoked token)", resp.status_code, 401)

        db.refresh(read_cred)
        check("HTTP token last_used_kind == api", read_cred.last_used_kind, "api")
    finally:
        if probe_account is not None:
            # Detach before deleting the user: account_owners.user_id has no
            # ON DELETE CASCADE, so a lingering ownership row would make the
            # user delete below fail and leave residue behind.
            account_service.remove_owner(db, probe_account.id, user.id)
        db.delete(db.query(User).filter(User.username == USERNAME).first())
        db.commit()
        shutil.rmtree(home_dir, ignore_errors=True)
        print(f"\nRemoved {home_dir}")

    print()
    if failures:
        print(f"FAILED: {len(failures)} expectation(s): {', '.join(failures)}")
        return 1
    print("All expectations met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
