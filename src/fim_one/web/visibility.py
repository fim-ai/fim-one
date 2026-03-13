"""Shared visibility filter for three-tier resource access control.

Three tiers:
- personal: only owner sees (default)
- org: org members see
- global: all authenticated users see (when status=published)
"""
from __future__ import annotations

from sqlalchemy import and_, or_


def build_visibility_filter(model, user_id: str, user_org_ids: list[str]):
    """Build a SQLAlchemy WHERE clause for visibility filtering.

    Returns rows where:
    - user owns the resource (any visibility), OR
    - resource is published to an org the user belongs to, OR
    - resource is globally visible and published
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

    # For global: check is_global for backward compat, or visibility == 'global'
    # Agents have a 'status' field; for other resources, global visibility alone suffices
    if hasattr(model, "status"):
        conditions.append(
            and_(model.visibility == "global", model.status == "published")
        )
    else:
        conditions.append(model.visibility == "global")

    return or_(*conditions)
