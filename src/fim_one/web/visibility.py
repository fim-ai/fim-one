"""Shared visibility filter — two-tier: personal | org.

Global visibility has been replaced by Platform org membership.
"""
from __future__ import annotations

from sqlalchemy import and_, or_

from fim_one.web.models.resource_subscription import ResourceSubscription


def build_visibility_filter(
    model,
    user_id: str,
    user_org_ids: list[str],
    subscribed_ids: list[str] | None = None,
):
    """Build a SQLAlchemy WHERE clause for visibility filtering.

    Returns rows where:
    - user owns the resource (any visibility), OR
    - resource is published to an org the user belongs to, OR
    - resource id is in the user's subscription list
    """
    conditions = [
        model.user_id == user_id,  # own resources (any visibility)
    ]

    if user_org_ids:
        conditions.append(
            and_(model.visibility == "org", model.org_id.in_(user_org_ids))
        )

    if subscribed_ids:
        conditions.append(model.id.in_(subscribed_ids))

    return or_(*conditions)
