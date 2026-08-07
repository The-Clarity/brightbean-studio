"""Clarity Page-only tests for ``LinkedInCompanyProvider``."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from providers.exceptions import APIError, RateLimitError
from providers.linkedin_company import LinkedInCompanyProvider
from providers.types import PublishContent

CLARITY_ORGANIZATION_ID = "112378013"
CLARITY_ORGANIZATION_URN = f"urn:li:organization:{CLARITY_ORGANIZATION_ID}"


def _make_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    return response


def _approved_acl(*, organization: str = CLARITY_ORGANIZATION_URN) -> dict:
    return {
        "elements": [
            {
                "organization": organization,
                "role": "ADMINISTRATOR",
                "state": "APPROVED",
                # The consenting human may appear in the ACL response, but it
                # must never become the connected or health-check identity.
                "roleAssignee": "urn:li:person:human-page-admin",
            }
        ]
    }


def _clarity_page() -> dict:
    return {
        "id": 112378013,
        "localizedName": "Clarity",
        "vanityName": "the-clarity",
        "logoV2": {
            "original~": {
                "elements": [
                    {
                        "identifiers": [
                            {
                                "identifier": "https://media.licdn.com/clarity-page.png",
                            }
                        ]
                    }
                ]
            }
        },
    }


def test_page_app_requests_exact_community_management_scopes():
    scopes = LinkedInCompanyProvider().required_scopes

    assert scopes == [
        "rw_organization_admin",
        "w_organization_social",
        "r_organization_social",
    ]
    assert not {"openid", "profile", "email", "r_basicprofile", "w_member_social"}.intersection(scopes)


def test_linkedin_http_failures_never_serialize_or_log_page_admin_identity(caplog):
    member_urn = "urn:li:person:human-page-admin"
    member_name = "Human Page Admin"
    member_avatar = "https://media.licdn.com/member-avatar.jpg"
    response = MagicMock()
    response.status_code = 429
    response.headers = {"Retry-After": "60"}
    response.text = f'{{"message":"denied for {member_urn}","name":"{member_name}","avatar":"{member_avatar}"}}'
    response.json.return_value = {
        "message": f"denied for {member_urn}",
        "name": member_name,
        "avatar": member_avatar,
    }
    client = MagicMock()
    client.__enter__.return_value = client
    client.request.return_value = response

    with (
        patch("providers.base.httpx.Client", return_value=client),
        caplog.at_level(logging.ERROR),
        pytest.raises(RateLimitError) as exc_info,
    ):
        LinkedInCompanyProvider().get_user_pages("page-token")

    observable = f"{exc_info.value!s} {exc_info.value.raw_response!r} {caplog.text}"
    assert "LinkedIn response details omitted" in observable
    assert member_urn not in observable
    assert member_name not in observable
    assert member_avatar not in observable


class TestExactClarityPageIdentity:
    @patch.object(LinkedInCompanyProvider, "_request")
    def test_page_selector_requires_approved_acl_then_fetches_exact_page(self, mock_request):
        mock_request.side_effect = [
            _make_response(
                {
                    "elements": [
                        {
                            "organization": "urn:li:organization:999",
                            "role": "ADMINISTRATOR",
                            "state": "APPROVED",
                        },
                        _approved_acl()["elements"][0],
                    ]
                }
            ),
            _make_response(_clarity_page()),
        ]

        pages = LinkedInCompanyProvider().get_user_pages("page-token")

        assert pages == [
            {
                "id": CLARITY_ORGANIZATION_ID,
                "name": "Clarity",
                "handle": "the-clarity",
                "access_token": "page-token",
                "picture": "https://media.licdn.com/clarity-page.png",
            }
        ]
        assert "human-page-admin" not in repr(pages)
        acl_call, page_call = mock_request.call_args_list
        assert acl_call.args[:2] == ("GET", "https://api.linkedin.com/rest/organizationAcls")
        assert acl_call.kwargs["params"] == {
            "q": "roleAssignee",
            "role": "ADMINISTRATOR",
            "state": "APPROVED",
            "count": 100,
            "start": 0,
        }
        assert page_call.args[:2] == (
            "GET",
            "https://api.linkedin.com/rest/organizations/112378013",
        )
        assert all("/v2/me" not in call.args[1] for call in mock_request.call_args_list)
        assert all("/userinfo" not in call.args[1] for call in mock_request.call_args_list)

    @patch.object(LinkedInCompanyProvider, "_request")
    def test_profile_is_the_page_and_never_the_consenting_human(self, mock_request):
        mock_request.side_effect = [
            _make_response(_approved_acl()),
            _make_response(_clarity_page()),
        ]

        profile = LinkedInCompanyProvider().get_profile("page-token")

        assert profile.platform_id == CLARITY_ORGANIZATION_ID
        assert profile.name == "Clarity"
        assert profile.handle == "the-clarity"
        assert profile.avatar_url == "https://media.licdn.com/clarity-page.png"
        assert "human-page-admin" not in repr(profile)
        assert all("/v2/me" not in call.args[1] for call in mock_request.call_args_list)
        assert all("/userinfo" not in call.args[1] for call in mock_request.call_args_list)

    @patch.object(LinkedInCompanyProvider, "_request")
    def test_selector_ignores_wrong_or_unapproved_organizations(self, mock_request):
        mock_request.return_value = _make_response(
            {
                "elements": [
                    {
                        "organization": "urn:li:organization:999",
                        "role": "ADMINISTRATOR",
                        "state": "APPROVED",
                    },
                    {
                        "organization": CLARITY_ORGANIZATION_URN,
                        "role": "ADMINISTRATOR",
                        "state": "REVOKED",
                    },
                ]
            }
        )

        assert LinkedInCompanyProvider().get_user_pages("page-token") == []
        assert mock_request.call_count == 1

    @patch.object(LinkedInCompanyProvider, "_request")
    def test_selector_finds_clarity_across_paginated_acl_results(self, mock_request):
        mock_request.side_effect = [
            _make_response(
                {
                    "elements": [
                        {
                            "organization": "urn:li:organization:999",
                            "role": "ADMINISTRATOR",
                            "state": "APPROVED",
                        }
                    ],
                    "paging": {"start": 0, "count": 1, "total": 2},
                }
            ),
            _make_response(
                {
                    **_approved_acl(),
                    "paging": {"start": 1, "count": 1, "total": 2},
                }
            ),
            _make_response(_clarity_page()),
        ]

        pages = LinkedInCompanyProvider().get_user_pages("page-token")

        assert pages[0]["id"] == CLARITY_ORGANIZATION_ID
        assert [call.kwargs["params"]["start"] for call in mock_request.call_args_list[:2]] == [0, 1]

    @patch.object(LinkedInCompanyProvider, "_request")
    def test_profile_fails_closed_without_exact_approved_acl(self, mock_request):
        mock_request.return_value = _make_response(_approved_acl(organization="urn:li:organization:999"))

        with pytest.raises(APIError, match="not an approved administrator") as exc_info:
            LinkedInCompanyProvider().get_profile("page-token")

        assert exc_info.value.retryable is False
        assert mock_request.call_count == 1

    @patch.object(LinkedInCompanyProvider, "_request")
    def test_profile_rejects_mismatched_page_payload(self, mock_request):
        wrong_page = _clarity_page()
        wrong_page["id"] = 999
        mock_request.side_effect = [
            _make_response(_approved_acl()),
            _make_response(wrong_page),
        ]

        with pytest.raises(APIError, match="unexpected organization") as exc_info:
            LinkedInCompanyProvider().get_profile("page-token")

        assert exc_info.value.retryable is False

    @patch.object(LinkedInCompanyProvider, "_request")
    def test_missing_page_logo_does_not_invalidate_identity(self, mock_request):
        page = _clarity_page()
        page.pop("logoV2")
        mock_request.side_effect = [
            _make_response(_approved_acl()),
            _make_response(page),
        ]

        profile = LinkedInCompanyProvider().get_profile("page-token")

        assert profile.platform_id == CLARITY_ORGANIZATION_ID
        assert profile.avatar_url is None


class TestExactClarityPageActor:
    @patch.object(LinkedInCompanyProvider, "_request")
    def test_publish_post_forces_the_exact_page_actor(self, mock_request):
        published = _make_response({})
        published.headers = {"x-restli-id": "urn:li:share:123"}
        mock_request.side_effect = [
            _make_response(_approved_acl()),
            _make_response(_clarity_page()),
            published,
        ]

        LinkedInCompanyProvider().publish_post("page-token", PublishContent(text="Page update"))

        publish_call = mock_request.call_args_list[-1]
        assert publish_call.args[:2] == ("POST", "https://api.linkedin.com/rest/posts")
        assert publish_call.kwargs["json"]["author"] == CLARITY_ORGANIZATION_URN
        assert "urn:li:person:" not in repr(publish_call.kwargs["json"])

    @patch.object(LinkedInCompanyProvider, "_request")
    def test_publish_post_rejects_a_supplied_person_actor_before_network(self, mock_request):
        content = PublishContent(
            text="Not allowed",
            extra={"author": "urn:li:person:human-page-admin"},
        )

        with pytest.raises(APIError, match="must be exactly the Clarity Page") as exc_info:
            LinkedInCompanyProvider().publish_post("page-token", content)

        assert exc_info.value.retryable is False
        mock_request.assert_not_called()

    @patch.object(LinkedInCompanyProvider, "_request")
    def test_comment_and_inbox_paths_use_the_page_actor(self, mock_request):
        comment = _make_response({"id": "comment-1"})
        comment.headers = {"x-restli-id": "comment-1"}
        mock_request.side_effect = [
            _make_response(_approved_acl()),
            _make_response(_clarity_page()),
            comment,
            _make_response(_approved_acl()),
            _make_response(_clarity_page()),
            _make_response({"elements": []}),
        ]
        provider = LinkedInCompanyProvider()

        provider.publish_comment("page-token", "urn:li:share:123", "Page reply")
        provider.get_messages("page-token")

        comment_call = mock_request.call_args_list[2]
        inbox_call = mock_request.call_args_list[5]
        assert comment_call.kwargs["json"]["actor"] == CLARITY_ORGANIZATION_URN
        assert inbox_call.kwargs["params"]["author"] == CLARITY_ORGANIZATION_URN
        assert "urn:li:person:" not in repr(comment_call.kwargs["json"])
        assert "urn:li:person:" not in repr(inbox_call.kwargs["params"])
