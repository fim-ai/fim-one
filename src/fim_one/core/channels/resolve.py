"""Which channel row an agent, or a user acting alone, may send through."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["resolve_org_channel"]


async def resolve_org_channel(
    db: AsyncSession,
    *,
    channel_id: str | None,
    org_id: str | None,
    owner_id: str | None,
) -> Any:
    """The active channel *channel_id*, if it lies inside the sender's organization.

    Only an explicitly chosen channel qualifies: nothing here falls back to
    "the first active channel of the organization", which would post to a
    chat whose members nobody chose.

    The channel must belong to *org_id* when there is one (the agent's own
    organization); otherwise, for a draft or personal agent, to an
    organization *owner_id* is a member of. A channel outside that would
    carry the message, tool arguments included, into a foreign chat, so it
    is treated as no channel. Returns the ``Channel`` row or ``None``.
    """
    from sqlalchemy import select

    from fim_one.db.models.channel import Channel
    from fim_one.db.models.organization import OrgMembership

    if not channel_id:
        return None
    stmt = select(Channel).where(Channel.id == channel_id, Channel.is_active.is_(True))
    if org_id:
        stmt = stmt.where(Channel.org_id == org_id)
    elif owner_id:
        stmt = stmt.where(
            Channel.org_id.in_(
                select(OrgMembership.org_id).where(OrgMembership.user_id == owner_id)
            )
        )
    else:
        return None
    return (await db.execute(stmt.limit(1))).scalar_one_or_none()
