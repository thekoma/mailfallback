# src/mailfallback/services/mbsync_config.py


def generate_mbsyncrc(
    account_name: str,
    imap_host: str,
    imap_port: int,
    username: str,
    auth_type: str,
    maildir_path: str,
    password: str | None = None,
    token_command: str | None = None,
) -> str:
    maildir = maildir_path.rstrip("/") + "/"
    lines = []

    lines.append(f"IMAPStore {account_name}-remote")
    lines.append(f"Host {imap_host}")
    lines.append(f"Port {imap_port}")
    lines.append(f"User {username}")
    lines.append("SSLType IMAPS")
    lines.append("CertificateFile /etc/ssl/certs/ca-certificates.crt")

    if auth_type == "oauth2":
        lines.append("AuthMechs XOAUTH2")
        lines.append(f'PassCmd "{token_command}"')
    else:
        lines.append(f'Pass "{password}"')

    lines.append("")
    lines.append(f"MaildirStore {account_name}-local")
    lines.append(f"Path {maildir}")
    lines.append(f"Inbox {maildir}INBOX")
    lines.append("SubFolders Verbatim")

    lines.append("")
    lines.append(f"Channel {account_name}")
    lines.append(f"Far :{account_name}-remote:")
    lines.append(f"Near :{account_name}-local:")
    lines.append("Patterns *")
    lines.append("Create Near")
    lines.append("Expunge None")
    lines.append("SyncState *")

    return "\n".join(lines) + "\n"
