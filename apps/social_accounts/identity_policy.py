"""Deployment identity policy for LinkedIn connections and publishing."""

from __future__ import annotations

from collections.abc import Iterable

from django.conf import settings

LINKEDIN_PERSONAL = "linkedin_personal"
LINKEDIN_COMPANY = "linkedin_company"
CLARITY_LINKEDIN_ORGANIZATION_ID = "112378013"


def _organization_id(value: object) -> str:
    raw = str(value or "").strip()
    prefix = "urn:li:organization:"
    return raw[len(prefix) :] if raw.startswith(prefix) else raw


def allowed_linkedin_organization_ids() -> frozenset[str]:
    """Return the one code-pinned Clarity Page, or fail closed on config drift."""
    configured = getattr(settings, "CLARITY_LINKEDIN_ALLOWED_ORGANIZATION_IDS", ())
    values: Iterable[object] = configured.split(",") if isinstance(configured, str) else configured
    normalized = frozenset(value for item in values if (value := _organization_id(item)))
    exact = frozenset({CLARITY_LINKEDIN_ORGANIZATION_ID})
    return frozenset() if normalized and normalized != exact else exact


def platform_is_connectable(platform: str) -> bool:
    """Whether the deployment permits initiating a new connection."""
    return platform != LINKEDIN_PERSONAL


def filter_platform_choices(choices):
    """Remove deployment-disabled platforms while retaining historical enum values."""
    return [(value, label) for value, label in choices if platform_is_connectable(value)]


def filter_linkedin_pages(pages: Iterable[dict]) -> list[dict]:
    """Retain only explicitly approved Clarity organizations."""
    allowed = allowed_linkedin_organization_ids()
    return [page for page in pages if _organization_id(page.get("id")) in allowed]


def linkedin_publish_block_reason(platform: str, account_platform_id: object) -> str | None:
    """Return a permanent-failure reason when a LinkedIn target violates policy."""
    if platform == LINKEDIN_PERSONAL:
        return "Personal LinkedIn publishing is disabled; use the approved Clarity Page."
    if platform != LINKEDIN_COMPANY:
        return None

    organization_id = _organization_id(account_platform_id)
    if organization_id not in allowed_linkedin_organization_ids():
        return f"LinkedIn organization {organization_id or '<missing>'} is not an approved Clarity identity."
    return None
