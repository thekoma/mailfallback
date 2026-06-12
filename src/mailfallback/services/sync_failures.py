"""Sync failure classification — throttle/transient pauses vs real errors.

Production evidence (2026-06-12, "Main gMail"): the first full sync of a
large mailbox died mid-run with the verbatim line
``IMAP error: unexpected BYE response: [OVERQUOTA] Account exceeded
command or bandwidth limits.`` — Gmail's daily bandwidth cap, not a broken
account. Design rule (sync-budget spec): only ``error`` is red in the UI;
``throttled``/``transient`` are self-recovering states the worker pauses
and the scheduler resumes with backoff.

Pure stdlib module (no app imports) — consumed by the sync worker.
"""

# Network blips that a short backoff fixes. Prose — matched
# case-insensitively.
TRANSIENT_SIGNATURES: tuple[str, ...] = (
    "Connection reset",
    "unexpected EOF",
    "Broken pipe",
    # No static "timed out" string exists in the isync 1.5.1 binary — kept
    # for OS-level strerror text (ETIMEDOUT → "Connection timed out") and
    # the worker's own "Sync timed out after 3600 seconds" log line.
    "timed out",
    # ANCHORED (review): isync's real timeout messages are
    # "Socket error on %s: timeout." and
    # "Error: Cannot resolve server '%s': timeout." — both end in
    # ": timeout". A bare "timeout" false-positives on benign content in
    # full -Dm logs (a folder named "Timeouts" would classify a real
    # failure as transient forever — endless short-backoff churn).
    ": timeout",
)

# Provider-specific throttle tells, keyed by Account.provider. Bracketed
# entries are IMAP response codes (case-sensitive protocol tokens); the
# rest is prose (case-insensitive).
THROTTLE_SIGNATURES: dict[str, tuple[str, ...]] = {
    "google": ("[OVERQUOTA]", "Too many simultaneous connections"),
    "microsoft": ("Request is throttled", "[THROTTLED]", "server busy"),
    "other": (),
}

# Bracketed response codes ANY provider can emit — checked for every
# provider, known or not.
GENERIC_THROTTLE: tuple[str, ...] = ("[OVERQUOTA]", "[THROTTLED]")


def _matches(log_tail: str, signature: str) -> bool:
    """Bracketed response codes match case-sensitively (protocol tokens);
    prose signatures match case-insensitively."""
    if signature.startswith("["):
        return signature in log_tail
    return signature.lower() in log_tail.lower()


def classify_failure(log_tail: str, provider: str) -> str | None:
    """Classify a failed sync from its log tail.

    Returns ``"throttled"`` | ``"transient"`` | ``None`` (= real error,
    today's red path). Scans whatever string it is given — the caller
    passes the tail of the log.

    Precedence: throttle (provider-specific OR generic bracketed code)
    wins over transient — a throttled session usually dies with a
    network-looking tail right after the BYE. Unknown provider keys still
    get the generic + transient checks (``.get``, never ``KeyError``).
    """
    if not log_tail:
        return None
    for signature in THROTTLE_SIGNATURES.get(provider, ()) + GENERIC_THROTTLE:
        if _matches(log_tail, signature):
            return "throttled"
    for signature in TRANSIENT_SIGNATURES:
        if _matches(log_tail, signature):
            return "transient"
    return None
