import defusedxml.ElementTree as ET
import dns.resolver
import httpx

from mailfallback.services.imap_check import validate_host_not_internal

WELL_KNOWN_PROVIDERS = {
    "gmail.com": {
        "provider": "google",
        "name": "Gmail",
        "host": "imap.gmail.com",
        "port": 993,
        "tls": "IMAPS",
        "auth_type": "oauth2",
        "oauth_provider": "google",
        "note": None,
        "patterns": '* !"[Gmail]/All Mail" !"[Gmail]/Spam" !"[Gmail]/Trash"',
    },
    "googlemail.com": {
        "provider": "google",
        "name": "Gmail",
        "host": "imap.gmail.com",
        "port": 993,
        "tls": "IMAPS",
        "auth_type": "oauth2",
        "oauth_provider": "google",
        "note": None,
        "patterns": '* !"[Gmail]/All Mail" !"[Gmail]/Spam" !"[Gmail]/Trash"',
    },
    "outlook.com": {
        "provider": "microsoft",
        "name": "Outlook",
        "host": "outlook.office365.com",
        "port": 993,
        "tls": "IMAPS",
        "auth_type": "oauth2",
        "oauth_provider": "microsoft",
        "note": "Microsoft requires OAuth2. Basic auth deprecated Sept 2024.",
    },
    "hotmail.com": {
        "provider": "microsoft",
        "name": "Hotmail",
        "host": "outlook.office365.com",
        "port": 993,
        "tls": "IMAPS",
        "auth_type": "oauth2",
        "oauth_provider": "microsoft",
        "note": "Microsoft requires OAuth2. Basic auth deprecated Sept 2024.",
    },
    "live.com": {
        "provider": "microsoft",
        "name": "Live",
        "host": "outlook.office365.com",
        "port": 993,
        "tls": "IMAPS",
        "auth_type": "oauth2",
        "oauth_provider": "microsoft",
        "note": "Microsoft requires OAuth2. Basic auth deprecated Sept 2024.",
    },
    "live.it": {
        "provider": "microsoft",
        "name": "Live.it",
        "host": "outlook.office365.com",
        "port": 993,
        "tls": "IMAPS",
        "auth_type": "oauth2",
        "oauth_provider": "microsoft",
        "note": "Microsoft requires OAuth2. Basic auth deprecated Sept 2024.",
    },
    "yahoo.com": {
        "provider": "yahoo",
        "name": "Yahoo",
        "host": "imap.mail.yahoo.com",
        "port": 993,
        "tls": "IMAPS",
        "auth_type": "app_password",
        "note": None,
    },
    "icloud.com": {
        "provider": "icloud",
        "name": "iCloud",
        "host": "imap.mail.me.com",
        "port": 993,
        "tls": "IMAPS",
        "auth_type": "app_password",
        "note": None,
    },
    "me.com": {
        "provider": "icloud",
        "name": "iCloud",
        "host": "imap.mail.me.com",
        "port": 993,
        "tls": "IMAPS",
        "auth_type": "app_password",
        "note": None,
    },
    "protonmail.com": {
        "provider": "protonmail",
        "name": "Protonmail",
        "host": "127.0.0.1",
        "port": 1143,
        "tls": "None",
        "auth_type": "app_password",
        "note": "Proton Bridge required. Install and run it locally before syncing.",
    },
    "proton.me": {
        "provider": "protonmail",
        "name": "Protonmail",
        "host": "127.0.0.1",
        "port": 1143,
        "tls": "None",
        "auth_type": "app_password",
        "note": "Proton Bridge required. Install and run it locally before syncing.",
    },
    "pm.me": {
        "provider": "protonmail",
        "name": "Protonmail",
        "host": "127.0.0.1",
        "port": 1143,
        "tls": "None",
        "auth_type": "app_password",
        "note": "Proton Bridge required. Install and run it locally before syncing.",
    },
    "pec.it": {
        "provider": "aruba-pec",
        "name": "Aruba PEC",
        "host": "imaps.pec.aruba.it",
        "port": 993,
        "tls": "IMAPS",
        "auth_type": "app_password",
        "auth_mechs": "LOGIN",
        "note": "PEC server advertises XOAUTH2 but only supports LOGIN/PLAIN.",
    },
    "arubapec.it": {
        "provider": "aruba-pec",
        "name": "Aruba PEC",
        "host": "imaps.pec.aruba.it",
        "port": 993,
        "tls": "IMAPS",
        "auth_type": "app_password",
        "auth_mechs": "LOGIN",
        "note": "PEC server advertises XOAUTH2 but only supports LOGIN/PLAIN.",
    },
    "legalmail.it": {
        "provider": "infocert-pec",
        "name": "Legalmail PEC",
        "host": "mbox.cert.legalmail.it",
        "port": 993,
        "tls": "IMAPS",
        "auth_type": "app_password",
        "note": None,
    },
}


def discover_provider(domain: str) -> dict | None:
    domain = domain.lower().strip()

    if domain in WELL_KNOWN_PROVIDERS:
        return WELL_KNOWN_PROVIDERS[domain]

    for method in (_discover_autoconfig, _discover_dns, _discover_from_mx):
        result = method(domain)
        if result:
            return result

    return None


def _discover_autoconfig(domain: str) -> dict | None:
    """Try Thunderbird autoconfig and Microsoft Autodiscover XML endpoints."""
    hosts_and_urls = [
        (f"autoconfig.{domain}", f"https://autoconfig.{domain}/mail/config-v1.1.xml"),
        (domain, f"https://{domain}/.well-known/autoconfig/mail/config-v1.1.xml"),
    ]
    for host, url in hosts_and_urls:
        try:
            validate_host_not_internal(host)
        except ValueError:
            continue
        result = _parse_autoconfig_xml(url, domain)
        if result:
            return result

    autodiscover_host = f"autodiscover.{domain}"
    try:
        validate_host_not_internal(autodiscover_host)
    except ValueError:
        return None
    autodiscover_url = f"https://{autodiscover_host}/autodiscover/autodiscover.xml"
    result = _parse_autodiscover_xml(autodiscover_url, domain)
    if result:
        return result

    return None


def _parse_autoconfig_xml(url: str, domain: str) -> dict | None:
    """Parse Mozilla/Thunderbird autoconfig XML format."""
    try:
        resp = httpx.get(url, timeout=5, follow_redirects=False)
        if resp.status_code != 200:
            return None
        root = ET.fromstring(resp.text)
        for server in root.iter("incomingServer"):
            if server.get("type") != "imap":
                continue
            hostname = server.findtext("hostname", "")
            port = int(server.findtext("port", "993"))
            socket_type = server.findtext("socketType", "SSL")
            tls = {"SSL": "IMAPS", "STARTTLS": "STARTTLS"}.get(socket_type, "IMAPS")
            return {
                "provider": "other",
                "name": f"Auto-discovered ({domain})",
                "host": hostname,
                "port": port,
                "tls": tls,
                "auth_type": "app_password",
                "note": f"Discovered via Thunderbird autoconfig: {url}",
            }
    except Exception:  # noqa: S110
        pass
    return None


_AUTODISCOVER_NS = "http://schemas.microsoft.com/exchange/autodiscover/outlook/responseschema/2006a"
_AUTODISCOVER_REQUEST = (
    '<?xml version="1.0" encoding="utf-8"?>'
    "<Autodiscover"
    ' xmlns="http://schemas.microsoft.com/exchange/autodiscover/'
    'outlook/requestschema/2006">'
    "<Request>"
    "<EMailAddress>user@{domain}</EMailAddress>"
    "<AcceptableResponseSchema>{ns}</AcceptableResponseSchema>"
    "</Request>"
    "</Autodiscover>"
)


def _parse_autodiscover_xml(url: str, domain: str) -> dict | None:
    """Parse Microsoft Autodiscover XML to find IMAP settings."""
    body = _AUTODISCOVER_REQUEST.format(domain=domain, ns=_AUTODISCOVER_NS)
    try:
        resp = httpx.post(
            url,
            content=body,
            headers={"Content-Type": "text/xml"},
            timeout=5,
            follow_redirects=False,
        )
        if resp.status_code != 200:
            return None
        return _extract_imap_from_autodiscover(resp.text, domain, url)
    except Exception:  # noqa: S110
        pass
    return None


def _extract_imap_from_autodiscover(
    xml_text: str,
    domain: str,
    source_url: str,
) -> dict | None:
    root = ET.fromstring(xml_text)
    ns = {"a": _AUTODISCOVER_NS}
    for protocol in root.findall(".//a:Protocol", ns):
        if protocol.findtext("a:Type", "", ns) != "IMAP":
            continue
        host = protocol.findtext("a:Server", "", ns)
        if not host:
            continue
        port = int(protocol.findtext("a:Port", "993", ns))
        ssl = protocol.findtext("a:SSL", "on", ns)
        return {
            "provider": "other",
            "name": f"Auto-discovered ({domain})",
            "host": host,
            "port": port,
            "tls": "IMAPS" if ssl == "on" else "STARTTLS",
            "auth_type": "app_password",
            "note": f"Discovered via Microsoft Autodiscover: {source_url}",
        }
    return None


def _discover_dns(domain: str) -> dict | None:
    for srv_name, tls, default_port in [
        (f"_imaps._tcp.{domain}", "IMAPS", 993),
        (f"_imap._tcp.{domain}", "STARTTLS", 143),
    ]:
        try:
            answers = dns.resolver.resolve(srv_name, "SRV")
            for rdata in answers:
                host = str(rdata.target).rstrip(".")
                port = rdata.port or default_port
                return {
                    "provider": "other",
                    "name": f"Auto-discovered ({domain})",
                    "host": host,
                    "port": port,
                    "tls": tls,
                    "auth_type": "app_password",
                    "note": f"Discovered via DNS SRV record {srv_name}",
                }
        except Exception:  # noqa: S112
            continue
    return None


def _discover_from_mx(domain: str) -> dict | None:
    try:
        answers = dns.resolver.resolve(domain, "MX")
        mx_hosts = [str(rdata.exchange).rstrip(".").lower() for rdata in answers]
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, Exception):
        return None

    mx_to_provider = {
        "google.com": "gmail.com",
        "googlemail.com": "gmail.com",
        "outlook.com": "outlook.com",
        "protection.outlook.com": "outlook.com",
        "yahoodns.net": "yahoo.com",
        "icloud.com": "icloud.com",
        "protonmail.ch": "protonmail.com",
    }

    for mx in mx_hosts:
        for pattern, provider_domain in mx_to_provider.items():
            if mx.endswith(pattern):
                provider = WELL_KNOWN_PROVIDERS[provider_domain].copy()
                provider["name"] = f"{provider['name']} (via MX for {domain})"
                provider["note"] = f"Detected via MX record: {mx}"
                return provider

    return None
