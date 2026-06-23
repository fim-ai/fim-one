"""Database connectors are not shareable (yet).

Sharing a DB connector would expose raw SQL on the owner's (typically
high-privilege) database account with no per-caller scoping, and a subscriber
would get nothing safe to use until the declarative-action layer ships. So
``publish_connector`` rejects ``type == "database"`` at the source — for both
org and global scope. (The tool-assembly layer also gates raw SQL to owner-only
as defense-in-depth; see test_db_raw_sql_owner_gate.py.)
"""

from __future__ import annotations

import uuid
from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fim_one.db.base import Base
from fim_one.db.models.connector import Connector
from fim_one.db.models.user import User
from fim_one.web.api.connectors import publish_connector
from fim_one.web.exceptions import AppError
from fim_one.web.schemas.common import PublishRequest


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    import fim_one.db.models  # noqa: F401

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _make_user(session: AsyncSession) -> User:
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@x.io", is_admin=True)
    session.add(user)
    await session.flush()
    return user


async def _make_connector(session: AsyncSession, owner: str, ctype: str) -> Connector:
    conn = Connector(
        user_id=owner,
        name="c",
        type=ctype,
        base_url=None if ctype == "database" else "https://x",
        db_config="enc" if ctype == "database" else None,
        status="draft",
    )
    session.add(conn)
    await session.flush()
    return conn


class TestDbConnectorNotShareable:
    @pytest.mark.asyncio
    async def test_publish_db_connector_to_org_rejected(
        self, session: AsyncSession
    ) -> None:
        user = await _make_user(session)
        conn = await _make_connector(session, user.id, "database")
        with pytest.raises(AppError) as exc:
            await publish_connector(
                conn.id,
                PublishRequest(scope="org", org_id=str(uuid.uuid4())),
                current_user=user,
                db=session,
            )
        assert exc.value.error_code == "db_connector_not_shareable"

    @pytest.mark.asyncio
    async def test_publish_db_connector_to_global_rejected(
        self, session: AsyncSession
    ) -> None:
        user = await _make_user(session)
        conn = await _make_connector(session, user.id, "database")
        with pytest.raises(AppError) as exc:
            await publish_connector(
                conn.id,
                PublishRequest(scope="global"),
                current_user=user,
                db=session,
            )
        assert exc.value.error_code == "db_connector_not_shareable"

    @pytest.mark.asyncio
    async def test_publish_api_connector_not_blocked_by_type_guard(
        self, session: AsyncSession
    ) -> None:
        # An API connector must NOT hit the db_connector_not_shareable guard.
        # (It may still fail later for other reasons, but never with this code.)
        user = await _make_user(session)
        conn = await _make_connector(session, user.id, "api")
        try:
            await publish_connector(
                conn.id,
                PublishRequest(scope="global"),
                current_user=user,
                db=session,
            )
        except AppError as e:
            assert e.error_code != "db_connector_not_shareable"
