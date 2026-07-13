"""Version module + exposure in /healthz."""

import re


def test_version_module_format():
    from mailfallback.version import __version__

    assert __version__ == "dev" or re.fullmatch(r"\d{4}\.\d{2}\.\d+(-(rc|beta)\d+)?", __version__)


def test_healthz_exposes_version(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    from mailfallback.version import __version__

    assert data["version"] == __version__
