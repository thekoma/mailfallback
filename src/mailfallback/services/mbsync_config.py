import json
import re


def _sanitize_value(value: str) -> str:
    return re.sub(r"[\n\r\x00-\x1f]", "", str(value)).strip()


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
    safe_name = account_name.lower().replace(" ", "_")
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
