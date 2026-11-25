"""First-administrator bootstrap: one lock, and a token only the operator has."""

from __future__ import annotations

import hashlib
import hmac
import os

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db.models.instance_guard import InstanceGuard
from fim_one.db.models.user import User

_SETUP_TOKEN_ENV = "FIM_SETUP_TOKEN"


def setup_token_configured() -> bool:
    """Whether ``FIM_SETUP_TOKEN`` is set to a non-empty value."""
    return bool(os.environ.get(_SETUP_TOKEN_ENV, "").strip())


def setup_token_matches(provided: str | None) -> bool:
    """Constant-time check of *provided* against ``FIM_SETUP_TOKEN``.

    Both sides are hashed first so the comparison does not depend on length.
    An empty expected or provided value does not match.
    """
    expected = os.environ.get(_SETUP_TOKEN_ENV, "")
    if not expected or not provided:
        return False
    return hmac.compare_digest(
        hashlib.sha256(provided.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


async def locked_user_count(db: AsyncSession) -> int:
    """How many users exist, after taking the instance lock.

    The lock is held until this session commits or rolls back. Callers that
    create the first account must do so before that.
    """
    connection = await db.connection()
    insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
    await db.execute(
        insert(InstanceGuard).values(id=1, nonce=0).on_conflict_do_nothing(index_elements=["id"])
    )
    await db.execute(
        update(InstanceGuard).where(InstanceGuard.id == 1).values(nonce=InstanceGuard.nonce + 1)
    )
    result = await db.execute(select(func.count(User.id)))
    return int(result.scalar_one())
