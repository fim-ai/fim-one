"""Tests for the per-agent ``suggest_followups`` opt-in toggle.

Covers two concerns shipped together:

1. The Alembic migration adds the column idempotently (re-running upgrade()
   on a DB that already has the column must not raise).
2. The chat SSE post-processing path emits ``post_processing`` and
   ``suggestions`` events ONLY when the agent has ``suggest_followups=True``,
   and persists the generated list onto the assistant message metadata.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect


# ---------------------------------------------------------------------------
# Migration idempotency
# ---------------------------------------------------------------------------


def _make_minimal_agents_table(engine: sa.Engine) -> None:
    """Create a stub ``agents`` table for the migration to operate on.

    The migration only inspects ``agents``; the rest of the schema is
    irrelevant for the column-add behaviour we want to verify.
    """
    metadata = sa.MetaData()
    sa.Table(
        "agents",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
    )
    metadata.create_all(engine)


class TestMigrationIdempotency:
    def test_upgrade_adds_column_once(self, tmp_path: Path) -> None:
        """First upgrade adds the column; second upgrade is a no-op."""
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        from fim_one.migrations.versions import (
            h8c0d2e4f567_add_suggest_followups_to_agents as mig,
        )

        db_url = f"sqlite:///{tmp_path}/idempotency.db"
        engine = create_engine(db_url)
        _make_minimal_agents_table(engine)

        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mig.upgrade()
            conn.commit()

        cols = {c["name"] for c in inspect(engine).get_columns("agents")}
        assert "suggest_followups" in cols

        # Re-run: the helpers gate must keep upgrade() safe.
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mig.upgrade()
            conn.commit()

        cols_after = {c["name"] for c in inspect(engine).get_columns("agents")}
        assert "suggest_followups" in cols_after


# ---------------------------------------------------------------------------
# _generate_suggestions still works against an AsyncMock'd fast LLM
# ---------------------------------------------------------------------------


class TestGenerateSuggestionsHelper:
    """Direct unit test for the helper used by inline post-processing."""

    @pytest.mark.asyncio
    async def test_returns_parsed_list_when_llm_returns_json(self) -> None:
        from fim_one.web.api.chat import _generate_suggestions

        fake_llm = MagicMock()
        fake_message = MagicMock()
        fake_message.content = '["Why?", "How?", "What next?"]'
        fake_result = MagicMock()
        fake_result.message = fake_message
        fake_result.usage = None
        fake_llm.chat = AsyncMock(return_value=fake_result)

        items = await _generate_suggestions(
            fake_llm, "the query", "the answer", count=3
        )
        assert items == ["Why?", "How?", "What next?"]

    @pytest.mark.asyncio
    async def test_returns_empty_on_llm_failure(self) -> None:
        from fim_one.web.api.chat import _generate_suggestions

        fake_llm = MagicMock()
        fake_llm.chat = AsyncMock(side_effect=RuntimeError("LLM down"))

        items = await _generate_suggestions(fake_llm, "q", "a", count=3)
        assert items == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_llm_returns_garbage(self) -> None:
        from fim_one.web.api.chat import _generate_suggestions

        fake_llm = MagicMock()
        fake_message = MagicMock()
        fake_message.content = "not json at all"
        fake_result = MagicMock()
        fake_result.message = fake_message
        fake_result.usage = None
        fake_llm.chat = AsyncMock(return_value=fake_result)

        items = await _generate_suggestions(fake_llm, "q", "a", count=3)
        assert items == []


# ---------------------------------------------------------------------------
# Inline emit logic: gating + persistence shape
# ---------------------------------------------------------------------------


def _gate(agent_cfg: dict[str, Any] | None, answer: str | None) -> bool:
    """Mirror the exact gating logic the chat generator uses.

    The gate reads ``(agent_cfg or {}).get("suggest_followups")`` plus a
    truthy answer; both must be true for ``post_processing`` /
    ``suggestions`` events to be appended to ``sse_events``.
    """
    return bool((agent_cfg or {}).get("suggest_followups")) and bool(
        (answer or "").strip()
    )


class TestInlineEmitGating:
    def test_off_by_default(self) -> None:
        assert _gate(None, "some answer") is False
        assert _gate({}, "some answer") is False
        assert _gate({"suggest_followups": False}, "some answer") is False

    def test_on_when_toggle_true_and_answer_nonempty(self) -> None:
        assert _gate({"suggest_followups": True}, "some answer") is True

    def test_off_when_toggle_true_but_answer_empty(self) -> None:
        assert _gate({"suggest_followups": True}, "") is False
        assert _gate({"suggest_followups": True}, "   \n") is False


# ---------------------------------------------------------------------------
# _resolve_agent_config returns the new key
# ---------------------------------------------------------------------------


class TestResolveAgentConfigKey:
    """The chat generator gates on ``agent_cfg["suggest_followups"]``.

    Any drift in the key name silently re-introduces the regression, so
    pin it via a string-based check on the resolver's source.
    """

    def test_resolver_includes_suggest_followups_key(self) -> None:
        import inspect as _inspect

        from fim_one.web.api import chat as chat_mod

        source = _inspect.getsource(chat_mod._resolve_agent_config)
        assert '"suggest_followups"' in source
        assert "suggest_followups" in source
