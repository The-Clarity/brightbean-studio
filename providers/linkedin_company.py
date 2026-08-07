"""LinkedIn provider variant for Company Page posting.

The human administrator only grants the OAuth token. Every identity read and
returned by this provider is the exact Clarity organization Page.
"""

from __future__ import annotations

import logging

from .exceptions import APIError
from .linkedin import API_BASE, LINKEDIN_HEADERS, LinkedInProvider
from .types import AccountProfile

logger = logging.getLogger(__name__)

CLARITY_LINKEDIN_ORGANIZATION_ID = "112378013"
CLARITY_LINKEDIN_ORGANIZATION_URN = f"urn:li:organization:{CLARITY_LINKEDIN_ORGANIZATION_ID}"
LINKEDIN_ACL_PAGE_SIZE = 100


class LinkedInCompanyProvider(LinkedInProvider):
    """LinkedIn provider scoped to Company Page posting."""

    @property
    def platform_name(self) -> str:
        return "LinkedIn (Company Page)"

    @property
    def required_scopes(self) -> list[str]:
        return [
            "rw_organization_admin",
            "w_organization_social",
            "r_organization_social",
        ]

    @staticmethod
    def _is_exact_approved_acl(element: object) -> bool:
        if not isinstance(element, dict):
            return False
        organization = element.get("organization")
        organization_target = element.get("organizationTarget")
        if (
            isinstance(organization, str)
            and isinstance(organization_target, str)
            and organization != organization_target
        ):
            return False
        organization_urn = organization if isinstance(organization, str) else organization_target
        return (
            organization_urn == CLARITY_LINKEDIN_ORGANIZATION_URN
            and element.get("role") == "ADMINISTRATOR"
            and element.get("state") == "APPROVED"
        )

    def _has_exact_approved_acl(self, access_token: str) -> bool:
        start = 0
        while True:
            response = self._request(
                "GET",
                f"{API_BASE}/rest/organizationAcls",
                access_token=access_token,
                headers=LINKEDIN_HEADERS,
                params={
                    "q": "roleAssignee",
                    "role": "ADMINISTRATOR",
                    "state": "APPROVED",
                    "count": LINKEDIN_ACL_PAGE_SIZE,
                    "start": start,
                },
            )
            data = response.json()
            elements = data.get("elements", []) if isinstance(data, dict) else []
            if not isinstance(elements, list):
                raise APIError("LinkedIn returned an invalid organization ACL response", platform=self.platform_name)
            if any(self._is_exact_approved_acl(element) for element in elements):
                return True

            paging = data.get("paging", {}) if isinstance(data.get("paging", {}), dict) else {}
            paging_start = paging.get("start", start)
            paging_count = paging.get("count", len(elements))
            total = paging.get("total")
            if not isinstance(paging_start, int) or not isinstance(paging_count, int):
                raise APIError("LinkedIn returned invalid organization ACL pagination", platform=self.platform_name)
            next_start = paging_start + paging_count
            has_next = next_start < total if isinstance(total, int) else len(elements) == LINKEDIN_ACL_PAGE_SIZE
            if not has_next:
                return False
            if next_start <= start:
                raise APIError("LinkedIn organization ACL pagination did not advance", platform=self.platform_name)
            start = next_start

    @staticmethod
    def _logo_url(page: dict) -> str | None:
        elements = page.get("logoV2", {}).get("original~", {}).get("elements", [])
        if not isinstance(elements, list) or not elements:
            return None
        identifiers = elements[0].get("identifiers", []) if isinstance(elements[0], dict) else []
        if not isinstance(identifiers, list) or not identifiers or not isinstance(identifiers[0], dict):
            return None
        identifier = identifiers[0].get("identifier")
        return identifier if isinstance(identifier, str) and identifier else None

    def _get_clarity_page(self, access_token: str) -> dict | None:
        if not self._has_exact_approved_acl(access_token):
            return None
        response = self._request(
            "GET",
            f"{API_BASE}/rest/organizations/{CLARITY_LINKEDIN_ORGANIZATION_ID}",
            access_token=access_token,
            headers=LINKEDIN_HEADERS,
        )
        page = response.json()
        if not isinstance(page, dict) or str(page.get("id", "")) != CLARITY_LINKEDIN_ORGANIZATION_ID:
            raise APIError(
                f"LinkedIn returned an unexpected organization for {CLARITY_LINKEDIN_ORGANIZATION_URN}",
                platform=self.platform_name,
                retryable=False,
            )
        if not isinstance(page.get("localizedName"), str) or not page["localizedName"].strip():
            raise APIError("LinkedIn returned an invalid Clarity Page name", platform=self.platform_name)
        return page

    def get_user_pages(self, access_token: str) -> list[dict]:
        page = self._get_clarity_page(access_token)
        if page is None:
            return []
        return [
            {
                "id": CLARITY_LINKEDIN_ORGANIZATION_ID,
                "name": page["localizedName"],
                "handle": page.get("vanityName", ""),
                "access_token": access_token,
                "picture": self._logo_url(page),
            }
        ]

    def get_profile(self, access_token: str) -> AccountProfile:
        """Return only the approved Clarity Page for connect and health paths."""
        page = self._get_clarity_page(access_token)
        if page is None:
            raise APIError(
                f"LinkedIn credential is not an approved administrator of {CLARITY_LINKEDIN_ORGANIZATION_URN}",
                platform=self.platform_name,
                retryable=False,
            )
        return AccountProfile(
            platform_id=CLARITY_LINKEDIN_ORGANIZATION_ID,
            name=page["localizedName"],
            handle=page.get("vanityName") or None,
            avatar_url=self._logo_url(page),
            extra={"organization": CLARITY_LINKEDIN_ORGANIZATION_URN},
        )

    def _resolve_actor_urn(self, access_token: str, requested_actor: str | None = None) -> str:
        if requested_actor and requested_actor != CLARITY_LINKEDIN_ORGANIZATION_URN:
            raise APIError(
                f"LinkedIn actor must be exactly the Clarity Page {CLARITY_LINKEDIN_ORGANIZATION_URN}",
                platform=self.platform_name,
                retryable=False,
            )
        # Revalidate the exact approved ACL before every actor-bearing read or
        # write. The consenting human remains an OAuth grantor, never an actor.
        self.get_profile(access_token)
        return CLARITY_LINKEDIN_ORGANIZATION_URN
