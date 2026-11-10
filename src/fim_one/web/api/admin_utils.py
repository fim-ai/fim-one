"""Shared utilities for admin API modules."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.core.audit import writer as audit
from fim_one.db.models import SystemSetting, User


async def get_setting(db: AsyncSession, key: str, default: str = "") -> str:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    return row.value if row is not None else default


async def set_setting(db: AsyncSession, key: str, value: str, *, commit: bool = True) -> None:
    """Store a system setting. With ``commit=False`` the caller commits."""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        db.add(SystemSetting(key=key, value=value))
        # Visible to a later read of the same key in this transaction.
        await db.flush()
    else:
        row.value = value
    if commit:
        await db.commit()


async def stage_audit(
    db: AsyncSession,
    admin: User,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    target_label: str | None = None,
    detail: str | None = None,
) -> None:
    """Add an audit log entry to *db*; the caller's commit stores it with the change.

    For changes to permissions, secrets and what users may do (administrator
    rights, account state, passwords, API keys, IP rules, settings, invite
    codes, quotas and plans): the change and its record are stored together
    or not at all. Call it before ``db.commit()``.
    """
    await audit.record(
        action,
        actor=audit.actor_of(admin),
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        detail=detail,
        db=db,
    )


async def write_audit(
    db: AsyncSession,
    admin: User,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    target_label: str | None = None,
    detail: str | None = None,
) -> None:
    """Append an audit log entry in a commit of its own, after the change's commit.

    If this write fails the change is already stored and has no record,
    so it is kept for actions where that is acceptable (content
    moderation, model configuration, exports and views, clean-ups) and for
    callers whose change is committed by a service they call. Everything
    else uses :func:`stage_audit`.
    """
    await stage_audit(db, admin, action, target_type, target_id, target_label, detail)
    await db.commit()
