"""Shared visibility filter — two-tier: personal | org.

Market org resources are visible only via ResourceSubscription.
Nobody joins Market org, so MARKET_ORG_ID is never in user_org_ids and the
org condition is naturally skipped for Market resources.  Subscribed Market
resources flow through the subscribed_ids path instead.
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

    Market org resources: Nobody joins Market org, so MARKET_ORG_ID is never
    present in ``user_org_ids`` and the org-membership condition is skipped.
    Subscribed Market resources are included via ``subscribed_ids``.
    """
    conditions = [
        model.user_id == user_id,  # own resources (any visibility)
    ]

    if user_org_ids:
        # Only show org resources that either don't need review or are approved.
        # pending_review and rejected resources are hidden from other org members
        # (the owner still sees them via the user_id == user_id condition above).
        conditions.append(
            and_(
                model.visibility == "org",
                model.org_id.in_(user_org_ids),
                or_(
                    model.publish_status == None,  # noqa: E711 — no review needed
                    model.publish_status == "approved",
                ),
            )
        )

    if subscribed_ids:
        conditions.append(model.id.in_(subscribed_ids))

    return or_(*conditions)
