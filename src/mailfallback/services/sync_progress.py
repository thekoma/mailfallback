"""mbsync output parser and progress tracking data model.

Pure-function parser that converts raw mbsync log lines into a structured
ProgressSnapshot. Stateless — call with all accumulated lines each time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class FolderProgress:
    name: str
    phase: Literal["opening", "loading", "pulling", "done"] = "opening"
    near: int | None = None
    far: int | None = None
    added_done: int = 0
    added_total: int = 0
    flagged_done: int = 0
    flagged_total: int = 0
    expunged_done: int = 0
    expunged_total: int = 0


@dataclass
class ParsedError:
    at_line: int
    category: Literal["auth", "network", "disk", "rate_limit", "tls", "config", "server", "unknown"]
    user_message: str
    technical_detail: str
    actionable: bool = False
    action: Literal["reauth", "retry", "admin", "none"] | None = None


@dataclass
class SyncEvent:
    line_number: int
    event_type: str
    detail: str


@dataclass
class ProgressSnapshot:
    phase: Literal[
        "queued",
        "starting",
        "connecting",
        "authenticating",
        "listing",
        "syncing",
        "finalizing",
        "done",
        "error",
    ] = "queued"
    current_channel: str | None = None
    current_folder: str | None = None
    connection_host: str | None = None
    connection_ip: str | None = None
    connection_port: int | None = None
    auth_method: str | None = None
    tls_info: str | None = None
    folder_index: int = 0
    folder_total_estimate: int | None = None
    folder_total_estimate_source: Literal["previous_sync", "live_count", "summary"] | None = None
    per_folder: list[FolderProgress] = field(default_factory=list)
    events: list[SyncEvent] = field(default_factory=list)
    errors: list[ParsedError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: dict | None = None
    mbsync_version: str | None = None
    raw_tail: list[str] = field(default_factory=list)


# --- Compiled regexes (module-level for reuse) ---

_RE_VERSION = re.compile(r"^isync (\S+)")
_RE_CHANNEL = re.compile(r"^Channel (\S+)")
_RE_RESOLVING = re.compile(r"^Resolving (\S+)")
_RE_CONNECTING = re.compile(r"^Connecting to (\S+) \((.+):(\d+)\)")
_RE_TLS = re.compile(r"Connection is now encrypted")
_RE_AUTH_SASL = re.compile(r"Authenticating with SASL mechanism (\w+)")
_RE_AUTH_LOGIN = re.compile(r"Logging in as \S+ \((\S+)\)")
_RE_FOLDER_OPEN = re.compile(r"^Opening far side box (.+)\.\.\.")
_RE_NEAR_COUNT = re.compile(r"near side: (\d+) messages?, (\d+) recent")
_RE_FAR_COUNT = re.compile(r"far side: (\d+) messages?, (\d+) recent")
_RE_SYNCHRONIZING = re.compile(r"^Synchronizing\.\.\.")
_RE_PROGRESS_F = re.compile(r"^F: \+(\d+)/(\d+) \*(\d+)/(\d+) #(\d+)/(\d+)")
_RE_PROGRESS_N = re.compile(r"^N: \+(\d+)/(\d+) \*(\d+)/(\d+) #(\d+)/(\d+)")
_RE_PULLING = re.compile(r"Pulling new message (\d+)/(\d+)")
_RE_SUMMARY = re.compile(
    r"Channels:\s+(\d+)\s+Boxes:\s+(\d+)\s+"
    r"Far:\s+\+(\d+)\s+\*(\d+)\s+#(\d+)\s+-(\d+)\s+"
    r"Near:\s+\+(\d+)\s+\*(\d+)\s+#(\d+)\s+-(\d+)"
)
_RE_WARNING = re.compile(r"^(?:Warning|Maildir error)[:\s]+(.*)", re.IGNORECASE)
_RE_ERROR_LINE = re.compile(r"^(?:Error|IMAP error)[:\s]+(.*)", re.IGNORECASE)

_ERROR_CATEGORIES: list[tuple[re.Pattern, str, str, str]] = [
    (
        re.compile(
            r"AUTHENTICATIONFAILED|LOGIN failed|Invalid credentials"
            r"|authentication failed",
            re.I,
        ),
        "auth",
        "Sign-in needed",
        "reauth",
    ),
    (
        re.compile(
            r"Connection refused|could not connect|connection timed out"
            r"|getaddrinfo|Name or service not known",
            re.I,
        ),
        "network",
        "Server unreachable",
        "retry",
    ),
    (
        re.compile(r"SSL|TLS error|certificate|handshake", re.I),
        "tls",
        "Connection failed (encryption issue)",
        "retry",
    ),
    (
        re.compile(
            r"Permission denied|No space|disk full|I/O error|read-only",
            re.I,
        ),
        "disk",
        "Storage error",
        "admin",
    ),
    (
        re.compile(r"Too many connections|OVERQUOTA|throttl", re.I),
        "rate_limit",
        "Rate limited",
        "retry",
    ),
    (
        re.compile(r"not configured|configuration|syntax error", re.I),
        "config",
        "Configuration error",
        "admin",
    ),
    (
        re.compile(r"protocol error|mailbox .* unavailable|BYE .* closing", re.I),
        "server",
        "Server error",
        "admin",
    ),
]


def _classify_error(text: str) -> tuple[str, str, str]:
    for pattern, category, user_msg, action in _ERROR_CATEGORIES:
        if pattern.search(text):
            return category, user_msg, action
    return "unknown", "Backup failed — unknown error", "none"


def parse_mbsync_lines(
    lines: list[str],
    prior_folder_count: int | None = None,
) -> ProgressSnapshot:
    """Parse mbsync output lines into a structured progress snapshot.

    Args:
        lines: Raw mbsync output lines (stripped of trailing newlines).
        prior_folder_count: Folder count from last successful sync, used
            as estimate when total isn't yet known.
    """
    snap = ProgressSnapshot()
    snap.raw_tail = lines[-20:] if lines else []

    if not lines:
        return snap

    snap.phase = "starting"
    current_folder: FolderProgress | None = None
    folder_by_name: dict[str, FolderProgress] = {}
    seen_synchronizing = False

    if prior_folder_count is not None:
        snap.folder_total_estimate = prior_folder_count
        snap.folder_total_estimate_source = "previous_sync"

    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue

        m = _RE_VERSION.match(line)
        if m:
            snap.mbsync_version = m.group(1)
            continue

        m = _RE_CHANNEL.match(line)
        if m:
            snap.current_channel = m.group(1)
            continue

        m = _RE_RESOLVING.match(line)
        if m:
            snap.connection_host = m.group(1).rstrip(".")
            snap.phase = "connecting"
            continue

        m = _RE_CONNECTING.match(line)
        if m:
            snap.connection_host = m.group(1)
            snap.connection_ip = m.group(2)
            snap.connection_port = int(m.group(3))
            snap.phase = "connecting"
            continue

        if _RE_TLS.search(line):
            snap.tls_info = "encrypted"
            continue

        m = _RE_AUTH_SASL.search(line)
        if m:
            snap.auth_method = m.group(1)
            snap.phase = "authenticating"
            continue

        m = _RE_AUTH_LOGIN.search(line)
        if m:
            snap.auth_method = m.group(1)
            snap.phase = "authenticating"
            continue

        if line == "Logging in...":
            snap.phase = "authenticating"
            continue

        m = _RE_FOLDER_OPEN.match(line)
        if m:
            folder_name = m.group(1)
            if folder_name not in folder_by_name:
                fp = FolderProgress(name=folder_name)
                folder_by_name[folder_name] = fp
                snap.per_folder.append(fp)
            current_folder = folder_by_name[folder_name]
            current_folder.phase = "loading"
            snap.current_folder = folder_name
            snap.folder_index = len(snap.per_folder)
            snap.phase = "syncing" if seen_synchronizing else "listing"
            continue

        m = _RE_FAR_COUNT.search(line)
        if m and current_folder:
            current_folder.far = int(m.group(1))
            continue

        m = _RE_NEAR_COUNT.search(line)
        if m and current_folder:
            current_folder.near = int(m.group(1))
            continue

        if _RE_SYNCHRONIZING.match(line):
            if current_folder:
                current_folder.phase = "done"
            seen_synchronizing = True
            snap.phase = "syncing"
            continue

        m = _RE_PROGRESS_F.match(line)
        if m and current_folder:
            current_folder.added_done = int(m.group(1))
            current_folder.added_total = int(m.group(2))
            current_folder.flagged_done = int(m.group(3))
            current_folder.flagged_total = int(m.group(4))
            current_folder.expunged_done = int(m.group(5))
            current_folder.expunged_total = int(m.group(6))
            current_folder.phase = "pulling"
            continue

        m = _RE_PROGRESS_N.match(line)
        if m and current_folder:
            near_added = int(m.group(1))
            near_total = int(m.group(2))
            if near_added > current_folder.added_done:
                current_folder.added_done = near_added
                current_folder.added_total = near_total
            continue

        m = _RE_PULLING.search(line)
        if m and current_folder:
            current_folder.added_done = int(m.group(1))
            current_folder.added_total = int(m.group(2))
            current_folder.phase = "pulling"
            continue

        m = _RE_SUMMARY.search(line)
        if m:
            snap.summary = {
                "channels": int(m.group(1)),
                "boxes": int(m.group(2)),
                "far_added": int(m.group(3)),
                "far_flagged": int(m.group(4)),
                "far_expunged": int(m.group(5)),
                "far_deleted": int(m.group(6)),
                "near_added": int(m.group(7)),
                "near_flagged": int(m.group(8)),
                "near_expunged": int(m.group(9)),
                "near_deleted": int(m.group(10)),
            }
            snap.folder_total_estimate = int(m.group(2))
            snap.folder_total_estimate_source = "summary"
            snap.phase = "done"
            continue

        m = _RE_WARNING.match(line)
        if m:
            snap.warnings.append(m.group(1))
            continue

        m = _RE_ERROR_LINE.match(line)
        if m:
            error_text = m.group(1)
            cat, user_msg, action = _classify_error(error_text)
            snap.errors.append(
                ParsedError(
                    at_line=i,
                    category=cat,
                    user_message=user_msg,
                    technical_detail=line,
                    actionable=action != "none",
                    action=action,
                )
            )
            if snap.phase != "done":
                snap.phase = "error"
            continue

    return snap
