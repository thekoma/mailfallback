import contextlib
import imaplib
import ipaddress
import socket

# 100.64.0.0/10 (RFC 6598 carrier-grade NAT) is NOT covered by
# ipaddress.is_private, so check it explicitly.
_CGNAT_V4 = ipaddress.ip_network("100.64.0.0/10")


def _is_internal_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Unwrap IPv4-mapped IPv6 (::ffff:a.b.c.d) so an attacker-controlled AAAA
    # record can't smuggle an internal IPv4 past the checks; also makes the
    # classification independent of per-version is_private unwrapping quirks.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or (ip.version == 4 and ip in _CGNAT_V4)
    )


def validate_host_not_internal(host: str) -> None:
    """Reject connections to private/loopback/link-local/CGNAT addresses.

    Raises ValueError if the host resolves to an internal address OR cannot be
    resolved at all — an unresolvable host must be rejected, not waved through
    (a swallowed gaierror was an SSRF bypass: the later connection re-resolves).
    """
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError(f"Host could not be resolved: {host}") from e
    if not infos:
        raise ValueError(f"Host could not be resolved: {host}")
    for _family, _, _, _, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if _is_internal_ip(ip):
            raise ValueError(f"Connections to private/internal addresses are not allowed: {host}")


def resolve_public_ip(host: str, port: int) -> str:
    """Resolve host, reject internal addresses, and return one pinned public IP.

    Connecting to this pinned IP (with the original hostname kept for TLS SNI)
    defeats DNS rebinding: validation and connection can't diverge because they
    no longer perform independent lookups.
    """
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError(f"Host could not be resolved: {host}") from e
    if not infos:
        raise ValueError(f"Host could not be resolved: {host}")
    for _family, _, _, _, sockaddr in infos:
        if _is_internal_ip(ipaddress.ip_address(sockaddr[0])):
            raise ValueError(f"Connections to private/internal addresses are not allowed: {host}")
    return infos[0][4][0]


class _PinnedIMAP4SSL(imaplib.IMAP4_SSL):
    """IMAP4_SSL that connects to a pre-resolved IP while keeping the hostname
    for SNI / certificate verification (rebinding-safe)."""

    def __init__(self, host, port, pinned_ip, timeout):
        self._pinned_ip = pinned_ip
        super().__init__(host, port, timeout=timeout)

    def _create_socket(self, timeout=None):
        return socket.create_connection((self._pinned_ip, self.port), timeout)


class _PinnedIMAP4(imaplib.IMAP4):
    """Plain/STARTTLS twin of _PinnedIMAP4SSL."""

    def __init__(self, host, port, pinned_ip, timeout):
        self._pinned_ip = pinned_ip
        super().__init__(host, port, timeout=timeout)

    def _create_socket(self, timeout=None):
        return socket.create_connection((self._pinned_ip, self.port), timeout)


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
    pin_public_ip: bool = False,
) -> imaplib.IMAP4:
    """Open an IMAP connection and authenticate.

    auth_method "login" sends plain LOGIN; "xoauth2" sends AUTHENTICATE
    XOAUTH2 with `password` as the OAuth2 access token — Gmail/Microsoft
    reject access tokens via LOGIN ([AUTHENTICATIONFAILED]).

    pin_public_ip: for untrusted, user-supplied hosts (test-connection, account
    create/edit) — resolve+validate once and connect to that pinned IP, keeping
    `host` for TLS SNI, so DNS rebinding can't swap in an internal address
    between validation and connection.
    """
    pinned_ip = resolve_public_ip(host, port) if pin_public_ip else None
    if tls_type == "IMAPS":
        if pinned_ip:
            conn = _PinnedIMAP4SSL(host, port, pinned_ip, timeout=timeout)
        else:
            conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    elif tls_type == "STARTTLS":
        conn = (
            _PinnedIMAP4(host, port, pinned_ip, timeout=timeout)
            if pinned_ip
            else imaplib.IMAP4(host, port, timeout=timeout)
        )
        conn.starttls()
    else:
        conn = (
            _PinnedIMAP4(host, port, pinned_ip, timeout=timeout)
            if pinned_ip
            else imaplib.IMAP4(host, port, timeout=timeout)
        )
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
    pin_public_ip: bool = False,
) -> dict:
    try:
        conn = connect_imap(host, port, tls_type, timeout=10, pin_public_ip=pin_public_ip)
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
    except (imaplib.IMAP4.error, TimeoutError, OSError, ValueError) as e:
        # ValueError: rebinding detected at connect time (resolve_public_ip).
        return {"ok": False, "message": str(e)}


def _extract_auth_capabilities(conn: imaplib.IMAP4) -> list[str]:
    with contextlib.suppress(Exception):
        typ, data = conn.capability()
        if typ == "OK" and data:
            caps = data[0].decode().split()
            return sorted(c.removeprefix("AUTH=") for c in caps if c.startswith("AUTH="))
    return []
