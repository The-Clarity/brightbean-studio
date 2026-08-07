"""Tests for the connection-link OAuth flow — PKCE round-trip for TikTok.

The connection-link flow is a second, public OAuth entry point (separate from
social_accounts.connect_platform). It must apply PKCE for providers that need
it (TikTok), otherwise the authorize URL lacks code_challenge and TikTok
rejects it with errCode=10007.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.onboarding.models import ConnectionLink
from apps.onboarding.views import CONNECTION_LINK_OAUTH_SESSION_KEY, _sign_connection_link_state
from apps.social_accounts.models import SocialAccount
from providers.types import AccountProfile, OAuthTokens


@pytest.fixture
def workspace(db, organization):
    from apps.workspaces.models import Workspace

    return Workspace.objects.create(name="Test WS", organization=organization)


@pytest.fixture
def connection_link(db, workspace):
    return ConnectionLink.objects.create(
        workspace=workspace,
        expires_at=timezone.now() + timezone.timedelta(days=1),
    )


@pytest.mark.django_db
class TestConnectionLinkPkce:
    def test_oauth_start_generates_and_forwards_verifier(self, client, workspace, connection_link):
        from apps.credentials.models import PlatformCredential

        PlatformCredential.objects.create(
            organization=workspace.organization,
            platform="tiktok",
            credentials={"client_key": "k", "client_secret": "s"},
            is_configured=True,
        )

        mock_provider = MagicMock()
        mock_provider.uses_pkce = True
        mock_provider.get_auth_url.return_value = "https://www.tiktok.com/v2/auth/authorize/?ok=1"

        url = reverse("onboarding:connection_oauth_start", kwargs={"token": connection_link.token})
        with patch("apps.onboarding.views._get_provider_for_platform", return_value=mock_provider):
            response = client.post(url, {"platform": "tiktok"})

        assert response.status_code == 302
        verifier = client.session[CONNECTION_LINK_OAUTH_SESSION_KEY]["code_verifier"]
        assert verifier  # non-empty
        _, kwargs = mock_provider.get_auth_url.call_args
        assert kwargs["code_verifier"] == verifier

    def test_oauth_callback_replays_verifier(self, client, workspace, connection_link):
        nonce = "nonce-xyz"
        verifier = "stored-connection-verifier"
        state = _sign_connection_link_state(workspace.id, "tiktok", connection_link.token, nonce)
        session = client.session
        session[CONNECTION_LINK_OAUTH_SESSION_KEY] = {
            "nonce": nonce,
            "workspace_id": str(workspace.id),
            "platform": "tiktok",
            "token": connection_link.token,
            "code_verifier": verifier,
        }
        session.save()

        mock_provider = MagicMock()
        mock_provider.exchange_code.return_value = OAuthTokens(access_token="tok", refresh_token="r", expires_in=3600)
        mock_provider.get_profile.return_value = AccountProfile(platform_id="open-1", name="TT")

        url = reverse("onboarding:oauth_callback", kwargs={"platform": "social1"})
        with patch("apps.onboarding.views._get_provider_for_platform", return_value=mock_provider):
            response = client.get(url, {"code": "auth-code", "state": state})

        assert response.status_code == 302
        mock_provider.exchange_code.assert_called_once()
        _, kwargs = mock_provider.exchange_code.call_args
        assert kwargs["code_verifier"] == verifier


@pytest.mark.django_db
class TestConnectionLinkClarityLinkedInPolicy:
    @override_settings(CLARITY_LINKEDIN_PAGE_ONLY=True)
    def test_connection_page_hides_personal_linkedin(self, client, connection_link):
        response = client.get(reverse("onboarding:connection_page", kwargs={"token": connection_link.token}))

        assert response.status_code == 200
        assert b"LinkedIn (Personal Profile)" not in response.content

    @override_settings(CLARITY_LINKEDIN_PAGE_ONLY=True)
    def test_oauth_start_rejects_personal_linkedin_server_side(self, client, connection_link):
        url = reverse("onboarding:connection_oauth_start", kwargs={"token": connection_link.token})
        with (
            patch("apps.onboarding.views._get_configured_platforms", return_value={"linkedin_personal"}),
            patch("apps.onboarding.views._get_provider_for_platform") as get_provider,
        ):
            response = client.post(url, {"platform": "linkedin_personal"})

        assert response.status_code == 302
        get_provider.assert_not_called()

    @override_settings(
        CLARITY_LINKEDIN_PAGE_ONLY=True,
        CLARITY_LINKEDIN_ALLOWED_ORGANIZATION_IDS=("112378013",),
    )
    def test_oauth_callback_connects_only_clarity_page(self, client, workspace, connection_link):
        nonce = "nonce-linkedin-company"
        state = _sign_connection_link_state(workspace.id, "linkedin_company", connection_link.token, nonce)
        session = client.session
        session[CONNECTION_LINK_OAUTH_SESSION_KEY] = {
            "nonce": nonce,
            "workspace_id": str(workspace.id),
            "platform": "linkedin_company",
            "token": connection_link.token,
            "code_verifier": None,
        }
        session.save()

        mock_provider = MagicMock()
        mock_provider.exchange_code.return_value = OAuthTokens(access_token="page-token", refresh_token="refresh")
        mock_provider.get_user_pages.return_value = [
            {"id": "999", "name": "Another Page", "access_token": "page-token"},
            {"id": "112378013", "name": "Clarity", "access_token": "page-token"},
        ]
        url = reverse("onboarding:oauth_callback", kwargs={"platform": "linkedin_company"})

        with patch("apps.onboarding.views._get_provider_for_platform", return_value=mock_provider):
            response = client.get(url, {"code": "auth-code", "state": state})

        assert response.status_code == 302
        assert list(
            SocialAccount.objects.filter(workspace=workspace, platform="linkedin_company").values_list(
                "account_platform_id", flat=True
            )
        ) == ["112378013"]
        mock_provider.get_profile.assert_not_called()
