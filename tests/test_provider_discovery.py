from mailfallback.services.provider_discovery import WELL_KNOWN_PROVIDERS, discover_provider


def test_gmail_has_provider_field():
    result = WELL_KNOWN_PROVIDERS["gmail.com"]
    assert result["provider"] == "google"


def test_outlook_has_provider_field():
    result = WELL_KNOWN_PROVIDERS["outlook.com"]
    assert result["provider"] == "microsoft"


def test_yahoo_has_provider_field():
    result = WELL_KNOWN_PROVIDERS["yahoo.com"]
    assert result["provider"] == "yahoo"


def test_icloud_has_provider_field():
    result = WELL_KNOWN_PROVIDERS["icloud.com"]
    assert result["provider"] == "icloud"


def test_protonmail_has_provider_field():
    result = WELL_KNOWN_PROVIDERS["protonmail.com"]
    assert result["provider"] == "protonmail"


def test_discover_well_known_returns_provider():
    result = discover_provider("gmail.com")
    assert result is not None
    assert result["provider"] == "google"


def test_all_well_known_have_provider():
    for domain, entry in WELL_KNOWN_PROVIDERS.items():
        assert "provider" in entry, f"{domain} missing 'provider' key"


def test_discover_response_includes_provider():
    result = discover_provider("outlook.com")
    assert result is not None
    assert result["provider"] == "microsoft"

    result = discover_provider("proton.me")
    assert result is not None
    assert result["provider"] == "protonmail"


def test_pec_it_returns_correct_host():
    result = discover_provider("pec.it")
    assert result is not None
    assert result["provider"] == "aruba-pec"
    assert result["host"] == "imaps.pec.aruba.it"
    assert result["port"] == 993
    assert result["tls"] == "IMAPS"
    assert result["auth_mechs"] == "LOGIN"


def test_arubapec_it_returns_correct_host():
    result = discover_provider("arubapec.it")
    assert result is not None
    assert result["provider"] == "aruba-pec"
    assert result["host"] == "imaps.pec.aruba.it"


def test_legalmail_it_returns_correct_host():
    result = discover_provider("legalmail.it")
    assert result is not None
    assert result["provider"] == "infocert-pec"
    assert result["host"] == "mbox.cert.legalmail.it"
