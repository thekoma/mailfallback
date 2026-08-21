"""Access-token lifecycle and verification.

The token is ``mfb_<prefix>_<secret>``. The prefix is an indexed lookup key so
verification is one row fetch; the secret is checked against a keyed HMAC. The
secret is returned exactly once, at creation, and is unrecoverable afterwards.
"""

import enum
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import AppCredential, User
from mailfallback.security import hash_token, verify_token

logger = logging.getLogger(__name__)

TOKEN_MARKER = "mfb_"
# Hex, not secrets.token_urlsafe: the urlsafe-base64 alphabet includes "_",
# which is also the token's field separator, so a generated prefix could
# collide with the delimiter and corrupt the prefix/secret split. Hex keeps
# the same byte-entropy with an alphabet that can never contain "_".
_PREFIX_BYTES = 9  # 18 hex characters
_SECRET_BYTES = 32  # 256 bits — the reason HMAC beats bcrypt here

SCOPE_IMAP = "imap"
SCOPE_MAIL_READ = "mail:read"
SCOPE_SYNC_TRIGGER = "sync:trigger"
VALID_SCOPES = frozenset({SCOPE_IMAP, SCOPE_MAIL_READ, SCOPE_SYNC_TRIGGER})


class VerifyResult(enum.StrEnum):
    """Verification outcome, mapped by callers onto their own protocol.

    ``not_a_token`` and ``unknown`` are deliberately distinct from
    ``rejected``: the Dovecot passdb turns the first two into
    PASSDB_RESULT_NEXT so the password falls through to the SQL passdb, and only
    ``rejected`` into PASSDB_RESULT_PASSWORD_MISMATCH.
    """

    ok = "ok"
    not_a_token = "not_a_token"
    unknown = "unknown"
    rejected = "rejected"


def create_credential(
    db: Session,
    user: User,
    *,
    name: str,
    scopes: list[str],
    ttl_days: int | None = None,
) -> tuple[AppCredential, str]:
    """Create a credential and return it with its one-time token."""
    if not scopes:
        raise ValueError("At least one scope is required")
    unknown = sorted(set(scopes) - VALID_SCOPES)
    if unknown:
        raise ValueError(f"Unknown scope(s): {', '.join(unknown)}")

    prefix = secrets.token_hex(_PREFIX_BYTES)
    secret = secrets.token_hex(_SECRET_BYTES)
    cred = AppCredential(
        user_id=user.id,
        name=name,
        token_prefix=prefix,
        secret_hash=hash_token(secret, settings.secret_key),
        scopes=",".join(sorted(set(scopes))),
        expires_at=datetime.now(UTC) + timedelta(days=ttl_days) if ttl_days else None,
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    return cred, f"{TOKEN_MARKER}{prefix}_{secret}"


def list_credentials(db: Session, user: User) -> list[AppCredential]:
    return (
        db.query(AppCredential)
        .filter(AppCredential.user_id == user.id)
        .order_by(AppCredential.created_at.desc())
        .all()
    )


def revoke_credential(db: Session, user: User, credential_id: str) -> bool:
    """Mark a credential revoked. False if it is not this user's."""
    cred = (
        db.query(AppCredential)
        .filter(AppCredential.id == credential_id, AppCredential.user_id == user.id)
        .first()
    )
    if cred is None:
        return False
    if cred.revoked_at is None:
        cred.revoked_at = datetime.now(UTC)
        db.commit()
    return True


def _split(token: str) -> tuple[str, str] | None:
    """('prefix', 'secret') for a well-formed token, else None."""
    if not token or not token.startswith(TOKEN_MARKER):
        return None
    rest = token[len(TOKEN_MARKER) :]
    prefix, _, secret = rest.partition("_")
    if not prefix or not secret:
        return None
    return prefix, secret


def verify_credential(
    db: Session,
    *,
    username: str | None,
    token: str,
    required_scope: str | None,
    kind: str,
) -> tuple[VerifyResult, AppCredential | None]:
    """Verify a token and record the usage on success.

    ``username`` is the identity the caller already has in hand, and the token
    must belong to it — that is the Dovecot passdb's situation, where IMAP
    supplies the username separately. Pass ``None`` when the token IS the
    identity, as with an HTTP bearer request: the owner is then resolved from
    the credential and no comparison happens.

    ``required_scope`` may also be ``None``, which skips the scope check
    entirely: that is the right call for something answering "who is this",
    not "may they do that" — as `dependencies.get_current_principal` does,
    leaving the scope gate to `dependencies.require_scope`.

    Every other check — user enabled, not migrating, credential active, scope
    present, secret matches — runs identically either way. There is deliberately
    no second entry point for the username-less case: a duplicated check ladder
    is a security ladder that drifts.

    Deliberately does NOT write an audit row: an agent opens many IMAP
    connections and one row each would bury the audit log. ``last_used_at`` is
    the usage record; outcomes go to the application log, carrying the prefix
    and never the secret.
    """
    parts = _split(token)
    if parts is None:
        return VerifyResult.not_a_token, None
    prefix, secret = parts

    cred = db.query(AppCredential).filter(AppCredential.token_prefix == prefix).first()
    if cred is None:
        logger.info("Access token rejected: unknown prefix %s", prefix)
        return VerifyResult.unknown, None

    user = db.query(User).filter(User.id == cred.user_id).first()
    if user is None:
        logger.warning("Access token %s has no owning user", prefix)
        return VerifyResult.unknown, None
    if username is not None and user.username != username:
        # Not this user's token: fall through rather than reveal that the
        # prefix exists at all.
        logger.info("Access token %s does not belong to user %s", prefix, username)
        return VerifyResult.unknown, None

    if not user.enabled or user.migrating:
        logger.warning(
            "Access token %s rejected: user %s disabled or migrating", prefix, user.username
        )
        return VerifyResult.rejected, None
    if not cred.active:
        logger.warning("Access token %s rejected: revoked or expired", prefix)
        return VerifyResult.rejected, None
    if required_scope is not None and required_scope not in cred.scope_set:
        logger.warning(
            "Access token %s rejected: missing scope %s (has %s)",
            prefix,
            required_scope,
            cred.scopes,
        )
        return VerifyResult.rejected, None
    if not verify_token(secret, cred.secret_hash, settings.secret_key):
        logger.warning("Access token %s rejected: secret mismatch", prefix)
        return VerifyResult.rejected, None

    cred.last_used_at = datetime.now(UTC)
    cred.last_used_kind = kind
    db.commit()
    logger.info("Access token %s authenticated %s for %s", prefix, kind, user.username)
    return VerifyResult.ok, cred
