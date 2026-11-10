"""Driving the real application for the audit tests.

Each audit test makes an HTTP request against the application as it is
assembled for production (middleware included) on a SQLite file, then
reads the audit tables. The request arrives from :data:`CLIENT_IP`, so a
row that carries it proves the address travelled from the HTTP layer to
the writer.

Import the fixtures a test module needs by name::

    from tests.audit.harness import World, world  # noqa: F401
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base
from fim_one.db.models import AuditLog, LoginHistory, User
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.review_log import ReviewLog

__all__ = ["CLIENT_IP", "USER_AGENT", "World", "world"]

CLIENT_IP = "203.0.113.7"
USER_AGENT = "audit-tests/1.0"
PASSWORD = "correct horse battery staple"


def _row_text(row: Any) -> str:
    """Every stored column of *row*, as one searchable string."""
    values = {column.key: getattr(row, column.key) for column in row.__mapper__.column_attrs}
    return json.dumps(values, default=str, ensure_ascii=False)


@dataclass
class World:
    """The application, a client that reaches it from :data:`CLIENT_IP`, and its database."""

    client: AsyncClient
    factory: async_sessionmaker[AsyncSession]
    app: Any

    # -- people and organizations -------------------------------------------

    async def user(
        self,
        name: str,
        *,
        admin: bool = False,
        password: str | None = None,
        **fields: Any,
    ) -> User:
        from fim_one.web.auth import hash_password

        row = User(
            id=str(uuid.uuid4()),
            username=name,
            email=f"{name}@example.test",
            is_admin=admin,
            email_verified=True,
            password_hash=hash_password(password) if password else None,
            **fields,
        )
        async with self.factory() as db:
            db.add(row)
            await db.commit()
        return row

    async def org(self, name: str, owner: User, **members: User) -> Organization:
        """An organization owned by *owner*; ``admin=…`` / ``member=…`` add one member each."""
        row = Organization(id=str(uuid.uuid4()), name=name, slug=name.lower(), owner_id=owner.id)
        async with self.factory() as db:
            db.add(row)
            await db.flush()
            db.add(OrgMembership(org_id=row.id, user_id=owner.id, role="owner"))
            for role, member in members.items():
                db.add(OrgMembership(org_id=row.id, user_id=member.id, role=role))
            await db.commit()
        return row

    async def add(self, *rows: Any) -> None:
        async with self.factory() as db:
            db.add_all(rows)
            await db.commit()

    async def get(self, model: Any, key: str) -> Any:
        async with self.factory() as db:
            return await db.get(model, key)

    # -- requests ----------------------------------------------------------

    @staticmethod
    def auth(user: User) -> dict[str, str]:
        import jwt as pyjwt

        from fim_one.web.auth import ALGORITHM, SECRET_KEY

        now = datetime.now(UTC)
        token = pyjwt.encode(
            {"sub": user.id, "type": "access", "iat": now, "exp": now + timedelta(hours=1)},
            SECRET_KEY,
            algorithm=ALGORITHM,
        )
        return {"Authorization": f"Bearer {token}"}

    async def call(
        self,
        method: str,
        path: str,
        *,
        by: User | None = None,
        expect: int | tuple[int, ...] = (200, 201, 204),
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Response:
        """One request, as *by* (or nobody). Fails the test on an unexpected status."""
        sent = {"User-Agent": USER_AGENT, **(self.auth(by) if by else {}), **(headers or {})}
        response = await self.client.request(method, path, headers=sent, **kwargs)
        allowed = (expect,) if isinstance(expect, int) else expect
        assert response.status_code in allowed, (
            f"{method} {path} -> {response.status_code}: {response.text[:500]}"
        )
        return response

    # -- what was recorded -------------------------------------------------------

    async def events(self, action: str | None = None) -> list[AuditLog]:
        async with self.factory() as db:
            # In the order written: timestamps have one-second resolution here.
            query = select(AuditLog).order_by(text("rowid"))
            if action is not None:
                query = query.where(AuditLog.action == action)
            return list((await db.execute(query)).scalars().all())

    async def logins(self) -> list[LoginHistory]:
        async with self.factory() as db:
            query = select(LoginHistory).order_by(text("rowid"))
            return list((await db.execute(query)).scalars().all())

    async def reviews(self) -> list[ReviewLog]:
        async with self.factory() as db:
            return list((await db.execute(select(ReviewLog))).scalars().all())

    async def one(
        self,
        action: str,
        *,
        actor: User | None,
        org: Organization | str | None = None,
        target: tuple[str | None, str | None] | None = None,
        outcome: str = "success",
        changes: dict[str, Any] | None = None,
        ip: str | None = CLIENT_IP,
    ) -> AuditLog:
        """The single *action* event, checked: actor, organization, target, outcome, address.

        *changes* is compared when given. Everything else about the row is
        the caller's to assert.
        """
        rows = await self.events(action)
        assert len(rows) == 1, f"{action}: {len(rows)} rows, expected 1"
        row = rows[0]
        if actor is not None:
            assert (row.actor_id, row.actor_label) == (actor.id, actor.username or actor.email)
            assert row.actor_kind == ("admin" if actor.is_admin else "user")
        org_id = org.id if isinstance(org, Organization) else org
        assert row.org_id == org_id, f"{action}: org {row.org_id!r}, expected {org_id!r}"
        if target is not None:
            assert (row.target_type, row.target_id) == target
        assert row.outcome == outcome
        if changes is not None:
            assert row.changes == changes
        assert row.ip_address == ip
        if ip is not None:
            assert row.user_agent == USER_AGENT and row.request_id
        return row

    @staticmethod
    def assert_absent(row: Any, *secrets: str) -> None:
        """None of *secrets* is anywhere in *row*."""
        text = _row_text(row)
        for secret in secrets:
            assert secret and secret not in text, f"{secret!r} is in the audit row: {text}"


@pytest.fixture()
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[World]:
    import fim_one.db.engine as db_engine
    from fim_one.web.app import create_app

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_conn: Any, _record: Any) -> None:
        dbapi_conn.execute("PRAGMA foreign_keys=ON")  # as the app's engine does

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # ``get_session`` and ``create_session`` both read the module's factory.
    monkeypatch.setattr(db_engine, "_engine", engine)
    monkeypatch.setattr(db_engine, "_session_factory", factory)

    @asynccontextmanager
    async def _no_lifespan(_app: Any) -> AsyncIterator[None]:
        yield

    with patch("fim_one.web.app.lifespan", _no_lifespan):
        app = create_app()
    transport = ASGITransport(app=app, client=(CLIENT_IP, 50123))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield World(client=client, factory=factory, app=app)
    await engine.dispose()
