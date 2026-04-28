# src/mailfallback/services/dovecot_config.py


def generate_dovecot_userdb(accounts_by_user: dict[str, list[dict]]) -> str:
    lines = []
    for username, accounts in accounts_by_user.items():
        if not accounts:
            continue

        if len(accounts) == 1:
            maildir = accounts[0]["maildir_path"].rstrip("/")
            lines.append(f"{username}::::::userdb_mail=maildir:{maildir}")
        else:
            first_maildir = accounts[0]["maildir_path"].rstrip("/")
            namespace_parts = []
            for acc in accounts:
                maildir = acc["maildir_path"].rstrip("/")
                namespace_parts.append(
                    f"namespace/{acc['name']}/prefix={acc['name']}/"
                    f":namespace/{acc['name']}/location=maildir:{maildir}"
                )
            namespaces = " ".join(namespace_parts)
            lines.append(f"{username}::::::userdb_mail=maildir:{first_maildir} {namespaces}")

    return "\n".join(lines)


def generate_dovecot_passdb(users: list[dict[str, str]]) -> str:
    lines = []
    for user in users:
        lines.append(f"{user['username']}:{user['password_hash']}")
    return "\n".join(lines)
