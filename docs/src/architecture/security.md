# Security Model

This page documents how MFB's security-relevant controls actually work: credential
encryption, defenses against server-side request forgery (SSRF), the read-only IMAP
boundary, multi-tenant isolation, OAuth CSRF protection, and the reserved restore
username prefix. It describes the current model, not a history of fixes.

## Credential encryption

IMAP credentials (passwords and OAuth2 refresh tokens) are stored encrypted in
PostgreSQL using [Fernet](https://cryptography.io/en/latest/fernet/) symmetric
encryption (`src/mailfallback/security.py`). The Fernet key is not `SECRET_KEY`
itself — it is derived from it with PBKDF2-HMAC-SHA256 (600,000 iterations, a
fixed application-specific salt):

```python
_KDF_ITERATIONS = 600_000
_KDF_SALT = b"mailfallback-fernet-v1"

def _derive_fernet_key(secret_key: str) -> bytes:
    dk = hashlib.pbkdf2_hmac("sha256", secret_key.encode(), _KDF_SALT, _KDF_ITERATIONS)
    return base64.urlsafe_b64encode(dk)
```

!!! warning "Operator duty: set a strong `SECRET_KEY`"
    The entire scheme's strength rests on `SECRET_KEY`. A short or guessable value
    makes the PBKDF2 derivation brute-forceable offline. Generate a long random
    value (e.g. `openssl rand -base64 32`) and treat it like any other root
    secret — losing it makes stored credentials unrecoverable; leaking it exposes
    them.

### Legacy KDF self-heal

An older, weaker key derivation (unsalted SHA-256 of `SECRET_KEY`) predates the
PBKDF2 scheme. `decrypt_credentials` still accepts ciphertext produced by it: a
modern decrypt is tried first, and only on `InvalidToken` does it fall back to
the legacy derivation. Rather than leaving legacy ciphertext in place, every read
path upgrades it in-line. `account_service.decrypt_account_credentials` is the
sanctioned entry point for reading an account's credentials — sync, restore, and
UI code call it instead of `decrypt_credentials` directly:

```python
def decrypt_account_credentials(db: Session, account: Account) -> str | None:
    if not account.credentials:
        return None
    plaintext, upgraded = decrypt_credentials_with_upgrade(account.credentials, settings.secret_key)
    if upgraded is not None:
        account.credentials = upgraded
        db.commit()
    return plaintext
```

`decrypt_credentials_with_upgrade` decrypts once with the modern key; if that
fails, it decrypts with the legacy key and immediately re-encrypts the plaintext
with the modern key, returning the new ciphertext for the caller to persist. The
net effect: every account whose credentials are actually read gets migrated off
the weak KDF automatically, with no batch migration step required. Rows that are
never read stay on the legacy KDF until they are.

## SSRF defenses

Account IMAP hosts are user-supplied and are used to open outbound TCP/TLS
connections from the server — both for one-off "test connection" calls and for
the recurring mbsync sync itself. Without guardrails, an attacker with account-edit
access could point `imap_host` at an internal service (e.g. the PostgreSQL
container, the Docker metadata endpoint, a private admin panel) and use MFB as an
SSRF proxy. `src/mailfallback/services/imap_check.py` implements the defense in
three layers.

### 1. Host validation

`validate_host_not_internal(host)` resolves the hostname and rejects it if any
resolved address is:

- private (RFC 1918), loopback, or link-local
- CGNAT (`100.64.0.0/10` — not covered by Python's `is_private`, so checked
  explicitly)
- reserved, multicast, or unspecified
- an IPv4-mapped IPv6 address wrapping an internal IPv4 (`::ffff:10.0.0.1`) —
  unwrapped before the check so a crafted `AAAA` record can't smuggle an
  internal address past it
- unresolvable at all — a `gaierror` is treated as a rejection, not silently
  passed through, because a swallowed resolution failure at validation time
  followed by a successful resolution at connection time is itself an SSRF
  bypass window

### 2. IP pinning for untrusted connects

Passing host validation once is not enough on its own: DNS is looked up again
when the connection is actually opened, and an attacker controlling the DNS
record (or exploiting the propagation window) could return a public IP for the
validation lookup and an internal IP for the connection lookup — DNS rebinding.

For untrusted, user-supplied hosts (test-connection, account create/edit),
`connect_imap(..., pin_public_ip=True)` closes this gap by resolving once via
`resolve_public_ip`, validating that result, and then connecting directly to
that pinned IP — `_PinnedIMAP4SSL` / `_PinnedIMAP4` open a raw socket to the IP
rather than letting `imaplib` re-resolve the hostname. The original hostname is
still passed to the TLS layer for SNI and certificate verification, so TLS
correctness isn't sacrificed for the pin:

```python
class _PinnedIMAP4SSL(imaplib.IMAP4_SSL):
    def __init__(self, host, port, pinned_ip, timeout):
        self._pinned_ip = pinned_ip
        super().__init__(host, port, timeout=timeout)

    def _create_socket(self, timeout=None):
        return socket.create_connection((self._pinned_ip, self.port), timeout)
```

### 3. Sync-time re-validation

An account's host can be valid at creation time and later start resolving to an
internal address (the operator's DNS changes, or a deliberately delayed rebind).
`sync_worker` re-runs `validate_host_not_internal(account.imap_host)` immediately
before every sync, and fails the job (`sync_state = error`) rather than invoking
mbsync if the host now resolves internally.

!!! note "Documented residual: mbsync's own resolution"
    The sync-time check and the moment mbsync itself opens a connection are not
    atomic — mbsync (an external binary, not `imap_check`'s pinned-IP path)
    re-resolves the hostname when it connects. A rebind timed precisely between
    MFB's check and mbsync's own DNS lookup is a narrow, bounded TOCTOU window
    that isn't closed without pinning mbsync's connection too, which would give
    up TLS hostname verification against the real target. This is accepted as a
    residual risk, not treated as fixed.

## Read-only IMAP via Dovecot ACL

MFB's Dovecot configuration is not hand-maintained — it's generated at container
boot by `src/mailfallback/services/config_generator.py::generate_dovecot_config`
and written into the `dovecot_confd` volume that the Dovecot container mounts
`conf.d` from. This makes the ACL policy part of MFB's own deployed state rather
than something an operator could drift out of sync with the data model.

The generator writes a global ACL (`acl_globals_only = yes`,
`acl_global_path = /etc/dovecot/conf.d/dovecot-acl`) whose default rule grants
the mailbox owner only:

- `l` — lookup (see the mailbox exists)
- `r` — read (fetch messages)
- `s` — write-seen (mark read/unread)

No insert, expunge, flag-write, or mailbox create/delete rights are granted by
default — a logged-in IMAP user (Roundcube or any other client against Dovecot)
cannot delete, modify, or add to a synced mailbox's replica.

!!! note "Staging is the one deliberate exception"
    The generated ACL file also grants the per-user `Staging` namespace broader
    rights (`lrwstie`: lookup/read/write-flags/write-seen/write-deleted/insert/expunge).
    `Staging` is the restore workflow's curation surface — where a user reviews
    and deletes-before-push a set of recovered messages prior to pushing them to
    a live account — and is never populated by mbsync or included in the
    always-on local backup semantics. Every other namespace stays `lrs`-only.

mbsync is unaffected by any of this: it writes to the Maildir filesystem
directly on the host, bypassing Dovecot and its ACL layer entirely. The ACL only
constrains IMAP clients that connect *through* Dovecot.

## Multi-tenant isolation

MFB is multi-user: one deployment can host many accounts owned by different
users, and users must not be able to see or act on accounts they don't own.
Ownership is modeled two ways, both checked by `account_service`:

- **Direct ownership** — the `account_owners` join table (many-to-many: an
  account can have several owners, a user can own several accounts).
- **Group membership** — `account_groups` + `group_members`; a group's accounts
  are visible to all of its members without being individually owned by them.

MFB enforces multi-tenant isolation through two access-control gates in
`account_service`:

- **`get_account`** (read/shared-access gate): returns the account for admins,
  direct owners, *or* group members. Used for read-only and operational routes
  (account detail, sync status, snapshots, backup history).
- **`get_account_for_modify`** (write gate): returns the account for admins and
  *direct* owners only — not group members. Used for account settings changes
  (name, IMAP host, credentials, sync schedule).

This distinction means:

- **Account settings** (edit IMAP host, change sync schedule, etc.) require
  direct ownership or admin role. Group members have no write access to account
  configuration.
- **Backup/restore/recovery operations** (configure off-site policy, back up now,
  restore a snapshot or attachment, delete a recovery) are intentionally
  available to group members via `get_account` — shared group access grants
  operational access to an account's off-site backup surface. This is by design.
- **Account ownership** itself is admin-only (`require_admin` on
  `/accounts/{id}/owners` endpoints), so users cannot self-grant or self-revoke
  ownership.

All these endpoints are account-scoped: routes that call `get_account` or
`get_account_for_modify` return 404 rather than leaking whether an account ID
exists to an unauthorized user. Before touching any account's data, the endpoint
resolves it for the requesting user. One tenant therefore cannot reach another's
mailbox, snapshots, or recoveries by guessing an account ID (an IDOR guard).

## OAuth CSRF protection

Google and Microsoft account linking uses the OAuth2 authorization-code flow
(`routers/auth.py`). Before redirecting to the provider, MFB generates a random
`state` nonce and stores it server-side in the session
(`request.session["oauth_state"] = nonce`). On the callback, the `state`
returned by the provider is compared against the stored value **before any
destructive action is taken**:

```python
expected_state = request.session.pop("oauth_state", None)
# Validate state BEFORE any destructive action (see google callback).
if not state or not expected_state or state != expected_state:
    ...(db, request, account_id, reason="invalid_state", delete_stub=False)
```

Both the Google and Microsoft callbacks follow this order deliberately: on a
state mismatch, the pending account stub is *not* deleted
(`delete_stub=False`). Without this check, a forged cross-site callback (an
attacker driving a victim's browser to MFB's callback URL with an
attacker-chosen `code`) could otherwise trigger cleanup of the victim's
in-progress account-linking state. Only a genuine, state-validated failure
proceeds to clean up the stub account.

## Reserved `_restore_` username prefix

Restoring a snapshot back into a live mailbox (IMAP→IMAP `RestoreJob`) works by
creating a short-lived Dovecot/IMAP user scoped to the accounts being restored,
so restic/Tika-adjacent tooling can push through the normal IMAP surface rather
than needing direct filesystem access. `src/mailfallback/services/dovecot_auth.py`
reserves the prefix:

```python
TEMP_USER_PREFIX = "_restore_"
```

`create_temp_imap_user` names every temporary user `_restore_{random hex}`, and
`delete_temp_imap_user` / `cleanup_temp_imap_users` — the latter run
periodically to sweep temp users older than one hour — only ever operate on
usernames matching that prefix:

```python
def delete_temp_imap_user(db: Session, username: str) -> None:
    if not username.startswith(TEMP_USER_PREFIX):
        return
    ...
```

Because deletion is gated on the prefix rather than on "the user this restore
job created," the prefix has to stay unreachable from every other user-creation
path, or cleanup could delete a real user by mistaking it for an expired temp
user. Both paths that create users from untrusted input enforce this
explicitly:

- `user_service.create_user` (admin-created users) raises `ValueError` if the
  requested username starts with `_restore_`, refusing creation outright.
- The OIDC login path (`routers/auth.py`) derives a username from the identity
  provider's claims and rewrites it — prefixing with `u` — if it happens to
  start with `_restore_`, rather than rejecting the login:

  ```python
  # Never let an IdP-supplied name collide with the reserved restore-user
  # prefix -- cleanup_temp_imap_users would later delete it as an orphan.
  if username.startswith(TEMP_USER_PREFIX):
      username = f"u{username}"
  ```
