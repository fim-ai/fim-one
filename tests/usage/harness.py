"""The application on a database of its own, a model provider that bills a
fixed amount per call, and the usage ledger to compare the two.

Import the fixtures a test module needs by name::

    from tests.usage.harness import Usage, usage  # noqa: F401
    from tests.usage.provider import Provider, provider  # noqa: F401
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select

import fim_one.db.models  # noqa: F401
from fim_one.core import utils as core_utils
from fim_one.core.model.usage import set_strict_metering
from fim_one.core.usage import meter as usage_meter
from fim_one.db import create_session, init_db, shutdown_db
from fim_one.db.base import Base
from fim_one.db.models import Agent, Conversation, User
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.usage_ledger import UsageLedger
from fim_one.web import create_app
from fim_one.web.auth import create_access_token

__all__ = ["Usage", "usage", "events"]

_PG = os.environ.get("FIM_TEST_PG_URL")


def events(body: str) -> list[tuple[str, Any]]:
    """The events of a server-sent stream, as ``(name, data)``."""
    found: list[tuple[str, Any]] = []
    for block in body.split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if name:
            found.append((name, json.loads(data) if data else None))
    return found


@dataclass
class Usage:
    """The application, people to act as, and the ledger."""

    app: FastAPI
    users: dict[str, User] = field(default_factory=dict)

    # -- people ------------------------------------------------------------------

    async def user(self, name: str, **fields: Any) -> User:
        row = User(
            id=str(uuid.uuid4()),
            username=name,
            email=f"{name}@example.test",
            password_hash="x",
            **fields,
        )
        async with create_session() as db:
            db.add(row)
            await db.commit()
        self.users[name] = row
        return row

    async def org(self, name: str, owner: User, *members: User) -> Organization:
        row = Organization(id=str(uuid.uuid4()), name=name, slug=name, owner_id=owner.id)
        async with create_session() as db:
            db.add(row)
            await db.flush()
            for member in (owner, *members):
                db.add(
                    OrgMembership(
                        id=str(uuid.uuid4()), org_id=row.id, user_id=member.id, role="member"
                    )
                )
            await db.commit()
        return row

    async def add(self, *rows: Any) -> None:
        async with create_session() as db:
            for row in rows:
                db.add(row)
                await db.flush()
            await db.commit()

    async def agent(self, owner: User, *, org: Organization | None = None, **fields: Any) -> Agent:
        row = Agent(
            id=str(uuid.uuid4()),
            user_id=owner.id,
            name=fields.pop("name", "Helper"),
            instructions="Answer briefly.",
            tool_categories=fields.pop("tool_categories", ["computation"]),
            status="published",
            is_active=True,
            org_id=org.id if org else None,
            visibility="org" if org else "personal",
            **fields,
        )
        await self.add(row)
        return row

    async def subscribe(self, user: User, resource_type: str, resource: Any) -> None:
        """Give *user* the use of someone else's published resource."""
        from fim_one.db.models.resource_subscription import ResourceSubscription

        await self.add(
            ResourceSubscription(
                id=str(uuid.uuid4()),
                user_id=user.id,
                resource_type=resource_type,
                resource_id=resource.id,
                org_id=resource.org_id,
            )
        )

    async def conversation(
        self, owner: User, *, agent: Agent | None = None, **fields: Any
    ) -> Conversation:
        row = Conversation(
            id=str(uuid.uuid4()),
            user_id=owner.id,
            title=fields.pop("title", "t"),
            mode=fields.pop("mode", "react"),
            agent_id=agent.id if agent else None,
            **fields,
        )
        await self.add(row)
        return row

    @staticmethod
    def token(user: User) -> str:
        return create_access_token(user.id, user.email or "")

    def auth(self, user: User) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token(user)}"}

    # -- requests ----------------------------------------------------------------

    async def call(
        self,
        method: str,
        path: str,
        *,
        by: User | None = None,
        expect: int | tuple[int, ...] = (200, 201, 204),
        **kwargs: Any,
    ) -> Response:
        headers = {**(self.auth(by) if by else {}), **kwargs.pop("headers", {})}
        transport = ASGITransport(app=self.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.request(method, path, headers=headers, **kwargs)
        allowed = (expect,) if isinstance(expect, int) else expect
        assert response.status_code in allowed, (
            f"{method} {path} -> {response.status_code}: {response.text[:800]}"
        )
        return response

    async def turn(
        self,
        endpoint: str,
        by: User,
        *,
        conversation: Conversation | None = None,
        agent: Agent | None = None,
        q: str = "say pong",
    ) -> list[tuple[str, Any]]:
        """One chat turn, read to the end; the events it sent."""
        body: dict[str, Any] = {"q": q, "token": self.token(by)}
        if conversation is not None:
            body["conversation_id"] = conversation.id
        if agent is not None:
            body["agent_id"] = agent.id
        response = await self.call("POST", endpoint, json=body)
        return events(response.text)

    # -- the ledger --------------------------------------------------------------

    async def settle(self) -> None:
        """Let the work a request left behind finish, and every write land."""
        for _ in range(200):
            pending = [task for task in core_utils._BACKGROUND_TASKS if not task.done()]
            if not pending:
                break
            await asyncio.wait(pending, timeout=5)
        # Streams abandoned mid-way are closed by the loop a step at a time.
        for _ in range(20):
            await asyncio.sleep(0)
        await usage_meter.flush_all()

    async def rows(self, **where: Any) -> list[UsageLedger]:
        await self.settle()
        async with create_session() as db:
            query = select(UsageLedger)
            for column, value in where.items():
                query = query.where(getattr(UsageLedger, column) == value)
            return list((await db.execute(query)).scalars().all())

    async def recorded(self, **where: Any) -> int:
        """Tokens in the ledger (for the rows matching *where*)."""
        return sum(row.total_tokens for row in await self.rows(**where))

    async def used(self, user: User, since: datetime | None = None) -> int:
        """What the ledger holds against *user* from *since* on."""
        from fim_one.core.usage.ledger import used_since

        await self.settle()
        async with create_session() as db:
            return await used_since(db, user.id, since or datetime(2000, 1, 1, tzinfo=UTC))


@pytest.fixture()
async def usage(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> AsyncIterator[Usage]:
    """The application as it is assembled for production, on an empty database.

    Strict metering is on: a model call made with no usage meter open
    fails the test instead of going unrecorded.
    """
    fd, path = tempfile.mkstemp(suffix=".db", dir=str(tmp_path))
    os.close(fd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-for-the-usage-ledger-tests")
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("VECTOR_STORE_DIR", str(tmp_path / "vector_store"))
    # The models every entry resolves when nothing is configured in the
    # database: the real client, pointed at an address nothing listens on.
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("LLM_MODEL", "main-model")
    monkeypatch.setenv("FAST_LLM_MODEL", "fast-model")
    monkeypatch.setenv("LLM_MAX_RETRIES", "0")
    # The registry of models is built once per process and kept: this
    # test's is built from the settings above, not from an earlier test's.
    from fim_one.web import deps

    monkeypatch.setattr(deps, "_cached_registry", None)
    monkeypatch.setattr(deps, "_cached_registry_version", -1)
    await init_db()
    from fim_one.db import engine as engine_mod

    assert engine_mod._engine is not None
    async with engine_mod._engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from fim_one.web.api import chat as chat_mod

    monkeypatch.setattr(
        chat_mod,
        "_conversation_sandbox_root",
        lambda conversation_id: tmp_path / "sandbox" / conversation_id if conversation_id else None,
    )
    set_strict_metering(True)
    try:
        yield Usage(app=create_app())
        await usage_meter.flush_all()
    finally:
        set_strict_metering(None)
        await shutdown_db()
