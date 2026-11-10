"""Which channel row an agent, or a user acting alone, may send through."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["list_member_channels", "resolve_org_channel"]


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


async def list_member_channels(db: AsyncSession, *, user_id: str) -> list[Any]:
    """The active channels of every organization *user_id* is a member of.

    What a user acting alone may send through: membership is read here, at
    the time of use, so leaving an organization ends access to its channels.
    Ordered by name, then id, so the same query lists them the same way.
    """
    from sqlalchemy import select

    from fim_one.db.models.channel import Channel
    from fim_one.db.models.organization import OrgMembership

    if not user_id:
        return []
    stmt = (
        select(Channel)
        .where(
            Channel.is_active.is_(True),
            Channel.org_id.in_(
                select(OrgMembership.org_id).where(OrgMembership.user_id == user_id)
            ),
        )
        .order_by(Channel.name, Channel.id)
    )
    return list((await db.execute(stmt)).scalars().all())
