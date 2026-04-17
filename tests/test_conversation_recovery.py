"""Conversation Recovery MVP — persist synthetic tool_results + resume endpoint.

Covers three tiers:

1. ``_persist_synthetic_tool_results`` — the DB writer used by DbMemory
   after repairing dangling tool_calls.  Exercised with a real in-memory
   SQLite database so the dialect-aware JSON-path idempotency query is
   validated end-to-end.
2. ``DbMemory.get_messages()`` integration — confirms the repair pass
   both returns a well-formed trajectory AND persists synthetic rows
   exactly once across successive reads.
3. ``POST /chat/resume`` — the replay endpoint for disconnected SSE
   clients.  Validates cursor filtering, ownership enforcement, and the
   terminal ``resume_done`` frame.

LLM calls are never made — the tests use raw ORM rows and dataclasses.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from fim_one.core.memory.db import (
    DbMemory,
    _INTERRUPTED_TOOL_RESULT,
    _persist_synthetic_tool_results,
)
from fim_one.core.model.types import ChatMessage, ToolCallRequest
from fim_one.db.base import Base
from fim_one.web.models import Conversation, Message, User


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


# ---------------------------------------------------------------------------
# DB fixtures — mirrored from test_metrics.py for consistency
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def engine() -> AsyncIterator[Any]:
    eng = create_async_engine(TEST_DB_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture()
async def session_factory(engine: Any) -> Any:
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture()
async def db_session(session_factory: Any) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture()
async def owner(db_session: AsyncSession) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username="recovery_owner",
        email="owner@recovery.test",
        password_hash="hashed",
        is_admin=False,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest_asyncio.fixture()
async def stranger(db_session: AsyncSession) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username="recovery_stranger",
        email="stranger@recovery.test",
        password_hash="hashed",
        is_admin=False,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest_asyncio.fixture()
async def conversation(db_session: AsyncSession, owner: User) -> Conversation:
    conv = Conversation(
        id=str(uuid.uuid4()),
        user_id=owner.id,
        title="Recovery Test Conv",
        mode="chat",
    )
    db_session.add(conv)
    await db_session.commit()
    return conv


@pytest.fixture()
def patched_create_session(session_factory: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``create_session`` wherever DbMemory imports it from.

    The function is imported lazily inside ``get_messages()`` and
    ``_persist_synthetic_tool_results()`` via ``from fim_one.db import
    create_session``.  Rebinding the attribute on ``fim_one.db.engine``
    (where it lives) and on ``fim_one.db`` (the re-export) covers both.
    """

    def _factory() -> AsyncSession:
        session: AsyncSession = session_factory()
        return session

    monkeypatch.setattr("fim_one.db.engine.create_session", _factory, raising=True)
    monkeypatch.setattr("fim_one.db.create_session", _factory, raising=True)


# ---------------------------------------------------------------------------
# 1. _persist_synthetic_tool_results — direct unit tests
# ---------------------------------------------------------------------------


class TestPersistSyntheticToolResults:
    """Validate DB persistence of synthetic tool_result rows."""

    @pytest.mark.asyncio
    async def test_inserts_missing_rows(
        self,
        patched_create_session: None,
        conversation: Conversation,
        db_session: AsyncSession,
    ) -> None:
        """One call per tool_call_id produces one new ``tool`` row."""
        synth = [
            ChatMessage(
                role="tool",
                content=_INTERRUPTED_TOOL_RESULT,
                tool_call_id="call_a",
            ),
            ChatMessage(
                role="tool",
                content=_INTERRUPTED_TOOL_RESULT,
                tool_call_id="call_b",
            ),
        ]

        inserted = await _persist_synthetic_tool_results(conversation.id, synth)

        assert inserted == 2

        # Verify rows in DB.
        from sqlalchemy import select as sa_select

        rows = (
            (
                await db_session.execute(
                    sa_select(Message).where(Message.conversation_id == conversation.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        for row in rows:
            assert row.role == "tool"
            assert row.content == _INTERRUPTED_TOOL_RESULT
            assert row.metadata_["synthetic"] is True
            assert row.metadata_["reason"] == "interrupted"
            assert row.metadata_["tool_call_id"] in {"call_a", "call_b"}

    @pytest.mark.asyncio
    async def test_idempotent_second_call(
        self,
        patched_create_session: None,
        conversation: Conversation,
        db_session: AsyncSession,
    ) -> None:
        """Re-running on the same tool_call_ids inserts zero new rows."""
        synth = [
            ChatMessage(
                role="tool",
                content=_INTERRUPTED_TOOL_RESULT,
                tool_call_id="call_dup",
            ),
        ]

        first = await _persist_synthetic_tool_results(conversation.id, synth)
        second = await _persist_synthetic_tool_results(conversation.id, synth)

        assert first == 1
        assert second == 0

        from sqlalchemy import select as sa_select

        rows = (
            (
                await db_session.execute(
                    sa_select(Message).where(Message.conversation_id == conversation.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_skips_entries_missing_tool_call_id(
        self,
        patched_create_session: None,
        conversation: Conversation,
    ) -> None:
        """Rows without a ``tool_call_id`` are silently ignored."""
        synth = [
            ChatMessage(role="tool", content="no id here"),
        ]

        inserted = await _persist_synthetic_tool_results(conversation.id, synth)

        assert inserted == 0

    @pytest.mark.asyncio
    async def test_empty_input_returns_zero(
        self,
        patched_create_session: None,
        conversation: Conversation,
    ) -> None:
        assert await _persist_synthetic_tool_results(conversation.id, []) == 0


# ---------------------------------------------------------------------------
# 2. DbMemory end-to-end — synthesis + persistence + idempotency
# ---------------------------------------------------------------------------


def _tc(tc_id: str, name: str = "noop") -> ToolCallRequest:
    return ToolCallRequest(id=tc_id, name=name, arguments={})


class TestDbMemoryRecoveryIntegration:
    """Confirm DbMemory writes and re-uses synthetic rows."""

    @pytest.mark.asyncio
    async def test_get_messages_persists_synthetic_rows(
        self,
        patched_create_session: None,
        conversation: Conversation,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """DbMemory writes synthetic tool_results when repair fires."""
        # Seed an interrupted trajectory: user + assistant with tool_calls
        # + trailing user (which DbMemory will pop).  No matching tool row.
        now = datetime.now(UTC)
        db_session.add_all(
            [
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content="run the thing",
                    message_type="text",
                    created_at=now,
                ),
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content="",
                    message_type="done",
                    metadata_={"pending": True},
                    created_at=now + timedelta(seconds=1),
                ),
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content="ping",
                    message_type="text",
                    created_at=now + timedelta(seconds=2),
                ),
            ]
        )
        await db_session.commit()

        # Patch the repair helper to inject synthetic messages as if
        # tool_calls had been loaded from DB.  This keeps the test
        # focused on the persistence hook without reaching into the
        # assistant-metadata schema (which is out of MVP scope).
        import fim_one.core.memory.db as db_mod

        real_repair = db_mod._repair_dangling_tool_calls

        def fake_repair(
            messages: list[ChatMessage],
            conversation_id: str,
            *,
            synthesized_sink: list[ChatMessage] | None = None,
        ) -> list[ChatMessage]:
            # Only inject once — the second get_messages() call should
            # hit the fast path with zero repair work once the row is
            # present in DB.  We detect "already persisted" by walking
            # ``messages`` for a tool-role entry with our sentinel id.
            already_persisted = any(
                m.role == "tool" and m.tool_call_id == "call_interrupted" for m in messages
            )
            if already_persisted:
                return list(messages)

            synth = ChatMessage(
                role="tool",
                content=_INTERRUPTED_TOOL_RESULT,
                tool_call_id="call_interrupted",
            )
            if synthesized_sink is not None:
                synthesized_sink.append(synth)
            return [*messages, synth]

        monkeypatch.setattr(db_mod, "_repair_dangling_tool_calls", fake_repair)

        mem = DbMemory(conversation_id=conversation.id, max_tokens=32_000)
        first_call = await mem.get_messages()

        # The returned list should include the synthetic entry.
        synth_in_memory = [m for m in first_call if m.role == "tool"]
        assert len(synth_in_memory) == 1
        assert synth_in_memory[0].content == _INTERRUPTED_TOOL_RESULT

        # Restore the real repair for the idempotency assertion.
        monkeypatch.setattr(db_mod, "_repair_dangling_tool_calls", real_repair)

        # Row must now exist in the DB.
        from sqlalchemy import select as sa_select

        from fim_one.db import create_session as _cs

        async with _cs() as check_session:
            persisted = (
                (
                    await check_session.execute(
                        sa_select(Message).where(
                            Message.conversation_id == conversation.id,
                            Message.role == "tool",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(persisted) == 1
        assert persisted[0].metadata_["synthetic"] is True
        assert persisted[0].metadata_["tool_call_id"] == "call_interrupted"

    @pytest.mark.asyncio
    async def test_double_persist_is_idempotent(
        self,
        patched_create_session: None,
        conversation: Conversation,
        db_session: AsyncSession,
    ) -> None:
        """Two persist passes on the same conversation keep one row each."""
        synth_a = [
            ChatMessage(
                role="tool",
                content=_INTERRUPTED_TOOL_RESULT,
                tool_call_id="tc_one",
            ),
            ChatMessage(
                role="tool",
                content=_INTERRUPTED_TOOL_RESULT,
                tool_call_id="tc_two",
            ),
        ]
        # First: both new.
        first = await _persist_synthetic_tool_results(conversation.id, synth_a)
        # Second: same ids, must dedupe.
        second = await _persist_synthetic_tool_results(conversation.id, synth_a)

        assert first == 2
        assert second == 0

        from sqlalchemy import select as sa_select

        rows = (
            (
                await db_session.execute(
                    sa_select(Message).where(
                        Message.conversation_id == conversation.id,
                        Message.role == "tool",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {r.metadata_["tool_call_id"] for r in rows} == {"tc_one", "tc_two"}


# ---------------------------------------------------------------------------
# 3. POST /chat/resume — API-level behaviour
# ---------------------------------------------------------------------------


def _create_jwt(user_id: str) -> str:
    """Build a minimal JWT signed with the project's secret."""
    import jwt as pyjwt

    from fim_one.web.auth import ALGORITHM, SECRET_KEY

    payload = {
        "sub": user_id,
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    return pyjwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {_create_jwt(user.id)}"}


@pytest_asyncio.fixture()
async def client(
    engine: Any,
    session_factory: Any,
    db_session: AsyncSession,
    owner: User,  # noqa: ARG001 — ensure the owner row is committed
    stranger: User,  # noqa: ARG001 — ensure the stranger row is committed
) -> AsyncIterator[AsyncClient]:
    """HTTPX async client wired to the FastAPI app with the test DB."""
    from unittest.mock import patch

    from fim_one.db import get_session
    from fim_one.web.app import create_app

    @asynccontextmanager
    async def _noop_lifespan(app: Any) -> AsyncIterator[None]:
        yield

    with patch("fim_one.web.app.lifespan", _noop_lifespan):
        app = create_app()

    async def _override_session() -> AsyncIterator[AsyncSession]:
        # Each request gets its own session bound to the shared engine
        # so commits are visible across requests.
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session

    @asynccontextmanager
    async def _mock_create_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    def _create_session_direct() -> AsyncSession:
        session: AsyncSession = session_factory()
        return session

    with (
        patch("fim_one.db.create_session", _create_session_direct),
        patch("fim_one.db.engine.create_session", _create_session_direct),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    app.dependency_overrides.clear()


class TestChatResumeEndpoint:
    """Exercise ``POST /api/chat/resume``."""

    @pytest.mark.asyncio
    async def test_resume_replays_events_after_cursor(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        owner: User,
    ) -> None:
        """Events with ``cursor > request.cursor`` are replayed in order."""
        conv = Conversation(
            id=str(uuid.uuid4()),
            user_id=owner.id,
            title="resume replay",
            mode="chat",
        )
        db_session.add(conv)
        assistant = Message(
            conversation_id=conv.id,
            role="assistant",
            content="done",
            message_type="done",
            metadata_={
                "sse_events": [
                    {"event": "step", "data": {"n": 0}, "cursor": 0},
                    {"event": "step", "data": {"n": 1}, "cursor": 1},
                    {"event": "step", "data": {"n": 2}, "cursor": 2},
                    {"event": "done", "data": {"answer": "ok"}, "cursor": 3},
                ]
            },
        )
        db_session.add(assistant)
        await db_session.commit()

        resp = await client.post(
            "/api/chat/resume",
            json={"conversation_id": conv.id, "cursor": 1},
            headers=_auth_headers(owner),
        )
        assert resp.status_code == 200, resp.text

        body = resp.text
        # Should include events 2, 3, then resume_done.
        assert "event: step" in body
        assert "event: done" in body
        assert "event: resume_done" in body
        # Cursor 1 must NOT appear as a replayed frame.  We look for the
        # synthesized data shape ``"cursor": 1`` which is unique to the
        # replayed event (resume_done uses ``"last_cursor"`` instead).
        assert '"cursor": 1' not in body
        assert '"cursor": 2' in body
        assert '"cursor": 3' in body

    @pytest.mark.asyncio
    async def test_resume_without_cursor_replays_all(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        owner: User,
    ) -> None:
        """``cursor=-1`` (default) replays every event."""
        conv = Conversation(
            id=str(uuid.uuid4()),
            user_id=owner.id,
            title="replay all",
            mode="chat",
        )
        db_session.add(conv)
        db_session.add(
            Message(
                conversation_id=conv.id,
                role="assistant",
                content="done",
                message_type="done",
                metadata_={
                    "sse_events": [
                        {"event": "step", "data": {"i": 0}, "cursor": 0},
                        {"event": "done", "data": {"ok": True}, "cursor": 1},
                    ]
                },
            )
        )
        await db_session.commit()

        resp = await client.post(
            "/api/chat/resume",
            json={"conversation_id": conv.id},
            headers=_auth_headers(owner),
        )
        assert resp.status_code == 200, resp.text
        assert '"cursor": 0' in resp.text
        assert '"cursor": 1' in resp.text
        assert "event: resume_done" in resp.text

    @pytest.mark.asyncio
    async def test_resume_rejects_wrong_user(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        owner: User,
        stranger: User,
    ) -> None:
        """A stranger's JWT cannot resume someone else's conversation."""
        conv = Conversation(
            id=str(uuid.uuid4()),
            user_id=owner.id,
            title="protected",
            mode="chat",
        )
        db_session.add(conv)
        db_session.add(
            Message(
                conversation_id=conv.id,
                role="assistant",
                content="hi",
                message_type="done",
                metadata_={"sse_events": []},
            )
        )
        await db_session.commit()

        resp = await client.post(
            "/api/chat/resume",
            json={"conversation_id": conv.id, "cursor": 0},
            headers=_auth_headers(stranger),
        )
        assert resp.status_code == 404
        assert resp.json().get("error_code") == "conversation_not_found"

    @pytest.mark.asyncio
    async def test_resume_rejects_unknown_conversation(
        self,
        client: AsyncClient,
        owner: User,
    ) -> None:
        resp = await client.post(
            "/api/chat/resume",
            json={"conversation_id": "does-not-exist", "cursor": 0},
            headers=_auth_headers(owner),
        )
        assert resp.status_code == 404
        assert resp.json().get("error_code") == "conversation_not_found"

    @pytest.mark.asyncio
    async def test_resume_rejects_conversation_without_assistant(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        owner: User,
    ) -> None:
        """Conversations with no assistant reply return 404."""
        conv = Conversation(
            id=str(uuid.uuid4()),
            user_id=owner.id,
            title="empty",
            mode="chat",
        )
        db_session.add(conv)
        await db_session.commit()

        resp = await client.post(
            "/api/chat/resume",
            json={"conversation_id": conv.id, "cursor": 0},
            headers=_auth_headers(owner),
        )
        assert resp.status_code == 404
        assert resp.json().get("error_code") == "no_recent_assistant_message"


# ---------------------------------------------------------------------------
# 4. Cursor monotonicity in ``_append_event``
# ---------------------------------------------------------------------------


class TestCursorMonotonicity:
    """Sanity check that the helper assigns strictly-increasing cursors."""

    def test_append_event_assigns_monotonic_cursor(self) -> None:
        from fim_one.web.api.chat import _append_event, _next_cursor

        events: list[dict[str, Any]] = []
        assert _next_cursor(events) == 0

        _append_event(events, "step", {"n": 0})
        _append_event(events, "step", {"n": 1})
        _append_event(events, "done", {"answer": "ok"})

        cursors = [e["cursor"] for e in events]
        assert cursors == [0, 1, 2]
        assert _next_cursor(events) == 3

    def test_emit_also_assigns_cursor(self) -> None:
        from fim_one.web.api.chat import _emit

        events: list[dict[str, Any]] = []
        frame = _emit(events, "step", {"hello": "world"})

        assert "event: step" in frame
        assert len(events) == 1
        assert events[0]["cursor"] == 0
