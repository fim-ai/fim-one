"""The usage figures people are shown are read from the usage ledger (B20, B22).

The user's own Usage card (the period's total, the days, the agents), the
administrators' per-user usage and user list, and the user export. They
summed ``conversations.total_tokens``: a figure fell when conversations
were deleted, left out workflow runs and the helper endpoints, and the
card's breakdown did not add up to the number the quota was enforced on.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any

import pytest

from fim_one.db.models import User

from .harness import Usage, usage  # noqa: F401
from .provider import UNIT, Provider, provider  # noqa: F401
from .test_entry_matrix import _workflow, runs  # noqa: F401


@pytest.fixture()
async def alice(usage: Usage) -> User:  # noqa: F811
    await usage.cap(10_000_000)
    return await usage.user("alice")


@pytest.fixture()
async def root(usage: Usage) -> User:  # noqa: F811
    return await usage.user("root", is_admin=True)


async def _everything(usage: Usage, provider: Provider, by: User) -> dict[str, Any]:  # noqa: F811
    """A turn with an agent, one without, a workflow run and an AI-assisted
    helper; what each of them was billed."""
    billed: dict[str, Any] = {}
    agent = await usage.agent(by, name="Helper")
    with_agent = await usage.conversation(by, agent=agent)
    await usage.turn("/api/react", by, conversation=with_agent, agent=agent)
    await usage.settle()
    billed["agent"] = provider.billed
    direct = await usage.conversation(by)
    await usage.turn("/api/react", by, conversation=direct)
    await usage.settle()
    billed["direct"] = provider.billed - billed["agent"]
    await _workflow(usage, by)
    await usage.call("POST", "/api/workflows/wf-1/run", by=by, json={"inputs": {}})
    billed["workflow"] = UNIT
    before = provider.billed
    await usage.call(
        "POST", "/api/agents/ai/create", by=by, json={"instruction": "x"},
        expect=tuple(range(200, 600)),
    )  # fmt: skip
    billed["assist"] = provider.billed - before
    billed["conversations"] = (with_agent, direct)
    billed["agent_row"] = agent
    return billed


class TestTheUsageCard:
    async def test_the_breakdown_adds_up_to_what_is_enforced(
        self, usage: Usage, provider: Provider, alice: User, runs: Any  # noqa: F811
    ) -> None:
        billed = await _everything(usage, provider, alice)

        card = await usage.card(alice)

        assert card["total_tokens"] == card["window_tokens"] == provider.billed
        assert card["window_tokens"] == await usage.quota_used(alice)
        by_agent = {
            row["agent_name"] or row["origin"]: (row["agent_id"], row["tokens"])
            for row in card["by_agent"]
        }
        # An agent by its name; the rest by a key the client translates.
        assert by_agent == {
            "Helper": (billed["agent_row"].id, billed["agent"]),
            "direct_chat": (None, billed["direct"]),
            "workflows": (None, billed["workflow"]),
            "ai_assist": (None, billed["assist"]),
        }
        assert {row["origin"] for row in card["by_agent"] if row["agent_id"]} == {None}
        assert {row["agent_name"] for row in card["by_agent"] if not row["agent_id"]} == {None}
        assert sum(tokens for _, tokens in by_agent.values()) == card["total_tokens"]
        assert card["daily"] == [
            {"date": datetime.now(UTC).date().isoformat(), "tokens": provider.billed}
        ]
        # Largest first.
        assert [row["tokens"] for row in card["by_agent"]] == sorted(
            (row["tokens"] for row in card["by_agent"]), reverse=True
        )

    async def test_deleting_conversations_and_the_agent_leaves_the_figures(
        self, usage: Usage, provider: Provider, alice: User, runs: Any  # noqa: F811
    ) -> None:
        billed = await _everything(usage, provider, alice)
        before = await usage.card(alice)

        for conversation in billed["conversations"]:
            await usage.call("DELETE", f"/api/conversations/{conversation.id}", by=alice)
        await usage.call("DELETE", "/api/workflows/wf-1", by=alice)
        await usage.call("DELETE", f"/api/agents/{billed['agent_row'].id}", by=alice)

        card = await usage.card(alice)
        assert (card["total_tokens"], card["window_tokens"], card["daily"]) == (
            before["total_tokens"], before["window_tokens"], before["daily"],
        )  # fmt: skip
        by_agent = {row["agent_name"] or row["origin"]: row["tokens"] for row in card["by_agent"]}
        # The agent is gone and so is its name; what went through it is not.
        assert by_agent == {
            "deleted_agents": billed["agent"],
            "direct_chat": billed["direct"],
            "workflows": billed["workflow"],
            "ai_assist": billed["assist"],
        }

    async def test_a_period_counts_what_was_consumed_in_it(
        self, usage: Usage, provider: Provider, alice: User  # noqa: F811
    ) -> None:
        """Not what the conversations created in it hold."""
        from datetime import timedelta

        from fim_one.core.usage.ledger import UsageEntry, write_entries
        from fim_one.db import create_session
        from fim_one.db.models.usage_ledger import UsageLedger
        from sqlalchemy import select

        await write_entries(
            [
                UsageEntry(user_id=alice.id, org_id=None, source_type="conversation",
                           source_id="old", entry="chat", model="m", total_tokens=700),
                UsageEntry(user_id=alice.id, org_id=None, source_type="conversation",
                           source_id="new", entry="chat", model="m", total_tokens=40),
            ]
        )  # fmt: skip
        async with create_session() as db:
            row = (
                await db.execute(select(UsageLedger).where(UsageLedger.source_id == "old"))
            ).scalar_one()
            row.created_at = datetime.now(UTC) - timedelta(days=20)  # the test's own doing
            await db.commit()

        week, quarter = await usage.card(alice, "7d"), await usage.card(alice, "90d")

        assert (week["total_tokens"], quarter["total_tokens"]) == (40, 740)
        assert [day["tokens"] for day in quarter["daily"]] == [700, 40]
        assert [day["tokens"] for day in week["daily"]] == [40]

    async def test_someone_elses_usage_is_not_on_the_card(
        self, usage: Usage, provider: Provider, alice: User  # noqa: F811
    ) -> None:
        bob = await usage.user("bob")
        await usage.turn("/api/react", bob)

        card = await usage.card(alice)

        assert (card["total_tokens"], card["window_tokens"], card["by_agent"]) == (0, 0, [])


class TestWhatAdministratorsSee:
    async def _rows(self, usage: Usage, root: User) -> dict[str, dict[str, Any]]:  # noqa: F811
        await usage.settle()
        listed = (await usage.call("GET", "/api/admin/users?size=100", by=root)).json()["items"]
        return {row["username"]: row for row in listed}

    async def test_the_user_list_counts_everything_and_keeps_it_after_a_delete(
        self, usage: Usage, provider: Provider, alice: User, root: User, runs: Any  # noqa: F811
    ) -> None:
        billed = await _everything(usage, provider, alice)
        assert (await self._rows(usage, root))["alice"]["monthly_tokens"] == provider.billed

        for conversation in billed["conversations"]:
            await usage.call("DELETE", f"/api/conversations/{conversation.id}", by=alice)

        rows = await self._rows(usage, root)
        assert rows["alice"]["monthly_tokens"] == provider.billed
        assert rows["root"]["monthly_tokens"] == 0

    async def test_per_user_usage_counts_everything_and_keeps_it_after_a_delete(
        self, usage: Usage, provider: Provider, alice: User, root: User, runs: Any  # noqa: F811
    ) -> None:
        billed = await _everything(usage, provider, alice)
        await usage.call(
            "DELETE", f"/api/conversations/{billed['conversations'][0].id}", by=alice
        )
        await usage.settle()

        for period in ("week", "month", "all"):
            listed = (
                await usage.call("GET", f"/api/admin/analytics/usage?period={period}", by=root)
            ).json()
            first = listed[0]
            assert (first["username"], first["total_tokens"]) == ("alice", provider.billed), period
            # The conversations that still exist.
            assert first["conversation_count"] == 1
            assert {row["username"]: row["total_tokens"] for row in listed}["root"] == 0

    async def test_the_user_export_counts_everything_and_keeps_it_after_a_delete(
        self, usage: Usage, provider: Provider, alice: User, root: User, runs: Any  # noqa: F811
    ) -> None:
        billed = await _everything(usage, provider, alice)
        for conversation in billed["conversations"]:
            await usage.call("DELETE", f"/api/conversations/{conversation.id}", by=alice)
        await usage.settle()

        export = await usage.call("GET", "/api/admin/export/users", by=root)

        rows = {row["username"]: row for row in csv.DictReader(io.StringIO(export.text))}
        assert int(rows["alice"]["monthly_tokens"]) == provider.billed
        assert int(rows["root"]["monthly_tokens"]) == 0
