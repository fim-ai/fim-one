"""The statistics pages read their token figures from the usage ledger (C2 phase 5).

The platform overview, the per-agent analytics and cost projection, the
daily trend, the metrics API and each user's dashboard summed
``conversations.total_tokens`` (and ``fast_llm_tokens``). Every figure fell
when a conversation was deleted, and none of them counted workflow runs,
the helper endpoints, turns that were cut short or delegated agents.

Each class below holds two kinds of test:

- *survives*: after a turn with an agent, a turn without, a workflow run and
  an AI-assisted helper, the figure is everything that was billed, and stays
  so when the conversations, the run and the workflow are deleted. These
  failed before the switch.
- *backfilled*: on data carried over by the ledger's migration, the figure is
  what the old query gave, plus the workflow runs where the page now counts
  them (the old query left them out; the definitions differ only there).
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from fim_one.db.models import Agent, Conversation, User
from fim_one.db.models.workflow import Workflow, WorkflowRun

from .harness import Usage, usage  # noqa: F401
from .provider import Provider, provider  # noqa: F401
from .test_entry_matrix import runs  # noqa: F401
from .test_usage_figures import _everything

_C2P3 = "fim_one.migrations.versions.c2p3a1b2c3d4_usage_ledger"


@pytest.fixture()
async def alice(usage: Usage) -> User:  # noqa: F811
    await usage.cap(10_000_000)
    return await usage.user("alice")


@pytest.fixture()
async def root(usage: Usage) -> User:  # noqa: F811
    return await usage.user("root", is_admin=True)


async def _delete_everything(usage: Usage, by: User, billed: dict[str, Any]) -> None:  # noqa: F811
    """The conversations, the run and the workflow they came through."""
    for conversation in billed["conversations"]:
        await usage.call("DELETE", f"/api/conversations/{conversation.id}", by=by)
    await usage.call("DELETE", "/api/workflows/wf-1/runs", by=by)
    await usage.call("DELETE", "/api/workflows/wf-1", by=by)
    await usage.settle()


async def _get(usage: Usage, path: str, by: User) -> Any:  # noqa: F811
    await usage.settle()
    return (await usage.call("GET", path, by=by)).json()


# ---------------------------------------------------------------------------
# Backfilled data
# ---------------------------------------------------------------------------

NOW = datetime.now(UTC).replace(microsecond=0)


class Backfilled:
    """Usage as it stood before the ledger, carried over by its migration."""

    def __init__(self, usage: Usage, alice: User, bob: User) -> None:  # noqa: F811
        self.usage, self.alice, self.bob = usage, alice, bob

    async def seed(self) -> None:
        alice, bob = self.alice, self.bob
        self.agent = Agent(
            id="agent-1", user_id=alice.id, name="Helper", instructions="x",
            status="published", is_active=True, visibility="personal",
        )  # fmt: skip
        rows: list[Any] = [
            self.agent,
            Workflow(id="wf-1", user_id=alice.id, name="wf", blueprint={}),
        ]
        conversations = [
            # (owner, tokens, fast tokens, model, agent, days ago)
            (alice, 1000, 120, "main-model", "agent-1", 1),
            (alice, 400, 0, "other-model", None, 3),
            (alice, 50, 50, None, "agent-1", 9),
            (bob, 700, 70, "main-model", None, 20),
            (bob, 90, 10, "main-model", "agent-1", 0),
            (bob, 5000, 0, "main-model", None, 60),
        ]
        for n, (owner, total, fast, model, agent_id, days) in enumerate(conversations):
            rows.append(
                Conversation(
                    id=f"conv-{n}", user_id=owner.id, title="t", mode="react",
                    total_tokens=total, fast_llm_tokens=fast, model_name=model,
                    agent_id=agent_id, created_at=NOW - timedelta(days=days, hours=1),
                )
            )  # fmt: skip
        for n, (owner, total, days) in enumerate([(alice, 300, 2), (bob, 11, 40)]):
            rows.append(
                WorkflowRun(
                    id=f"run-{n}", workflow_id="wf-1", user_id=owner.id,
                    blueprint_snapshot={}, status="completed", mode="manual",
                    total_tokens=total, created_at=NOW - timedelta(days=days, hours=1),
                )
            )  # fmt: skip
        await self.usage.add(*rows)
        from fim_one.db import engine as engine_mod

        assert engine_mod._engine is not None
        module = importlib.import_module(_C2P3)
        async with engine_mod._engine.begin() as conn:
            await conn.run_sync(module._backfill_and_reconcile)

    async def old(self, sql: str, **params: Any) -> list[Any]:
        """What a query over the old per-row totals gives."""
        from fim_one.db import create_session

        async with create_session() as db:
            return list((await db.execute(text(sql), params)).all())

    async def old_one(self, sql: str, **params: Any) -> int:
        return int((await self.old(sql, **params))[0][0] or 0)

    async def runs_since(self, since: datetime | None = None, user: User | None = None) -> int:
        sql = "SELECT COALESCE(SUM(total_tokens), 0) FROM workflow_runs WHERE 1 = 1"
        params: dict[str, Any] = {}
        if since is not None:
            sql += " AND created_at >= :since"
            params["since"] = since
        if user is not None:
            sql += " AND user_id = :user"
            params["user"] = user.id
        return await self.old_one(sql, **params)


@pytest.fixture()
async def backfilled(usage: Usage, alice: User) -> Backfilled:  # noqa: F811
    data = Backfilled(usage, alice, await usage.user("bob"))
    await data.seed()
    return data


# ---------------------------------------------------------------------------
# GET /api/admin/stats: the administrators' overview
# ---------------------------------------------------------------------------


class TestPlatformOverview:
    async def test_survives(
        self, usage: Usage, provider: Provider, alice: User, root: User, runs: Any  # noqa: F811
    ) -> None:
        billed = await _everything(usage, provider, alice)
        before = await _get(usage, "/api/admin/stats", root)
        assert before["total_tokens"] == provider.billed
        assert sum(slice_["count"] for slice_ in before["tokens_by_model"]) == provider.billed
        assert before["tokens_by_agent"] == [
            {"agent_id": billed["agent_row"].id, "name": "Helper", "total_tokens": billed["agent"]}
        ]

        await _delete_everything(usage, alice, billed)

        after = await _get(usage, "/api/admin/stats", root)
        figures = ("total_tokens", "total_fast_llm_tokens", "tokens_by_model", "tokens_by_agent")
        for figure in figures:
            assert after[figure] == before[figure], figure
        # The counts are of what still exists.
        assert after["total_conversations"] == 0

    async def test_backfilled(self, usage: Usage, backfilled: Backfilled, root: User) -> None:  # noqa: F811
        stats = await _get(usage, "/api/admin/stats", root)
        run_tokens = await backfilled.runs_since()

        old_total = await backfilled.old_one("SELECT SUM(total_tokens) FROM conversations")
        assert stats["total_tokens"] == old_total + run_tokens
        old_fast = await backfilled.old_one("SELECT SUM(fast_llm_tokens) FROM conversations")
        assert stats["total_fast_llm_tokens"] == old_fast

        # By model: the conversations' main part under their model, the fast
        # part as one slice, as before; the runs carried no model.
        old_by_model = dict(
            await backfilled.old(
                "SELECT COALESCE(model_name, 'Unknown'), SUM(total_tokens - fast_llm_tokens)"
                " FROM conversations GROUP BY COALESCE(model_name, 'Unknown')"
            )
        )
        expected = {
            "LLM (main-model)": old_by_model["main-model"],
            "other-model": old_by_model["other-model"],
            "Unknown": old_by_model["Unknown"] + run_tokens,
            "Fast LLM (fast-model)": old_fast,
        }
        assert {s["model"]: s["count"] for s in stats["tokens_by_model"] if s["count"]} == {
            label: tokens for label, tokens in expected.items() if tokens
        }

        old_agent = await backfilled.old_one(
            "SELECT SUM(total_tokens) FROM conversations WHERE agent_id = 'agent-1'"
        )
        assert stats["tokens_by_agent"] == [
            {"agent_id": "agent-1", "name": "Helper", "total_tokens": old_agent}
        ]
