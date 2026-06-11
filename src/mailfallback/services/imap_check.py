import contextlib
import imaplib
import ipaddress
import socket


def validate_host_not_internal(host: str) -> None:
    """Reject connections to private/loopback/link-local addresses."""
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        return  # let the actual connection fail with a proper error
    for _family, _, _, _, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"Connections to private/internal addresses are not allowed: {host}")


def _xoauth2_authobject(username: str, access_token: str):
    """Build an imaplib authobject for SASL XOAUTH2 (Gmail / Microsoft 365).

    Returns the raw SASL string for every challenge (imaplib base64-encodes
    it); answering a re-challenge — e.g. Gmail's base64 error blob — with the
    same string lets the exchange terminate with a proper NO response.
    """
    sasl = f"user={username}\x01auth=Bearer {access_token}\x01\x01".encode()

    def _respond(_challenge: bytes | None) -> bytes:
        return sasl

    return _respond


def connect_imap(
    host: str,
    port: int = 993,
    tls_type: str = "IMAPS",
    username: str | None = None,
    password: str | None = None,
    timeout: int = 30,
    auth_method: str = "login",
) -> imaplib.IMAP4:
    """Open an IMAP connection and authenticate.

    auth_method "login" sends plain LOGIN; "xoauth2" sends AUTHENTICATE
    XOAUTH2 with `password` as the OAuth2 access token — Gmail/Microsoft
    reject access tokens via LOGIN ([AUTHENTICATIONFAILED]).
    """
    if tls_type == "IMAPS":
        conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    elif tls_type == "STARTTLS":
        conn = imaplib.IMAP4(host, port, timeout=timeout)
        conn.starttls()
    else:
        conn = imaplib.IMAP4(host, port, timeout=timeout)
    if username and password:
        if auth_method == "xoauth2":
            conn.authenticate("XOAUTH2", _xoauth2_authobject(username, password))
        else:
            conn.login(username, password)
    return conn


def check_imap_credentials(
    host: str,
    port: int = 993,
    tls_type: str = "IMAPS",
    username: str | None = None,
    password: str | None = None,
) -> dict:
    try:
        conn = connect_imap(host, port, tls_type, timeout=10)
        greeting = conn.welcome.decode() if conn.welcome else "OK"

        auth_mechs = _extract_auth_capabilities(conn)

        login_ok = None
        login_message = None
        if username and password:
            try:
                conn.login(username, password)
                login_ok = True
                login_message = "Login successful"
            except imaplib.IMAP4.error as e:
                login_ok = False
                login_message = str(e)

        conn.logout()
        return {
            "ok": True,
            "message": greeting,
            "auth_mechs": auth_mechs,
            "login_ok": login_ok,
            "login_message": login_message,
        }
    except (imaplib.IMAP4.error, TimeoutError, OSError) as e:
        return {"ok": False, "message": str(e)}


def _extract_auth_capabilities(conn: imaplib.IMAP4) -> list[str]:
    with contextlib.suppress(Exception):
        typ, data = conn.capability()
        if typ == "OK" and data:
            caps = data[0].decode().split()
            return sorted(c.removeprefix("AUTH=") for c in caps if c.startswith("AUTH="))
    return []
