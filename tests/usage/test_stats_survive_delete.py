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

The last class is the gate: no code sums the per-row totals kept on
conversations, runs and evaluation runs, except where it is named with
the reason.
"""

from __future__ import annotations

import ast
import importlib
import re
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy import text

import fim_one
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

        # By model: the conversations' main part under their model's plain
        # name, the fast part as one slice; the runs carried no model.
        old_by_model = dict(
            await backfilled.old(
                "SELECT COALESCE(model_name, 'Unknown'), SUM(total_tokens - fast_llm_tokens)"
                " FROM conversations GROUP BY COALESCE(model_name, 'Unknown')"
            )
        )
        expected = {
            "main-model": old_by_model["main-model"],
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


    async def test_top_ten_models_then_the_fast_slice_and_top_ten_agents(
        self, usage: Usage, alice: User, root: User  # noqa: F811
    ) -> None:
        """Tokens by model: the ten models with the most tokens, most first,
        and after them the fast slice (the work after the turns), however
        large. Tokens by agent: the ten agents with the most, most first."""
        from fim_one.db.models.usage_ledger import UsageLedger

        rows: list[Any] = []
        for n in range(12):
            rows.append(
                Agent(
                    id=f"agent-{n:02}", user_id=alice.id, name=f"A{n:02}", instructions="x",
                    status="published", is_active=True, visibility="personal",
                )
            )  # fmt: skip
            rows.append(
                UsageLedger(
                    user_id=alice.id, source_type="conversation", entry="chat",
                    agent_id=f"agent-{n:02}", model=f"model-{n:02}",
                    total_tokens=100 + 10 * n,
                )
            )  # fmt: skip
        rows.append(
            UsageLedger(
                user_id=alice.id, source_type="conversation", entry="background",
                model=None, kind="backfill", total_tokens=10_000,
            )
        )  # fmt: skip
        await usage.add(*rows)

        stats = await _get(usage, "/api/admin/stats", root)
        assert stats["tokens_by_model"] == [
            *({"model": f"model-{n:02}", "count": 100 + 10 * n} for n in range(11, 1, -1)),
            {"model": "Fast LLM (fast-model)", "count": 10_000},
        ]
        assert [(a["agent_id"], a["total_tokens"]) for a in stats["tokens_by_agent"]] == [
            (f"agent-{n:02}", 100 + 10 * n) for n in range(11, 1, -1)
        ]


    async def test_a_turn_on_the_fast_model_is_its_own_slice_not_a_second_fast_slice(
        self, usage: Usage, alice: User, root: User  # noqa: F811
    ) -> None:
        """A turn whose model is FAST_LLM_MODEL is a per-model slice under the
        model's plain name; only the background work is "Fast LLM (...)", so
        no two slices carry the same label."""
        from fim_one.db.models.usage_ledger import UsageLedger

        await usage.add(
            UsageLedger(
                user_id=alice.id, source_type="conversation", entry="chat",
                model="fast-model", total_tokens=300,
            ),
            UsageLedger(
                user_id=alice.id, source_type="conversation", entry="chat",
                model="main-model", total_tokens=200,
            ),
            UsageLedger(
                user_id=alice.id, source_type="conversation", entry="background",
                model="fast-model", total_tokens=50,
            ),
        )  # fmt: skip

        stats = await _get(usage, "/api/admin/stats", root)
        assert stats["tokens_by_model"] == [
            {"model": "fast-model", "count": 300},
            {"model": "main-model", "count": 200},
            {"model": "Fast LLM (fast-model)", "count": 50},
        ]
        labels = [s["model"] for s in stats["tokens_by_model"]]
        assert len(labels) == len(set(labels))


# ---------------------------------------------------------------------------
# GET /api/admin/analytics/by-agent: Analytics, per agent
# ---------------------------------------------------------------------------


class TestPerAgent:
    PATH = "/api/admin/analytics/by-agent?period={period}"

    async def _helper(self, usage: Usage, period: str, by: User) -> list[Any]:  # noqa: F811
        # Every agent is listed; the AI-assisted helper made one of its own.
        listed = await _get(usage, self.PATH.format(period=period), by)
        return [agent for agent in listed if agent["agent_name"] == "Helper"]

    async def test_survives(
        self, usage: Usage, provider: Provider, alice: User, root: User, runs: Any  # noqa: F811
    ) -> None:
        billed = await _everything(usage, provider, alice)
        (helper,) = await self._helper(usage, "7d", root)
        assert (helper["total_tokens"], helper["total_conversations"]) == (billed["agent"], 1)

        await usage.call(
            "DELETE", f"/api/conversations/{billed['conversations'][0].id}", by=alice
        )

        for period in ("7d", "30d", "90d"):
            (helper,) = await self._helper(usage, period, root)
            assert helper["total_tokens"] == billed["agent"], period
            # The conversation count is of those that still exist; the
            # average is the agent's tokens over them (none left: no average).
            assert helper["total_conversations"] == 0
            assert helper["avg_tokens_per_conversation"] is None

    async def test_backfilled(self, usage: Usage, backfilled: Backfilled, root: User) -> None:  # noqa: F811
        for period, days in (("7d", 7), ("30d", 30), ("90d", 90)):
            (helper,) = await self._helper(usage, period, root)
            since = datetime.now(UTC) - timedelta(days=days)
            tokens, count = (
                await backfilled.old(
                    "SELECT COALESCE(SUM(total_tokens), 0), COUNT(*) FROM conversations"
                    " WHERE agent_id = 'agent-1' AND created_at >= :since",
                    since=since,
                )
            )[0]
            assert (helper["total_tokens"], helper["total_conversations"]) == (tokens, count)


# ---------------------------------------------------------------------------
# GET /api/admin/analytics/cost-projection: Analytics, cost projection
# ---------------------------------------------------------------------------


class TestCostProjection:
    PATH = "/api/admin/analytics/cost-projection"

    async def test_survives(
        self, usage: Usage, provider: Provider, alice: User, root: User, runs: Any  # noqa: F811
    ) -> None:
        billed = await _everything(usage, provider, alice)
        before = await _get(usage, self.PATH, root)
        assert before["trailing_7d_tokens"] == provider.billed
        assert before["projected_30d_tokens"] == provider.billed // 7 * 30

        await _delete_everything(usage, alice, billed)

        after = await _get(usage, self.PATH, root)
        assert {**after, "trailing_7d_conversations": 2} == before
        assert after["trailing_7d_conversations"] == 0

    async def test_backfilled(self, usage: Usage, backfilled: Backfilled, root: User) -> None:  # noqa: F811
        projection = await _get(usage, self.PATH, root)
        since = datetime.now(UTC) - timedelta(days=7)
        old_tokens, old_count = (
            await backfilled.old(
                "SELECT COALESCE(SUM(total_tokens), 0), COUNT(*) FROM conversations"
                " WHERE created_at >= :since",
                since=since,
            )
        )[0]
        assert projection["trailing_7d_tokens"] == old_tokens + await backfilled.runs_since(since)
        assert projection["trailing_7d_conversations"] == old_count


# ---------------------------------------------------------------------------
# GET /api/admin/analytics/trends: Analytics, the 30-day trend
# ---------------------------------------------------------------------------


class TestDailyTrend:
    PATH = "/api/admin/analytics/trends"

    async def test_survives(
        self, usage: Usage, provider: Provider, alice: User, root: User, runs: Any  # noqa: F811
    ) -> None:
        billed = await _everything(usage, provider, alice)
        (today,) = await _get(usage, self.PATH, root)
        assert (today["total_tokens"], today["conversation_count"]) == (provider.billed, 2)

        await _delete_everything(usage, alice, billed)

        # The day stays, with its tokens, though no conversation is left.
        (after,) = await _get(usage, self.PATH, root)
        assert after == {**today, "conversation_count": 0, "active_users": 0}

    async def test_backfilled(self, usage: Usage, backfilled: Backfilled, root: User) -> None:  # noqa: F811
        since = datetime.now(UTC) - timedelta(days=30)
        old = {
            str(day): (int(tokens), count, users)
            for day, tokens, count, users in await backfilled.old(
                "SELECT DATE(created_at), SUM(total_tokens), COUNT(*), COUNT(DISTINCT user_id)"
                " FROM conversations WHERE created_at >= :since GROUP BY DATE(created_at)",
                since=since,
            )
        }
        runs_by_day = dict(
            await backfilled.old(
                "SELECT DATE(created_at), SUM(total_tokens) FROM workflow_runs"
                " WHERE created_at >= :since GROUP BY DATE(created_at)",
                since=since,
            )
        )
        trend = {
            row["date"]: (row["total_tokens"], row["conversation_count"], row["active_users"])
            for row in await _get(usage, self.PATH, root)
        }
        expected = dict(old)
        for day, tokens in runs_by_day.items():
            tokens_then, count, users = expected.get(str(day), (0, 0, 0))
            expected[str(day)] = (tokens_then + int(tokens), count, users)
        assert trend == expected
        assert list(trend) == sorted(trend)


# ---------------------------------------------------------------------------
# GET /api/metrics/overview: the metrics API's summary
# ---------------------------------------------------------------------------


class TestMetricsOverview:
    PATH = "/api/metrics/overview?period={period}"

    async def _tokens(self, usage: Usage, period: str, by: User) -> dict[str, int]:  # noqa: F811
        body = await _get(usage, self.PATH.format(period=period), by)
        tokens: dict[str, int] = body["data"]["token_usage"]
        return tokens

    async def test_survives(
        self, usage: Usage, provider: Provider, alice: User, root: User, runs: Any  # noqa: F811
    ) -> None:
        billed = await _everything(usage, provider, alice)
        before = await self._tokens(usage, "24h", root)
        assert before["total_input"] + before["total_output"] == provider.billed
        # The title written after each turn is the work done after it.
        background = await usage.recorded(entry="background")
        assert before["total_output"] == background

        await _delete_everything(usage, alice, billed)

        for period in ("24h", "7d", "30d"):
            assert await self._tokens(usage, period, root) == before, period

    async def test_backfilled(self, usage: Usage, backfilled: Backfilled, root: User) -> None:  # noqa: F811
        for period, days in (("7d", 7), ("30d", 30)):
            since = datetime.now(UTC) - timedelta(days=days)
            total, fast = (
                await backfilled.old(
                    "SELECT COALESCE(SUM(total_tokens), 0), COALESCE(SUM(fast_llm_tokens), 0)"
                    " FROM conversations WHERE created_at >= :since",
                    since=since,
                )
            )[0]
            assert await self._tokens(usage, period, root) == {
                "total_input": total - fast + await backfilled.runs_since(since),
                "total_output": fast,
            }, period


# ---------------------------------------------------------------------------
# GET /api/metrics/token-usage: the metrics API's tokens per day and model
# ---------------------------------------------------------------------------


class TestMetricsTokenUsage:
    PATH = "/api/metrics/token-usage?period={period}"

    async def _usage(self, usage: Usage, period: str, by: User) -> dict[str, Any]:  # noqa: F811
        body: dict[str, Any] = (await _get(usage, self.PATH.format(period=period), by))["data"]
        return body

    async def test_survives(
        self, usage: Usage, provider: Provider, alice: User, root: User, runs: Any  # noqa: F811
    ) -> None:
        billed = await _everything(usage, provider, alice)
        before = await self._usage(usage, "24h", root)
        assert before["total"]["input"] + before["total"]["output"] == provider.billed
        assert set(before["by_model"]) == {"main-model", "fast-model"}

        await _delete_everything(usage, alice, billed)

        for period in ("24h", "7d", "30d"):
            assert await self._usage(usage, period, root) == before, period

    async def test_backfilled(self, usage: Usage, backfilled: Backfilled, root: User) -> None:  # noqa: F811
        """The totals are as before, the runs added to the input. By model,
        the main part is under the conversation's model as before; the
        fast part, carried over without a model, is now under "unknown"
        (it was under the conversation's model)."""
        since = datetime.now(UTC) - timedelta(days=30)
        found = await self._usage(usage, "30d", root)
        main = dict(
            await backfilled.old(
                "SELECT COALESCE(model_name, 'unknown'), SUM(total_tokens - fast_llm_tokens)"
                " FROM conversations WHERE created_at >= :since"
                " GROUP BY COALESCE(model_name, 'unknown')",
                since=since,
            )
        )
        fast = await backfilled.old_one(
            "SELECT SUM(fast_llm_tokens) FROM conversations WHERE created_at >= :since",
            since=since,
        )
        run_tokens = await backfilled.runs_since(since)
        assert found["total"] == {
            "input": sum(main.values()) + run_tokens,
            "output": fast,
        }
        expected = {model: {"input": tokens, "output": 0} for model, tokens in main.items()}
        unknown = expected.setdefault("unknown", {"input": 0, "output": 0})
        unknown["input"] += run_tokens
        unknown["output"] += fast
        assert found["by_model"] == {
            model: tokens for model, tokens in expected.items() if any(tokens.values())
        }
        days = [row["date"] for row in found["daily"]]
        assert days == sorted(days)


# ---------------------------------------------------------------------------
# GET /api/dashboard/stats: each user's own dashboard
# ---------------------------------------------------------------------------


class TestDashboard:
    PATH = "/api/dashboard/stats"
    TOKENS = ("total_tokens", "tokens_week_trend")

    async def test_survives(
        self, usage: Usage, provider: Provider, alice: User, root: User, runs: Any  # noqa: F811
    ) -> None:
        billed = await _everything(usage, provider, alice)
        before = await _get(usage, self.PATH, alice)
        assert before["total_tokens"] == provider.billed
        assert before["tokens_week_trend"] == 100.0
        assert sum(day["tokens"] for day in before["activity_trend"]) == provider.billed
        # Someone else's usage is not on it.
        assert (await _get(usage, self.PATH, root))["total_tokens"] == 0

        await _delete_everything(usage, alice, billed)

        after = await _get(usage, self.PATH, alice)
        for figure in self.TOKENS:
            assert after[figure] == before[figure], figure
        assert [day["tokens"] for day in after["activity_trend"]] == [
            day["tokens"] for day in before["activity_trend"]
        ]
        assert after["total_conversations"] == 0

    async def test_backfilled(
        self, usage: Usage, backfilled: Backfilled, alice: User  # noqa: F811
    ) -> None:
        dashboard = await _get(usage, self.PATH, alice)
        now = datetime.now(UTC)
        week, two_weeks = now - timedelta(days=7), now - timedelta(days=14)

        async def old(since: datetime | None, until: datetime | None = None) -> int:
            sql = "SELECT COALESCE(SUM(total_tokens), 0) FROM conversations WHERE user_id = :u"
            params: dict[str, Any] = {"u": alice.id}
            if since is not None:
                sql += " AND created_at >= :since"
                params["since"] = since
            if until is not None:
                sql += " AND created_at < :until"
                params["until"] = until
            return await backfilled.old_one(sql, **params)

        runs_all = await backfilled.runs_since(user=alice)
        assert dashboard["total_tokens"] == await old(None) + runs_all
        current = await old(week) + await backfilled.runs_since(week, alice)
        previous = await old(two_weeks, week) + (
            await backfilled.runs_since(two_weeks, alice) - await backfilled.runs_since(week, alice)
        )
        assert previous > 0
        assert dashboard["tokens_week_trend"] == round((current - previous) / previous * 100, 2)
        assert sum(day["tokens"] for day in dashboard["activity_trend"]) == current


class TestDashboardDays:
    """The dashboard's seven days are UTC dates counted back from the UTC
    today, the same days the ledger buckets by, whatever the server's zone."""

    @pytest.mark.parametrize("zone", ["Etc/GMT-14", "Etc/GMT+12"])
    async def test_the_last_day_is_the_utc_today(
        self, usage: Usage, alice: User, zone: str  # noqa: F811
    ) -> None:
        """UTC+14 and UTC-12 are a calendar day apart at every hour, so in
        one of them the local date is not the UTC date."""
        import os
        import time

        from fim_one.db.models.usage_ledger import UsageLedger

        await usage.add(
            UsageLedger(user_id=alice.id, source_type="conversation", entry="chat",
                        total_tokens=9, created_at=datetime.now(UTC) - timedelta(minutes=1)),
        )  # fmt: skip
        before = os.environ.get("TZ")
        os.environ["TZ"] = zone
        time.tzset()
        try:
            dashboard = await _get(usage, "/api/dashboard/stats", alice)
        finally:
            if before is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = before
            time.tzset()
        days = dashboard["activity_trend"]
        assert days[-1]["date"] == str(datetime.now(UTC).date())
        assert sum(day["tokens"] for day in days) == 9


# ---------------------------------------------------------------------------
# A slice below zero
# ---------------------------------------------------------------------------


class TestANegativeSlice:
    """An adjustment can outweigh the usage of its slice (here a model with
    no usage in the window). The slices are not clamped one by one, only
    the totals, so every page that shows the same window shows the same
    number."""

    async def test_the_pages_agree(self, usage: Usage, alice: User, root: User) -> None:  # noqa: F811
        from fim_one.db.models.usage_ledger import UsageLedger

        recent = datetime.now(UTC) - timedelta(hours=1)
        await usage.add(
            UsageLedger(user_id=alice.id, source_type="conversation", entry="chat",
                        model="model-a", total_tokens=100, created_at=recent),
            UsageLedger(user_id=alice.id, source_type="conversation", entry="chat",
                        model="model-b", total_tokens=-30, kind="adjustment",
                        created_at=recent),
            UsageLedger(user_id=alice.id, source_type="conversation", entry="background",
                        model=None, kind="backfill", total_tokens=20, created_at=recent),
        )  # fmt: skip
        net = 100 - 30 + 20

        stats = await _get(usage, "/api/admin/stats", root)
        assert stats["total_tokens"] == net
        assert sum(slice_["count"] for slice_ in stats["tokens_by_model"]) == net
        projection = await _get(usage, "/api/admin/analytics/cost-projection", root)
        assert projection["trailing_7d_tokens"] == net
        trend = await _get(usage, "/api/admin/analytics/trends", root)
        assert sum(day["total_tokens"] for day in trend) == net
        overview = (await _get(usage, "/api/metrics/overview?period=7d", root))["data"]
        split = overview["token_usage"]
        assert split["total_input"] + split["total_output"] == net
        dashboard = await _get(usage, "/api/dashboard/stats", alice)
        assert dashboard["total_tokens"] == net
        assert sum(day["tokens"] for day in dashboard["activity_trend"]) == net


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


# Columns kept per row, for that row's own page.
_PER_ROW_COLUMNS = frozenset({"total_tokens", "fast_llm_tokens"})
_LEDGER = "UsageLedger"
_RAW_SUM = re.compile(r"SUM\(\s*(total_tokens|fast_llm_tokens)\b", re.IGNORECASE)
# Helpers whose result is a subquery over the ledger.
_LEDGER_SUBQUERY = re.compile(r"^usage_per_\w+_subquery$")


class _SumFinder(ast.NodeVisitor):
    """Finds aggregates over a per-row token column in one module.

    A column is ``X.total_tokens``, ``X.c.total_tokens``,
    ``X.__table__.c.total_tokens`` or ``getattr(X, "total_tokens")`` (and the
    same for ``fast_llm_tokens``) where ``X`` is not the usage ledger, or a
    variable bound to one. An aggregate is a call to ``sum`` / ``avg`` under
    any prefix and in any case (``func.sum``, ``func.SUM``,
    ``sqlalchemy.sql.functions.sum``, the builtin ``sum``), anywhere in its
    arguments. Raw SQL is a string with ``SUM(total_tokens``. Each hit is
    reported as ``Model.column``, the model resolved through import aliases,
    ``aliased(Model)`` and plain assignment.
    """

    def __init__(self) -> None:
        self.names: dict[str, str] = {}  # local name -> model it stands for
        self.columns: dict[str, str] = {}  # variable -> "Model.column" it holds
        self.found: set[str] = set()

    # -- binding ---------------------------------------------------------

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.asname:
                self.names[alias.asname] = alias.name

    def _model(self, node: ast.AST) -> str | None:
        """The model *node* stands for, or ``None`` for the ledger."""
        if isinstance(node, ast.Name):
            name = self.names.get(node.id, node.id)
            return None if name == _LEDGER else name
        if isinstance(node, ast.Attribute):
            if node.attr in {"c", "__table__", "columns"}:
                return self._model(node.value)
            return None if node.attr == _LEDGER else node.attr
        if isinstance(node, ast.Call):
            return self._model(node.func)
        return "?"

    def _column(self, node: ast.AST) -> str | None:
        """``Model.column`` when *node* is a per-row column, else ``None``."""
        if isinstance(node, ast.Attribute) and node.attr in _PER_ROW_COLUMNS:
            model = self._model(node.value)
            return f"{model}.{node.attr}" if model else None
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in _PER_ROW_COLUMNS
        ):
            model = self._model(node.args[0])
            return f"{model}.{node.args[1].value}" if model else None
        if isinstance(node, ast.Name) and node.id in self.columns:
            return self.columns[node.id]
        return None

    def visit_Assign(self, node: ast.Assign) -> None:
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        value = node.value
        column = self._column(value)
        for target in targets:
            if column:
                self.columns[target] = column
                continue
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name | ast.Attribute)
                and (value.func.id if isinstance(value.func, ast.Name) else value.func.attr)
                == "aliased"
                and value.args
            ):
                inner = self._model(value.args[0])
                self.names[target] = inner or _LEDGER
            elif isinstance(value, ast.Call) and _LEDGER_SUBQUERY.match(
                getattr(value.func, "attr", getattr(value.func, "id", ""))
            ):
                self.names[target] = _LEDGER
            elif isinstance(value, ast.Name):
                self.names[target] = self.names.get(value.id, value.id)
        self.generic_visit(node)

    # -- aggregates ------------------------------------------------------

    @staticmethod
    def _is_aggregate(call: ast.Call) -> bool:
        func = call.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        return name.lower() in {"sum", "avg"}

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_aggregate(node):
            for inner in ast.walk(node):
                if inner is node:
                    continue
                column = self._column(inner)
                if column:
                    self.found.add(column)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            for column in _RAW_SUM.findall(node.value):
                self.found.add(f"SQL.{column}")


def _sums_in(source: str) -> set[str]:
    finder = _SumFinder()
    with warnings.catch_warnings():  # escape sequences in the sources' own strings
        warnings.simplefilter("ignore", SyntaxWarning)
        warnings.simplefilter("ignore", DeprecationWarning)
        tree = ast.parse(source)
    finder.visit(tree)
    return finder.found


class TestNothingSumsThePerRowTotals:
    """A conversation's, a run's or an evaluation run's own ``total_tokens``
    (and a conversation's ``fast_llm_tokens``) is what that one row used,
    for its own page. Summed, it falls when rows are deleted: a figure over
    many of them reads the usage ledger instead."""

    SRC = Path(fim_one.__file__).parent
    # (file, column summed) -> why it stays.
    EXEMPT: ClassVar[dict[tuple[str, str], str]] = {
        ("web/api/admin_eval.py", "EvalRun.total_tokens"): (
            "the evaluation center's 'tokens consumed': evaluation tokens were not"
            " carried into the ledger by its migration, so read from the ledger the"
            " figure would drop to the evaluations run since"
        ),
    }

    def _sums(self) -> set[tuple[str, str]]:
        found = set()
        for path in self.SRC.rglob("*.py"):
            for column in _sums_in(path.read_text(encoding="utf-8")):
                found.add((str(path.relative_to(self.SRC)), column))
        return found

    def test_only_the_exempt_sums_remain(self) -> None:
        assert self._sums() == set(self.EXEMPT)

    @pytest.mark.parametrize(
        ("source", "column"),
        [
            ("select(func.coalesce(func.sum(Conversation.total_tokens), 0))",
             "Conversation.total_tokens"),
            ("func.sum(Conversation.total_tokens - Conversation.fast_llm_tokens)",
             "Conversation.fast_llm_tokens"),
            ("func.sum(\n    WorkflowRun.total_tokens\n)", "WorkflowRun.total_tokens"),
            ("conv = aliased(Conversation)\nfunc.sum(conv.total_tokens)",
             "Conversation.total_tokens"),
            ("from fim_one.db.models.workflow import WorkflowRun as Run\n"
             "func.sum(Run.total_tokens)", "WorkflowRun.total_tokens"),
            ("col = Conversation.total_tokens\nselect(func.sum(col))",
             "Conversation.total_tokens"),
            ("func.sum(Conversation.__table__.c.total_tokens)", "Conversation.total_tokens"),
            ("func.sum(getattr(Conversation, 'total_tokens'))", "Conversation.total_tokens"),
            ("func.sum(func.abs(x) + Conversation.total_tokens)", "Conversation.total_tokens"),
            ("func.SUM(Conversation.total_tokens)", "Conversation.total_tokens"),
            ("func.avg(Conversation.total_tokens)", "Conversation.total_tokens"),
            ("sum(c.total_tokens for c in convs)", "c.total_tokens"),
            ("text('SELECT SUM(total_tokens) FROM conversations')", "SQL.total_tokens"),
            ("sqlalchemy.sql.functions.sum(Conversation.fast_llm_tokens)",
             "Conversation.fast_llm_tokens"),
        ],
    )  # fmt: skip
    def test_the_checker_finds_each_spelling(self, source: str, column: str) -> None:
        assert column in _sums_in(source), source

    @pytest.mark.parametrize(
        "source",
        [
            "func.sum(UsageLedger.total_tokens)",
            "from fim_one.db.models.usage_ledger import UsageLedger as L\nfunc.sum(L.total_tokens)",
            "row = aliased(UsageLedger)\nfunc.sum(row.total_tokens)",
            "sub = usage_ledger.usage_per_agent_subquery()\nfunc.sum(sub.c.total_tokens)",
            "func.sum(case((WorkflowRun.status == 'completed', 1), else_=0))",
            # A single row's own figure, for its own page.
            "select(Conversation.total_tokens).where(Conversation.id == cid)",
            "return {'total_tokens': conv.total_tokens}",
        ],
    )
    def test_the_checker_passes_ledger_sums_and_single_rows(self, source: str) -> None:
        assert not _sums_in(source), source
