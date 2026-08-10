"""Tests for the ask_user_question tool and its answer pipeline.

Covers:
- ``normalize_questions`` validation rules (counts, duplicates, headers).
- ``format_answers`` rendering (single, multi-select, unanswered).
- ``AskUserQuestionTool.run`` — answered / dismissed / expired flows
  against an in-memory SQLite DB, plus the listener emission.
- ``confirmation_sse`` — kind-aware event payload + scope fallback.
- ``POST /api/confirmations/{id}/answer`` endpoint behaviour.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fim_one.core.tool.builtin.ask_user_question import (
    AskUserQuestionTool,
    format_answers,
    normalize_questions,
)
from fim_one.db.base import Base
from fim_one.db.models.channel import ConfirmationRequest


QUESTIONS = [
    {
        "question": "Which output format?",
        "header": "Format",
        "options": [
            {"label": "Summary", "description": "Brief overview"},
            {"label": "Detailed", "description": "Full explanation"},
        ],
    },
    {
        "question": "Which sections to include?",
        "header": "Sections",
        "options": [
            {"label": "Intro", "description": "Opening context"},
            {"label": "Conclusion", "description": "Final summary"},
            {"label": "Appendix", "description": "Raw data"},
        ],
        "multi_select": True,
    },
]


# ---------------------------------------------------------------------------
# normalize_questions
# ---------------------------------------------------------------------------


class TestNormalizeQuestions:
    def test_valid_questions_normalized(self) -> None:
        result = normalize_questions(QUESTIONS)
        assert len(result) == 2
        assert result[0]["multi_select"] is False
        assert result[1]["multi_select"] is True
        assert result[0]["header"] == "Format"

    def test_rejects_non_list(self) -> None:
        with pytest.raises(ValueError, match="must be an array"):
            normalize_questions({"question": "?"})

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            normalize_questions([])

    def test_rejects_too_many_questions(self) -> None:
        with pytest.raises(ValueError, match="at most 4"):
            normalize_questions(
                [dict(QUESTIONS[0], question=f"Q{i}?") for i in range(5)]
            )

    def test_rejects_single_option(self) -> None:
        bad = [
            {
                "question": "Pick one?",
                "header": "Pick",
                "options": [{"label": "Only", "description": "x"}],
            }
        ]
        with pytest.raises(ValueError, match="between 2 and 4"):
            normalize_questions(bad)

    def test_rejects_duplicate_question(self) -> None:
        with pytest.raises(ValueError, match="duplicates"):
            normalize_questions([QUESTIONS[0], QUESTIONS[0]])

    def test_rejects_duplicate_option_labels(self) -> None:
        bad = [
            {
                "question": "Pick?",
                "header": "Pick",
                "options": [
                    {"label": "Same", "description": "a"},
                    {"label": "Same", "description": "b"},
                ],
            }
        ]
        with pytest.raises(ValueError, match="duplicates"):
            normalize_questions(bad)

    def test_header_truncated_and_defaulted(self) -> None:
        qs = [dict(QUESTIONS[0], header="A" * 40)]
        assert len(normalize_questions(qs)[0]["header"]) == 12
        qs = [dict(QUESTIONS[0], header="")]
        assert normalize_questions(qs)[0]["header"] == "Q1"


# ---------------------------------------------------------------------------
# format_answers
# ---------------------------------------------------------------------------


class TestFormatAnswers:
    def test_renders_answers(self) -> None:
        qs = normalize_questions(QUESTIONS)
        text = format_answers(
            qs,
            {
                "Which output format?": "Summary",
                "Which sections to include?": ["Intro", "Conclusion"],
            },
        )
        assert '"Which output format?" = "Summary"' in text
        assert '"Which sections to include?" = "Intro, Conclusion"' in text
        assert text.startswith("User answered your questions:")

    def test_marks_unanswered(self) -> None:
        qs = normalize_questions(QUESTIONS)
        text = format_answers(qs, {"Which output format?": "Summary"})
        assert '"Which sections to include?" = (not answered)' in text

    def test_renders_notes(self) -> None:
        qs = normalize_questions(QUESTIONS)
        text = format_answers(
            qs,
            {"Which output format?": "Summary"},
            {"Which output format?": "but keep tables verbose"},
        )
        assert (
            '"Which output format?" = "Summary" '
            "(user note: but keep tables verbose)" in text
        )


# ---------------------------------------------------------------------------
# Tool run() against an in-memory DB
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory(tmp_path: Any) -> Any:
    # File-backed DB: the tool and the test flip rows from concurrent
    # sessions, and in-memory SQLite gives every pooled connection its
    # own private database.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


def make_tool(session_factory: Any, **kwargs: Any) -> AskUserQuestionTool:
    defaults: dict[str, Any] = {
        "session_factory": session_factory,
        "user_id": "user-1",
        "conversation_id": "conv-1",
        "timeout_seconds": 2,
        "poll_interval_seconds": 0.05,
    }
    defaults.update(kwargs)
    return AskUserQuestionTool(**defaults)


async def _flip_row(
    session_factory: Any,
    *,
    status: str,
    response_payload: dict[str, Any] | None = None,
) -> None:
    """Wait for the pending row to appear, then flip its status."""
    for _ in range(100):
        async with session_factory() as session:
            from sqlalchemy import select

            row = (
                await session.execute(select(ConfirmationRequest))
            ).scalar_one_or_none()
            if row is not None:
                row.status = status
                if response_payload is not None:
                    row.response_payload = response_payload
                await session.commit()
                return
        await asyncio.sleep(0.02)
    raise AssertionError("pending ConfirmationRequest row never appeared")


class TestAskUserQuestionTool:
    @pytest.mark.asyncio
    async def test_invalid_questions_return_error_string(
        self, session_factory: Any
    ) -> None:
        tool = make_tool(session_factory)
        result = await tool.run(questions="not a list")
        assert result.startswith("[Error]")

    @pytest.mark.asyncio
    async def test_answered_flow(self, session_factory: Any) -> None:
        tool = make_tool(session_factory)
        run_task = asyncio.create_task(tool.run(questions=QUESTIONS))
        await _flip_row(
            session_factory,
            status="answered",
            response_payload={"answers": {"Which output format?": "Detailed"}},
        )
        result = await run_task
        assert '"Which output format?" = "Detailed"' in result

    @pytest.mark.asyncio
    async def test_answered_flow_with_notes(self, session_factory: Any) -> None:
        tool = make_tool(session_factory)
        run_task = asyncio.create_task(tool.run(questions=QUESTIONS))
        await _flip_row(
            session_factory,
            status="answered",
            response_payload={
                "answers": {"Which output format?": "Summary"},
                "notes": {"Which output format?": "shorter is fine"},
            },
        )
        result = await run_task
        assert "(user note: shorter is fine)" in result

    @pytest.mark.asyncio
    async def test_dismissed_flow(self, session_factory: Any) -> None:
        tool = make_tool(session_factory)
        run_task = asyncio.create_task(tool.run(questions=QUESTIONS))
        await _flip_row(session_factory, status="dismissed")
        result = await run_task
        assert "chose not to answer" in result

    @pytest.mark.asyncio
    async def test_timeout_marks_expired(self, session_factory: Any) -> None:
        tool = make_tool(session_factory, timeout_seconds=0)
        result = await tool.run(questions=QUESTIONS)
        assert "No response from the user" in result
        from sqlalchemy import select

        async with session_factory() as session:
            row = (
                await session.execute(select(ConfirmationRequest))
            ).scalar_one()
            assert row.status == "expired"

    @pytest.mark.asyncio
    async def test_row_shape(self, session_factory: Any) -> None:
        """The committed row carries kind/payload the SSE bridge expects."""
        tool = make_tool(session_factory, agent_id=None, org_id=None)
        run_task = asyncio.create_task(tool.run(questions=QUESTIONS))
        from sqlalchemy import select

        row = None
        for _ in range(100):
            async with session_factory() as session:
                row = (
                    await session.execute(select(ConfirmationRequest))
                ).scalar_one_or_none()
                if row is not None:
                    break
            await asyncio.sleep(0.02)
        assert row is not None
        assert row.kind == "user_question"
        assert row.mode == "inline"
        assert row.org_id is None
        assert row.payload is not None
        assert row.payload["conversation_id"] == "conv-1"
        assert len(row.payload["questions"]) == 2
        # Unblock the run
        await _flip_row(session_factory, status="dismissed")
        await run_task

    @pytest.mark.asyncio
    async def test_listener_fired(self, session_factory: Any) -> None:
        from fim_one.core.hooks.inline_confirmation import (
            set_inline_confirmation_listener,
        )

        seen: list[Any] = []

        async def listener(request: Any) -> None:
            seen.append(request)

        set_inline_confirmation_listener(listener)
        try:
            tool = make_tool(session_factory)
            run_task = asyncio.create_task(tool.run(questions=QUESTIONS))
            await _flip_row(session_factory, status="dismissed")
            await run_task
        finally:
            set_inline_confirmation_listener(None)

        assert len(seen) == 1
        assert seen[0].kind == "user_question"


# ---------------------------------------------------------------------------
# confirmation_sse — kind-aware payload + scope
# ---------------------------------------------------------------------------


class TestConfirmationSse:
    def test_scope_for(self) -> None:
        from fim_one.web.confirmation_sse import scope_for

        assert scope_for("agent-1", "conv-1") == "agent-1"
        assert scope_for("", "conv-1") == "conv:conv-1"
        assert scope_for(None, None) == ""

    def test_user_question_event_payload(self) -> None:
        from datetime import datetime, timezone
        from types import SimpleNamespace

        from fim_one.web.confirmation_sse import _request_to_event_payload

        request = SimpleNamespace(
            id="q-1",
            agent_id=None,
            kind="user_question",
            mode="inline",
            created_at=datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc),
            payload={
                "kind": "user_question",
                "questions": normalize_questions(QUESTIONS),
                "conversation_id": "conv-9",
                "timeout_seconds": 180,
            },
        )
        event = _request_to_event_payload(request)  # type: ignore[arg-type]
        assert event["type"] == "awaiting_user_question"
        assert event["question_id"] == "q-1"
        assert event["conversation_id"] == "conv-9"
        assert len(event["questions"]) == 2
        assert event["timeout_at"] == "2026-08-10T12:03:00+00:00"

    def test_confirmation_event_payload_unchanged(self) -> None:
        from datetime import datetime, timezone
        from types import SimpleNamespace

        from fim_one.web.confirmation_sse import _request_to_event_payload

        request = SimpleNamespace(
            id="c-1",
            agent_id="agent-1",
            kind="confirmation",
            mode="inline",
            created_at=datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc),
            payload={
                "tool_name": "shell_exec",
                "tool_args": {"command": "ls"},
                "mode": "inline",
            },
        )
        event = _request_to_event_payload(request)  # type: ignore[arg-type]
        assert event["type"] == "awaiting_confirmation"
        assert event["confirmation_id"] == "c-1"
        assert event["tool_name"] == "shell_exec"

    @pytest.mark.asyncio
    async def test_listener_routes_by_conversation_scope(self) -> None:
        """Agent-less question requests land on the conv:<id> queue."""
        from datetime import datetime, timezone
        from types import SimpleNamespace

        from fim_one.web import confirmation_sse

        await confirmation_sse.clear_all_queues()
        request = SimpleNamespace(
            id="q-2",
            agent_id=None,
            user_id="user-1",
            kind="user_question",
            mode="inline",
            created_at=datetime.now(timezone.utc),
            payload={
                "kind": "user_question",
                "questions": normalize_questions(QUESTIONS),
                "conversation_id": "conv-42",
                "timeout_seconds": 180,
            },
        )
        await confirmation_sse._listener(request)  # type: ignore[arg-type]
        q = confirmation_sse.queue_for("conv:conv-42", "user-1")
        event = q.get_nowait()
        assert event["type"] == "awaiting_user_question"
        await confirmation_sse.clear_all_queues()


# ---------------------------------------------------------------------------
# Answer endpoint
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def api_client(session_factory: Any) -> Any:
    """Minimal FastAPI app mounting the confirmations router with overrides."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from fim_one.db import get_session
    from fim_one.web.api.confirmations import router
    from fim_one.web.auth import get_current_user

    app = FastAPI()
    app.include_router(router)

    async def _override_session() -> Any:
        async with session_factory() as session:
            yield session

    from types import SimpleNamespace

    current_user = SimpleNamespace(id="user-1", is_admin=False)

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_user] = lambda: current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, current_user


async def _seed_question_row(
    session_factory: Any,
    *,
    user_id: str = "user-1",
    status: str = "pending",
) -> str:
    row = ConfirmationRequest(
        id="q-row-1",
        agent_id=None,
        user_id=user_id,
        approver_user_id=user_id,
        org_id=None,
        channel_id=None,
        mode="inline",
        kind="user_question",
        status=status,
        payload={
            "kind": "user_question",
            "questions": normalize_questions(QUESTIONS),
            "conversation_id": "conv-1",
            "timeout_seconds": 180,
        },
    )
    async with session_factory() as session:
        session.add(row)
        await session.commit()
    return row.id


class TestAnswerEndpoint:
    @pytest.mark.asyncio
    async def test_answer_flips_row(
        self, api_client: Any, session_factory: Any
    ) -> None:
        client, _user = api_client
        row_id = await _seed_question_row(session_factory)
        resp = await client.post(
            f"/api/confirmations/{row_id}/answer",
            json={
                "answers": {
                    "Which output format?": "Summary",
                    "Which sections to include?": ["Intro", "Appendix"],
                }
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "answered"

        from sqlalchemy import select

        async with session_factory() as session:
            row = (
                await session.execute(select(ConfirmationRequest))
            ).scalar_one()
            assert row.status == "answered"
            assert row.response_payload["answers"]["Which output format?"] == (
                "Summary"
            )
            assert row.response_payload["answers"][
                "Which sections to include?"
            ] == ["Intro", "Appendix"]

    @pytest.mark.asyncio
    async def test_answer_with_notes(
        self, api_client: Any, session_factory: Any
    ) -> None:
        client, _user = api_client
        row_id = await _seed_question_row(session_factory)
        resp = await client.post(
            f"/api/confirmations/{row_id}/answer",
            json={
                "answers": {"Which output format?": "Summary"},
                "notes": {"Which output format?": "but bilingual please"},
            },
        )
        assert resp.status_code == 200, resp.text

        from sqlalchemy import select

        async with session_factory() as session:
            row = (
                await session.execute(select(ConfirmationRequest))
            ).scalar_one()
            assert row.response_payload["notes"] == {
                "Which output format?": "but bilingual please"
            }

        status_resp = await client.get(f"/api/confirmations/{row_id}")
        assert status_resp.json()["notes"] == {
            "Which output format?": "but bilingual please"
        }

    @pytest.mark.asyncio
    async def test_notes_unknown_question_422(
        self, api_client: Any, session_factory: Any
    ) -> None:
        client, _user = api_client
        row_id = await _seed_question_row(session_factory)
        resp = await client.post(
            f"/api/confirmations/{row_id}/answer",
            json={
                "answers": {"Which output format?": "Summary"},
                "notes": {"Nonexistent?": "note"},
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_skip_dismisses(
        self, api_client: Any, session_factory: Any
    ) -> None:
        client, _user = api_client
        row_id = await _seed_question_row(session_factory)
        resp = await client.post(
            f"/api/confirmations/{row_id}/answer",
            json={"answers": {}, "skip": True},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "dismissed"

    @pytest.mark.asyncio
    async def test_unknown_question_422(
        self, api_client: Any, session_factory: Any
    ) -> None:
        client, _user = api_client
        row_id = await _seed_question_row(session_factory)
        resp = await client.post(
            f"/api/confirmations/{row_id}/answer",
            json={"answers": {"Nonexistent?": "A"}},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_answers_422(
        self, api_client: Any, session_factory: Any
    ) -> None:
        client, _user = api_client
        row_id = await _seed_question_row(session_factory)
        resp = await client.post(
            f"/api/confirmations/{row_id}/answer",
            json={"answers": {}},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_non_initiator_403(
        self, api_client: Any, session_factory: Any
    ) -> None:
        client, user = api_client
        row_id = await _seed_question_row(session_factory, user_id="someone-else")
        assert user.id != "someone-else"
        resp = await client.post(
            f"/api/confirmations/{row_id}/answer",
            json={"answers": {"Which output format?": "Summary"}},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_already_decided_409(
        self, api_client: Any, session_factory: Any
    ) -> None:
        client, _user = api_client
        row_id = await _seed_question_row(session_factory, status="answered")
        resp = await client.post(
            f"/api/confirmations/{row_id}/answer",
            json={"answers": {"Which output format?": "Summary"}},
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_wrong_kind_404(
        self, api_client: Any, session_factory: Any
    ) -> None:
        client, _user = api_client
        row = ConfirmationRequest(
            id="c-row-1",
            agent_id="agent-1",
            user_id="user-1",
            org_id=None,
            mode="inline",
            kind="confirmation",
            status="pending",
            payload={"tool_name": "shell_exec", "tool_args": {}},
        )
        async with session_factory() as session:
            session.add(row)
            await session.commit()
        resp = await client.post(
            "/api/confirmations/c-row-1/answer",
            json={"answers": {"x": "y"}},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_status_endpoint_returns_questions(
        self, api_client: Any, session_factory: Any
    ) -> None:
        client, _user = api_client
        row_id = await _seed_question_row(session_factory)
        resp = await client.get(f"/api/confirmations/{row_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "user_question"
        assert len(body["questions"]) == 2
        assert body["answers"] is None
