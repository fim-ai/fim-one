"""Webhook/cron workflow runs are metered, not a free LLM spigot.

Two halves:

1. **Accounting** — the LLM node records its token usage into the run's shared
   ``UsageTracker`` (which the engine totals onto ``WorkflowRun.total_tokens``).
2. **Enforcement** — those workflow tokens count toward the owner's quota
   window, so the pre-flight gate at the webhook/cron entry sees them.
"""

from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.core.model.usage import UsageTracker
from fim_one.core.workflow.nodes import LLMExecutor
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore
from fim_one.db.base import Base
from fim_one.db.models import SystemSetting, User
from fim_one.db.models.usage_ledger import UsageLedger


class _DummyCM:
    async def __aenter__(self) -> Any:
        return MagicMock()

    async def __aexit__(self, *_: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_llm_node_records_usage_to_tracker() -> None:
    tracker = UsageTracker()
    node = WorkflowNodeDef(
        id="llm1", type=NodeType.LLM, data={"type": "LLM", "prompt": "hi"}
    )
    store = VariableStore()
    ctx = ExecutionContext(
        run_id="r", user_id="u", workflow_id="w", usage_tracker=tracker
    )

    fake_result = MagicMock()
    fake_result.message.content = "hello"
    fake_result.usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    fake_llm = MagicMock()
    fake_llm.chat = AsyncMock(return_value=fake_result)
    fake_llm.model_id = "test-model"

    with (
        patch("fim_one.db.create_session", new=lambda: _DummyCM()),
        patch(
            "fim_one.web.deps.get_effective_llm",
            new=AsyncMock(return_value=fake_llm),
        ),
    ):
        result = await LLMExecutor().execute(node, store, ctx)

    assert result.status == NodeStatus.COMPLETED
    assert tracker.get_summary().total_tokens == 15


@pytest.mark.asyncio
async def test_llm_node_without_tracker_does_not_crash() -> None:
    # A run with no tracker (e.g. a unit-level invocation) must still work.
    node = WorkflowNodeDef(
        id="llm1", type=NodeType.LLM, data={"type": "LLM", "prompt": "hi"}
    )
    ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")
    fake_result = MagicMock()
    fake_result.message.content = "hello"
    fake_result.usage = {"total_tokens": 15}
    fake_llm = MagicMock()
    fake_llm.chat = AsyncMock(return_value=fake_result)

    with (
        patch("fim_one.db.create_session", new=lambda: _DummyCM()),
        patch(
            "fim_one.web.deps.get_effective_llm",
            new=AsyncMock(return_value=fake_llm),
        ),
    ):
        result = await LLMExecutor().execute(node, VariableStore(), ctx)

    assert result.status == NodeStatus.COMPLETED


@pytest.fixture()
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_quota_window_includes_workflow_run_tokens(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Chat usage + unattended workflow-run usage share one quota window."""
    user = "owner-1"
    async with session_factory() as s:
        # No override and no plan: the cap is the default_token_quota setting.
        s.add(User(id=user, email="o@x.io", username=user))
        s.add(SystemSetting(key="default_token_quota", value="100"))
        s.add(
            UsageLedger(user_id=user, source_type="conversation", entry="chat", total_tokens=30)
        )
        s.add(
            UsageLedger(
                user_id=user,
                source_type="workflow_run",
                source_id="run-1",
                entry="trigger",
                workflow_id="wf-1",
                total_tokens=50,
            )
        )
        await s.commit()

    from fim_one.web.services.quota_backend import usage_and_cap

    with patch("fim_one.db.create_session", new=lambda: session_factory()):
        used, cap = await usage_and_cap(user)

    assert cap == 100
    # 30 (chat) + 50 (workflow run) must both count.
    assert used == 80
