"""Gap 3: surface platform capabilities (char_limit, needs_title,
supports_first_comment) on every account-shaped response.

Without these, agents have to guess which platform needs a title or
whether a first_comment will actually be posted. The fields come from
``SocialAccount.char_limit``, ``SocialAccount.field_config``, and
``SocialAccount.supports_first_comment()`` respectively.

Historical LinkedIn Personal rows are retained only for data migration and
must never appear on REST or MCP account surfaces.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.utils import timezone

from apps.api_keys import services
from apps.members.models import PERMISSION_KEYS, OrgMembership, WorkspaceMembership


class _SecureClient(Client):
    def generic(self, method, path, *args, **kwargs):
        kwargs["secure"] = True
        return super().generic(method, path, *args, **kwargs)


# ---------------------------------------------------------------------------
# Shared scaffolding
# ---------------------------------------------------------------------------


@pytest.fixture
def user(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="cap@example.com",
        password="testpass123",
        name="Caps",
        tos_accepted_at=timezone.now(),
    )


@pytest.fixture
def organization(db):
    from apps.organizations.models import Organization

    return Organization.objects.create(name="Cap Org")


@pytest.fixture
def workspace(db, organization):
    from apps.workspaces.models import Workspace

    return Workspace.objects.create(name="Caps WS", organization=organization)


@pytest.fixture
def owner_memberships(db, user, organization, workspace):
    OrgMembership.objects.create(user=user, organization=organization, org_role=OrgMembership.OrgRole.OWNER)
    return WorkspaceMembership.objects.create(
        user=user, workspace=workspace, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
    )


def _make_account(workspace, platform: str, **kwargs):
    from apps.social_accounts.models import SocialAccount

    return SocialAccount.objects.create(
        workspace=workspace,
        platform=platform,
        account_platform_id=kwargs.pop("account_platform_id", f"{platform}-id"),
        account_name=kwargs.pop("account_name", f"{platform} acct"),
        connection_status=kwargs.pop("connection_status", "connected"),
        **kwargs,
    )


def _issued_key(workspace, user, social_accounts):
    return services.issue_api_key(
        workspace=workspace,
        social_accounts=social_accounts,
        issued_by=user,
        name="caps",
        permissions=list(PERMISSION_KEYS),
    )


# ---------------------------------------------------------------------------
# Per-platform capability matrix
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPerPlatformCapabilities:
    """One fixture, many platforms; verify each surfaces correctly."""

    @pytest.fixture
    def all_platforms_key(self, user, owner_memberships, workspace):
        accounts = [
            _make_account(workspace, "youtube", account_platform_id="yt-1"),
            _make_account(workspace, "pinterest", account_platform_id="pi-1"),
            _make_account(workspace, "tiktok", account_platform_id="tk-1"),
            _make_account(workspace, "bluesky", account_platform_id="bs-1"),
            _make_account(workspace, "google_business", account_platform_id="gb-1"),
            _make_account(workspace, "linkedin_company", account_platform_id="112378013"),
            _make_account(workspace, "facebook", account_platform_id="fb-1"),
        ]
        return _issued_key(workspace, user, accounts)

    @pytest.fixture
    def client(self, all_platforms_key):
        return _SecureClient(HTTP_AUTHORIZATION=f"Bearer {all_platforms_key.plaintext_token}")

    def _by_platform(self, accounts: list[dict]) -> dict[str, dict]:
        return {a["platform"]: a for a in accounts}

    def test_me_surfaces_capabilities_per_platform(self, client):
        body = client.get("/api/v1/me/").json()
        accs = self._by_platform(body["allowlisted_accounts"])

        # YouTube: needs a title, supports first comment, 5000-char captions.
        assert accs["youtube"]["needs_title"] is True
        assert accs["youtube"]["char_limit"] == 5000
        assert accs["youtube"]["supports_first_comment"] is True

        # Pinterest: needs a title, but no first comment.
        assert accs["pinterest"]["needs_title"] is True
        assert accs["pinterest"]["supports_first_comment"] is False
        assert accs["pinterest"]["char_limit"] == 500

        # TikTok / Bluesky / Google Business: no first comment.
        assert accs["tiktok"]["supports_first_comment"] is False
        assert accs["bluesky"]["supports_first_comment"] is False
        assert accs["google_business"]["supports_first_comment"] is False

        # LinkedIn Company: supports first comment unconditionally.
        assert accs["linkedin_company"]["supports_first_comment"] is True
        assert accs["linkedin_company"]["needs_title"] is False
        assert accs["linkedin_company"]["char_limit"] == 3000

        # Facebook: also defaults to supports_first_comment=True.
        assert accs["facebook"]["supports_first_comment"] is True

    def test_accounts_endpoint_surfaces_same_capabilities(self, client):
        body = client.get("/api/v1/accounts/").json()
        accs = self._by_platform(body["accounts"])
        assert accs["youtube"]["needs_title"] is True
        assert accs["tiktok"]["supports_first_comment"] is False
        assert accs["facebook"]["char_limit"] == 63206

    def test_mcp_list_accounts_tool_surfaces_same_capabilities(self, client):
        """MCP list_accounts must mirror REST or agents that route through
        the MCP surface get a different capability picture for the same
        accounts.
        """
        r = client.post(
            "/api/v1/mcp/",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": "list_accounts", "arguments": {}},
                    "id": 1,
                }
            ),
            content_type="application/json",
        )
        envelope = r.json()
        assert "error" not in envelope, envelope
        inner = json.loads(envelope["result"]["content"][0]["text"])
        accs = self._by_platform(inner["accounts"])
        assert accs["youtube"]["needs_title"] is True
        assert accs["pinterest"]["supports_first_comment"] is False
        assert accs["linkedin_company"]["char_limit"] == 3000


# ---------------------------------------------------------------------------
# Historical LinkedIn Personal quarantine
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHistoricalLinkedInPersonalQuarantine:
    """A legacy allowlist relation cannot reactivate a personal identity."""

    @pytest.fixture
    def key_with_historical_relation(self, user, owner_memberships, workspace):
        from apps.social_accounts.models import SocialAccount

        active = _make_account(workspace, "facebook", account_platform_id="fb-active-1")
        historical = SocialAccount.all_objects.create(
            workspace=workspace,
            platform="linkedin_personal",
            account_platform_id="human-page-admin",
            account_name="Historical Human",
        )
        key = _issued_key(workspace, user, [active])
        # Simulate a relation created before personal LinkedIn was retired.
        key.api_key.social_accounts.add(historical)
        return key

    @pytest.fixture
    def client(self, key_with_historical_relation):
        return _SecureClient(HTTP_AUTHORIZATION=f"Bearer {key_with_historical_relation.plaintext_token}")

    def test_rest_and_mcp_never_return_historical_personal_identity(self, client):
        me_accounts = client.get("/api/v1/me/").json()["allowlisted_accounts"]
        listed_accounts = client.get("/api/v1/accounts/").json()["accounts"]
        response = client.post(
            "/api/v1/mcp/",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": "list_accounts", "arguments": {}},
                    "id": 1,
                }
            ),
            content_type="application/json",
        ).json()
        mcp_accounts = json.loads(response["result"]["content"][0]["text"])["accounts"]

        for accounts in (me_accounts, listed_accounts, mcp_accounts):
            assert {account["platform"] for account in accounts} == {"facebook"}
            assert "human-page-admin" not in repr(accounts)
