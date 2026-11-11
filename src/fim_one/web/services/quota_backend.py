"""The numbers behind the assembly layer's quota check.

``core.assembly.quota`` decides the order of the checks and the refusal
codes; this module answers its three questions from the billing tables and
the admin settings, which only the web layer can read. Registered at
start-up by :func:`fim_one.web.services.assembly_ports.register_assembly_ports`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "WebQuotaBackend",
    "next_month_reset",
    "reset_at",
    "unentitled",
    "usage_and_cap",
    "window_usage",
]


async def window_usage(session: AsyncSession, user_id: str) -> tuple[int, datetime]:
    """``(tokens used in the current quota window, when it resets)``.

    What counts against a user's quota is what the usage ledger holds
    against them since the window began: every model call made on their
    account, from chat, workflow runs billed to them (trigger keys and
    schedules included), the AI-assisted builders, KB and DB chat and
    evaluation runs, whether the turn or run finished, failed or was
    abandoned. The ledger is append-only, so the figure never goes down
    within a window: deleting a conversation, a run or a workflow does not
    give anything back. The enforced number and the one the usage card
    shows are both this.

    Usage belongs to the window it was recorded in, not to the window its
    conversation was created in.

    The window is billing-aligned: paid users reset on their subscription's
    monthly anniversary, free users on the calendar month. Resolution falls
    back to the calendar month on failure.
    """
    from fim_one.core.usage.ledger import used_since
    from fim_one.web.services.quota_window import resolve_quota_window

    window_start, window_reset = await resolve_quota_window(session, user_id)
    return await used_since(session, user_id, window_start), window_reset


async def usage_and_cap(user_id: str) -> tuple[int, int]:
    """``(tokens used this window, effective cap)`` for *user_id*.

    The cap is the one chain in ``quota_enforcer.get_user_quota_by_id``:
    admin override, then the plan (only while the instance sells access by
    plan), then the system default. The usage card reads the same function,
    and its "used" figure is :func:`window_usage`, as here.
    A cap of ``0`` means no cap; usage is then not summed and reads ``0``.
    A failed lookup raises: the caller refuses, it does not let the run
    through on a guess.
    """
    from fim_one.db import create_session
    from fim_one.web.services.quota_enforcer import get_user_quota_by_id

    async with create_session() as session:
        cap = await get_user_quota_by_id(user_id, session)
        # ``None`` is unlimited. ``0`` is a user with no plan where access
        # is sold by plan: that refusal is :func:`unentitled`'s to make.
        if cap is None or cap <= 0:
            return (0, 0)

        monthly_tokens, _reset_at = await window_usage(session, user_id)
        return (monthly_tokens, int(cap))


async def unentitled(user_id: str) -> bool:
    """``paid_only`` and the user has neither a plan nor an admin override."""
    from fim_one.db import create_session
    from fim_one.web.services.quota_enforcer import is_user_unentitled

    async with create_session() as session:
        return await is_user_unentitled(user_id, session)


def next_month_reset() -> datetime:
    """The first day of next calendar month, 00:00 UTC."""
    today = date.today()
    if today.month == 12:
        return datetime(today.year + 1, 1, 1, tzinfo=UTC)
    return datetime(today.year, today.month + 1, 1, tzinfo=UTC)


async def reset_at(user_id: str) -> datetime:
    """When *user_id*'s usage window next resets.

    Billing-aligned (see :func:`usage_and_cap`); the calendar-month boundary
    when the window cannot be resolved.
    """
    from fim_one.db import create_session
    from fim_one.web.services.quota_window import resolve_quota_window

    try:
        async with create_session() as session:
            _start, reset = await resolve_quota_window(session, user_id)
        return reset
    except Exception:
        return next_month_reset()


class WebQuotaBackend:
    """``core.assembly.quota.QuotaBackend`` over this module's functions.

    The functions are looked up when called, so a test that replaces one of
    them replaces it for the registered backend too.
    """

    async def usage_and_cap(self, user_id: str) -> tuple[int, int]:
        return await usage_and_cap(user_id)

    async def unentitled(self, user_id: str) -> bool:
        return await unentitled(user_id)

    async def reset_at(self, user_id: str) -> datetime:
        return await reset_at(user_id)
