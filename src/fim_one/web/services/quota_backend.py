"""The numbers behind the assembly layer's quota check.

``core.assembly.quota`` decides the order of the checks and the refusal
codes; this module answers its three questions from the billing tables and
the admin settings, which only the web layer can read. Registered at
start-up by :func:`fim_one.web.services.assembly_ports.register_assembly_ports`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy import select as sa_select

__all__ = ["WebQuotaBackend", "next_month_reset", "reset_at", "unentitled", "usage_and_cap"]


async def _plan_quota(session: Any, user_id: str) -> int | None:
    """The user's plan-bound ``monthly_token_quota``, or ``None``.

    A query error reads as "no plan": the caller falls through to the
    system default.
    """
    from fim_one.db.models import BillingPlan, User

    try:
        result = await session.execute(
            sa_select(BillingPlan.monthly_token_quota)
            .select_from(User)
            .join(BillingPlan, User.plan_id == BillingPlan.id)
            .where(User.id == user_id)
        )
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None
    except Exception:
        return None


async def usage_and_cap(user_id: str) -> tuple[int, int]:
    """``(tokens used this window, effective cap)`` for *user_id*.

    The cap is resolved override-first:

      1. ``user.token_quota == 0``  → ``0`` (unlimited; admin VIP gift)
      2. ``user.token_quota >  0``  → that value (admin hard cap)
      3. ``user.token_quota IS NULL`` → ``user.plan.monthly_token_quota``
      4. *(fallback)* the system-wide ``default_token_quota`` admin setting
      5. ``0`` → unlimited

    A cap of ``0`` means no cap; usage is then not summed and reads ``0``.
    """
    from fim_one.db import create_session
    from fim_one.db.models import Conversation, User
    from fim_one.web.api.admin_utils import get_setting

    async with create_session() as session:
        result = await session.execute(sa_select(User.token_quota).where(User.id == user_id))
        user_quota: int | None = result.scalar_one_or_none()

        plan_quota = await _plan_quota(session, user_id)

        # Admin override wins over the plan: NULL follows the plan, 0 is
        # unlimited, N > 0 is a hard cap.
        if user_quota is not None:
            if user_quota == 0:
                return (0, 0)
        elif plan_quota is not None:
            user_quota = plan_quota
        else:
            default_str = await get_setting(session, "default_token_quota", "0")
            user_quota = int(default_str) if default_str.isdigit() else 0

        if not user_quota or user_quota <= 0:
            return (0, 0)

        from fim_one.web.services.quota_window import resolve_quota_window

        # The usage window is billing-aligned: paid users reset on their
        # subscription's monthly anniversary, free users on the calendar
        # month. Resolution falls back to the calendar month on failure.
        window_start, _reset_at = await resolve_quota_window(session, user_id)
        monthly_result = await session.execute(
            sa_select(func.coalesce(func.sum(Conversation.total_tokens), 0)).where(
                Conversation.user_id == user_id,
                Conversation.created_at >= window_start,
            )
        )
        monthly_tokens = int(monthly_result.scalar_one() or 0)

        # Workflow runs billed to this user (trigger keys and schedules
        # included) count against the same quota as chat.
        from fim_one.db.models.workflow import WorkflowRun

        wf_result = await session.execute(
            sa_select(func.coalesce(func.sum(WorkflowRun.total_tokens), 0)).where(
                WorkflowRun.user_id == user_id,
                WorkflowRun.created_at >= window_start,
            )
        )
        monthly_tokens += int(wf_result.scalar_one() or 0)
        return (monthly_tokens, int(user_quota))


async def unentitled(user_id: str) -> bool:
    """``paid_only`` and the user has neither a plan nor an admin override.

    A failed lookup reads as entitled.
    """
    from fim_one.db import create_session
    from fim_one.web.services.quota_enforcer import is_user_unentitled

    try:
        async with create_session() as session:
            return await is_user_unentitled(user_id, session)
    except Exception:
        return False


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
