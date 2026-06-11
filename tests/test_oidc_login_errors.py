"""OIDC login/callback must degrade gracefully when the IdP is unreachable.

A slow or down identity provider used to surface as a raw 500 (httpx.ReadTimeout
inside authlib's lazy server-metadata fetch). Both endpoints now redirect to
/login with a friendly error instead.
"""

from unittest.mock import AsyncMock, patch

import httpx
from authlib.integrations.base_client.errors import MismatchingStateError


class TestOidcLoginErrors:
    def test_login_redirects_on_idp_timeout(self, client, db_session, default_store):
        with patch("mailfallback.routers.auth.settings") as mock_settings:
            mock_settings.oidc_enabled = True
            with patch("mailfallback.routers.auth.oauth") as mock_oauth:
                mock_oauth.oidc.authorize_redirect = AsyncMock(
                    side_effect=httpx.ReadTimeout("timed out")
                )
                resp = client.get("/auth/oidc/login", follow_redirects=False)

        assert resp.status_code == 303
        assert resp.headers["location"] == "/login?error=sso_unreachable"

    def test_callback_redirects_on_idp_timeout(self, client, db_session, default_store):
        with patch("mailfallback.routers.auth.settings") as mock_settings:
            mock_settings.oidc_enabled = True
            with patch("mailfallback.routers.auth.oauth") as mock_oauth:
                mock_oauth.oidc.authorize_access_token = AsyncMock(
                    side_effect=httpx.ReadTimeout("timed out")
                )
                resp = client.get("/auth/oidc/callback", follow_redirects=False)

        assert resp.status_code == 303
        assert resp.headers["location"] == "/login?error=sso_unreachable"

    def test_callback_redirects_on_state_mismatch(self, client, db_session, default_store):
        with patch("mailfallback.routers.auth.settings") as mock_settings:
            mock_settings.oidc_enabled = True
            with patch("mailfallback.routers.auth.oauth") as mock_oauth:
                mock_oauth.oidc.authorize_access_token = AsyncMock(
                    side_effect=MismatchingStateError()
                )
                resp = client.get("/auth/oidc/callback", follow_redirects=False)

        assert resp.status_code == 303
        assert resp.headers["location"] == "/login?error=sso_failed"

    def test_login_page_renders_sso_error_message(self, client, db_session, default_store):
        resp = client.get("/login?error=sso_unreachable")

        assert resp.status_code == 200
        assert "SSO" in resp.text
