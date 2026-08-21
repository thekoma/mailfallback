#!/usr/bin/env python3
"""Prove an access token authenticates against Dovecot IMAP, and nothing else broke.

Run INSIDE the mailfallback container, which can reach dovecot by name:

    docker compose exec -T -w /app mailfallback \
        uv run --no-sync python scripts/verify_access_token_login.py

Exits non-zero on the first failed expectation.
"""
# ruff: noqa: T201

import imaplib
import os
import sys

from mailfallback.db import SessionLocal
from mailfallback.models import MailStore, User, UserRole
from mailfallback.services import app_credential_service as svc
from mailfallback.services import staging_service, user_service

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
    try:
        _, token = svc.create_credential(db, user, name="probe", scopes=[svc.SCOPE_IMAP])
        _, read_only = svc.create_credential(db, user, name="no-imap", scopes=[svc.SCOPE_MAIL_READ])
        revoked_cred, revoked = svc.create_credential(
            db, user, name="revoked", scopes=[svc.SCOPE_IMAP]
        )
        svc.revoke_credential(db, user, revoked_cred.id)

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
    finally:
        db.delete(db.query(User).filter(User.username == USERNAME).first())
        db.commit()

    print()
    if failures:
        print(f"FAILED: {len(failures)} expectation(s): {', '.join(failures)}")
        return 1
    print("All expectations met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
