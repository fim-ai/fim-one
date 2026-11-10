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


async def set_setting(db: AsyncSession, key: str, value: str) -> None:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        row.value = value
    await db.commit()


async def write_audit(
    db: AsyncSession,
    admin: User,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    target_label: str | None = None,
    detail: str | None = None,
) -> None:
    """Append an audit log entry. Call after the main db.commit() succeeds.

    A thin wrapper over :func:`fim_one.core.audit.writer.record` for the
    platform administration endpoints: the entry is committed here, in a
    commit of its own after the change it describes.
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
    await db.commit()
