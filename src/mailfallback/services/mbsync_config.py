import json
import re


def _sanitize_value(value: str) -> str:
    return re.sub(r"[\n\r\x00-\x1f]", "", str(value)).strip()


def channel_name(account_name: str) -> str:
    """The Channel name generate_mbsyncrc derives from the account name.

    Exported for the sync worker's priority pass (`mbsync <channel>:INBOX`)
    — it must target EXACTLY the channel the generated config declares.
    """
    return _sanitize_value(account_name).lower().replace(" ", "_")


# Tokens of a Patterns string: optionally negated, optionally quoted.
_PATTERN_TOKEN_RE = re.compile(r'!?"[^"]*"|[^\s"]+')


def excluded_folder_names(patterns: str) -> list[str]:
    """The !-negations of a channel Patterns string, unquoted.

    Exported for the worker's upstream STATUS pass: the initial-sync
    progress denominator must count only folders mbsync will actually
    sync. Handles quoted (!"[Gmail]/All Mail") and bare (!Spam) negations;
    glob negations are returned verbatim (callers match with fnmatch).
    """
    excluded: list[str] = []
    for token in _PATTERN_TOKEN_RE.findall(patterns or ""):
        if not token.startswith("!"):
            continue
        name = token[1:]
        if len(name) >= 2 and name.startswith('"') and name.endswith('"'):
            name = name[1:-1]
        if name:
            excluded.append(name)
    return excluded


def generate_mbsyncrc(
    account_name: str,
    imap_host: str,
    imap_port: int,
    username: str,
    auth_type: str,
    maildir_path: str,
    tls_type: str = "IMAPS",
    password: str | None = None,
    token_command: str | None = None,
    extra_config: str | None = None,
) -> str:
    raw_extra = json.loads(extra_config) if extra_config else {}
    extra = {k: _sanitize_value(v) for k, v in raw_extra.items() if v}
    account_name = _sanitize_value(account_name)
    imap_host = _sanitize_value(imap_host)
    username = _sanitize_value(username)
    maildir_path = _sanitize_value(maildir_path)
    tls_type = _sanitize_value(tls_type)
    if password:
        password = _sanitize_value(password)
    if token_command:
        token_command = _sanitize_value(token_command)
    safe_name = channel_name(account_name)  # single source with the worker
    lines = []

    lines.append(f"IMAPAccount {safe_name}")
    lines.append(f"Host {imap_host}")
    lines.append(f"Port {imap_port}")
    lines.append(f"User {username}")

    if tls_type and tls_type != "None":
        lines.append(f"TLSType {tls_type}")
    if extra.get("tls_versions"):
        lines.append(f"TLSVersions {extra['tls_versions']}")
    lines.append(
        f"CertificateFile {extra.get('certificate_file', '/etc/ssl/certs/ca-certificates.crt')}"
    )
    if extra.get("client_certificate"):
        lines.append(f"ClientCertificate {extra['client_certificate']}")
    if extra.get("client_key"):
        lines.append(f"ClientKey {extra['client_key']}")

    if auth_type == "oauth2":
        lines.append("AuthMechs XOAUTH2")
        lines.append(f'PassCmd "{token_command}"')
    else:
        auth_mechs = extra.get("auth_mechs", "")
        if auth_mechs:
            lines.append(f"AuthMechs {auth_mechs}")
        if password:
            lines.append(f'Pass "{password}"')

    if extra.get("timeout"):
        lines.append(f"Timeout {extra['timeout']}")
    if extra.get("pipeline_depth"):
        lines.append(f"PipelineDepth {extra['pipeline_depth']}")
    if extra.get("disable_extensions"):
        lines.append(f"DisableExtensions {extra['disable_extensions']}")

    lines.append("")
    lines.append(f"IMAPStore {safe_name}-remote")
    lines.append(f"Account {safe_name}")
    if "use_namespace" in extra:
        lines.append(f"UseNamespace {'yes' if extra['use_namespace'] else 'no'}")
    if extra.get("path_delimiter"):
        lines.append(f"PathDelimiter {extra['path_delimiter']}")

    lines.append("")
    lines.append(f"MaildirStore {safe_name}-local")
    lines.append(f"Path {maildir_path.rstrip('/')}/")
    lines.append(f"Inbox {maildir_path.rstrip('/')}/INBOX")
    lines.append("SubFolders Verbatim")

    sync = extra.get("sync", "Pull")
    create = extra.get("create", "Near")
    expunge = extra.get("expunge", "None")
    patterns = extra.get("patterns", "*")

    lines.append("")
    lines.append(f"Channel {safe_name}")
    lines.append(f"Far :{safe_name}-remote:")
    lines.append(f"Near :{safe_name}-local:")
    lines.append(f"Patterns {patterns}")
    lines.append(f"Sync {sync}")
    lines.append(f"Create {create}")
    lines.append(f"Expunge {expunge}")
    if extra.get("max_messages"):
        lines.append(f"MaxMessages {extra['max_messages']}")
    if extra.get("max_size"):
        lines.append(f"MaxSize {extra['max_size']}")
    if extra.get("copy_arrival_date"):
        lines.append("CopyArrivalDate yes")
    lines.append("SyncState *")

    return "\n".join(lines) + "\n"
