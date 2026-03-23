"""Shared visibility filter — subscription-based model.

After the marketplace redesign, org auto-visibility is removed.  Resources
are visible to a user only if:

1. The user owns the resource (any visibility setting), OR
2. The user has an explicit ResourceSubscription for it.

Org-scoped resources are surfaced via subscriptions that are created when
org members subscribe from the Market page (or via auto-subscribe on
resource creation / org membership join).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select

from fim_one.web.models.resource_subscription import ResourceSubscription

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def build_visibility_filter(
    model: Any,
    user_id: str,
    user_org_ids: list[str],  # kept for backward compat; unused after marketplace redesign
    subscribed_ids: list[str] | None = None,
) -> Any:
    """Build a SQLAlchemy WHERE clause for visibility filtering.

    Returns rows where:
    - user owns the resource (any visibility), OR
    - resource id is in the user's subscription list

    Org resources are now included via subscriptions (created when user
    subscribes from the org scope in the Market page).

    For a higher-level helper that auto-fetches org IDs and subscriptions,
    see :func:`resolve_visibility`.
    """
    conditions = [
        model.user_id == user_id,  # own resources (any visibility)
    ]

    if subscribed_ids:
        conditions.append(model.id.in_(subscribed_ids))

    return or_(*conditions)


async def resolve_visibility(
    model: Any,
    user_id: str,
    resource_type: str,
    db: AsyncSession,
) -> tuple[Any, list[str], list[str]]:
    """High-level visibility resolver: org IDs + subscriptions in one call.

    Parameters
    ----------
    model:
        SQLAlchemy model class (Agent, Connector, MCPServer, etc.)
    user_id:
        Current user ID.
    resource_type:
        Subscription resource type (``"agent"``, ``"connector"``,
        ``"mcp_server"``, ``"knowledge_base"``, ``"skill"``, ``"workflow"``).
    db:
        Async DB session.

    Returns
    -------
    (filter_clause, user_org_ids, subscribed_ids)
        - ``filter_clause``: ready-to-use SQLAlchemy WHERE clause
        - ``user_org_ids``: list of org IDs the user belongs to
        - ``subscribed_ids``: list of subscribed resource IDs
    """
    from fim_one.web.auth import get_user_org_ids

    user_org_ids = await get_user_org_ids(user_id, db)
    sub_result = await db.execute(
        select(ResourceSubscription.resource_id).where(
            ResourceSubscription.user_id == user_id,
            ResourceSubscription.resource_type == resource_type,
        )
    )
    subscribed_ids: list[str] = list(sub_result.scalars().all())
    clause = build_visibility_filter(
        model, user_id, user_org_ids, subscribed_ids=subscribed_ids,
    )
    return clause, user_org_ids, subscribed_ids
