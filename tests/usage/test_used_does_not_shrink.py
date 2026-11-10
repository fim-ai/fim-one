"""What a user has used is what they consumed: it does not shrink, and nothing is free.

Each of these failed before quota read the usage ledger. "Used" was the sum
of ``total_tokens`` over the conversations and workflow runs created in the
window, written only when a turn or run finished:

- deleting a conversation, a run or a workflow, or the nightly clean-up of
  runs, took their tokens back out of "used";
- a turn in a conversation created before the window began was not counted;
- a turn that was abandoned or failed was not counted;
- a turn without a conversation, a delegated agent's calls and the
  model-backed helper endpoints were not counted.

"Used" here is the number the quota check enforces
(``core.assembly.quota.quota_status``), read the way the check reads it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from fim_one.db.models import User

from .harness import Usage, events, usage  # noqa: F401
from .provider import UNIT, Provider, provider  # noqa: F401
from .test_entry_matrix import ONE_STEP, _workflow, runs  # noqa: F401


@pytest.fixture()
async def alice(usage: Usage) -> User:  # noqa: F811
    await usage.cap(10_000_000)
    return await usage.user("alice")


async def _a_turn(usage: Usage, by: User) -> Any:  # noqa: F811
    conversation = await usage.conversation(by)
    sent = await usage.turn("/api/react", by, conversation=conversation)
    assert "done" in [name for name, _ in sent]
    return conversation


class TestDeletingGivesNothingBack:
    async def test_usage_survives_conversation_delete(
        self, usage: Usage, provider: Provider, alice: User  # noqa: F811
    ) -> None:
        conversation = await _a_turn(usage, alice)
        used = await usage.quota_used(alice)
        assert used == provider.billed > 0

        await usage.call("DELETE", f"/api/conversations/{conversation.id}", by=alice)

        assert await usage.quota_used(alice) == used

    async def test_usage_survives_deleting_conversations_in_a_batch(
        self, usage: Usage, provider: Provider, alice: User  # noqa: F811
    ) -> None:
        first, second = await _a_turn(usage, alice), await _a_turn(usage, alice)
        used = await usage.quota_used(alice)

        await usage.call(
            "DELETE", "/api/conversations/batch", by=alice, json={"ids": [first.id, second.id]}
        )

        assert await usage.quota_used(alice) == used == provider.billed

    async def test_usage_survives_an_administrator_deleting_the_conversation(
        self, usage: Usage, provider: Provider, alice: User  # noqa: F811
    ) -> None:
        root = await usage.user("root", is_admin=True)
        conversation = await _a_turn(usage, alice)
        used = await usage.quota_used(alice)

        await usage.call("DELETE", f"/api/admin/conversations/{conversation.id}", by=root)

        assert await usage.quota_used(alice) == used == provider.billed

    async def test_usage_survives_run_delete(
        self, usage: Usage, provider: Provider, alice: User, runs: Any  # noqa: F811
    ) -> None:
        await _workflow(usage, alice)
        for _ in range(3):
            await usage.call("POST", "/api/workflows/wf-1/run", by=alice, json={"inputs": {}})
        used = await usage.quota_used(alice)
        assert used == 3 * UNIT
        stored = (await usage.call("GET", "/api/workflows/wf-1/runs", by=alice)).json()
        run_id = stored["items"][0]["id"]

        await usage.call("DELETE", f"/api/workflows/wf-1/runs/{run_id}", by=alice)
        assert await usage.quota_used(alice) == used
        await usage.call("DELETE", "/api/workflows/wf-1/runs", by=alice)
        assert await usage.quota_used(alice) == used

    async def test_usage_survives_workflow_delete(
        self, usage: Usage, provider: Provider, alice: User, runs: Any  # noqa: F811
    ) -> None:
        """Deleting a workflow deletes every run of it, other people's too:
        a subscriber's used fell when the author deleted the workflow."""
        author = await usage.user("author")
        org = await usage.org("acme", author, alice)
        workflow = await _workflow(usage, author, org_id=org.id, visibility="org")
        await usage.subscribe(alice, "workflow", workflow)
        await usage.call("POST", "/api/workflows/wf-1/run", by=alice, json={"inputs": {}})
        used = await usage.quota_used(alice)
        assert used == UNIT

        await usage.call("DELETE", "/api/workflows/wf-1", by=author)

        assert await usage.quota_used(alice) == used

    async def test_usage_survives_run_cleanup(
        self, usage: Usage, provider: Provider, alice: User, runs: Any  # noqa: F811
    ) -> None:
        """A workflow keeps its newest 100 runs: after 101 runs, used is the
        sum of 101, not of 100."""
        from fim_one.core.workflow.run_cleanup import WorkflowRunCleaner
        from fim_one.core.workflow.run_service import RunMode
        from fim_one.db import create_session
        from fim_one.db.models.workflow import WorkflowRun
        from sqlalchemy import func, select

        workflow = await _workflow(usage, alice)
        for _ in range(101):
            outcome = await runs.run_to_completion(
                workflow, caller_id=alice.id, inputs={}, mode=RunMode.INTERACTIVE
            )
            assert outcome.status == "completed"
        assert await usage.quota_used(alice) == 101 * UNIT

        async with create_session() as db:
            deleted = await WorkflowRunCleaner().cleanup(db)
            await db.commit()
            left = (await db.execute(select(func.count()).select_from(WorkflowRun))).scalar_one()

        assert (deleted, left) == (1, 100)
        assert await usage.quota_used(alice) == 101 * UNIT == provider.billed


class TestEverythingConsumedIsCounted:
    async def test_usage_old_conversation_counted(
        self, usage: Usage, provider: Provider, alice: User  # noqa: F811
    ) -> None:
        """A conversation from before the window began, continued today."""
        long_ago = datetime.now(UTC) - timedelta(days=75)
        conversation = await usage.conversation(alice, created_at=long_ago)

        sent = await usage.turn("/api/react", alice, conversation=conversation)

        assert "done" in [name for name, _ in sent]
        assert await usage.quota_used(alice) == provider.billed > 0

    async def test_usage_aborted_turn_counted(
        self, usage: Usage, provider: Provider, alice: User  # noqa: F811
    ) -> None:
        """The client goes away while the model is answering."""
        from fim_one.web.api import chat

        conversation = await usage.conversation(alice)
        # The first streamed call of the turn (the agent's own) sends two
        # chunks and stops; the calls before it have answered.
        provider.hang_at = 3

        class _Client:
            headers: dict[str, str] = {}

            async def is_disconnected(self) -> bool:
                return provider.hung.is_set()

        response = await chat.react_endpoint(
            _Client(),  # type: ignore[arg-type]
            chat.ChatStreamRequest(
                q="say pong", conversation_id=conversation.id, token=usage.token(alice)
            ),
        )
        sent = events("".join([str(chunk) async for chunk in response.body_iterator]))

        assert "done" not in [name for name, _ in sent]
        rows = await usage.rows()
        counted = sum(row.total_tokens for row in rows if not row.estimated)
        estimated = [row for row in rows if row.estimated]
        # The calls that answered, as billed; the one cut short, estimated
        # from what was sent and what had come back.
        assert counted == (provider.calls - 1) * UNIT > 0
        assert len(estimated) == 1 and estimated[0].total_tokens > 0
        assert estimated[0].completion_tokens is not None
        assert 0 < estimated[0].completion_tokens <= len("".join(provider.streamed))
        assert await usage.quota_used(alice) == counted + estimated[0].total_tokens

    async def test_usage_of_a_stream_nobody_finished_reading_is_counted(
        self, usage: Usage, provider: Provider, alice: User  # noqa: F811
    ) -> None:
        """The server closes the response's stream when the connection drops."""
        from fim_one.web.api import chat

        conversation = await usage.conversation(alice)
        provider.chunk_delay = 0.01

        class _Client:
            headers: dict[str, str] = {}

            async def is_disconnected(self) -> bool:
                return False

        response = await chat.react_endpoint(
            _Client(),  # type: ignore[arg-type]
            chat.ChatStreamRequest(
                q="say pong", conversation_id=conversation.id, token=usage.token(alice)
            ),
        )
        body = response.body_iterator
        async for chunk in body:
            if "event: answer" in str(chunk):
                break
        await body.aclose()  # type: ignore[attr-defined]

        used = await usage.quota_used(alice)
        assert used >= (provider.calls - 1) * UNIT > 0

    async def test_usage_failed_turn_counted(
        self, usage: Usage, provider: Provider, alice: User  # noqa: F811
    ) -> None:
        conversation = await usage.conversation(alice)
        provider.fail_from = 3

        sent = await usage.turn("/api/react", alice, conversation=conversation)

        names = [name for name, _ in sent]
        assert "error" in names and "done" not in names
        assert await usage.quota_used(alice) == provider.billed == 2 * UNIT

    async def test_usage_of_a_turn_refused_before_it_streamed_is_counted(
        self, usage: Usage, provider: Provider, alice: User, monkeypatch: Any  # noqa: F811
    ) -> None:
        """Auto mode asked the routing model, then the turn could not be assembled."""
        from fim_one.web.exceptions import AppError
        from fim_one.web.services import agent_runtime

        async def _no_models(*_args: Any, **_kwargs: Any) -> Any:
            raise AppError("agent_config_error", status_code=500)

        conversation = await usage.conversation(alice)
        routed: list[int] = []

        async def _then_break(*args: Any, **kwargs: Any) -> Any:
            routed.append(provider.calls)
            return await _no_models()

        monkeypatch.setattr(agent_runtime, "resolve_models", _then_break)

        await usage.call(
            "POST",
            "/api/auto",
            json={"q": "say pong", "conversation_id": conversation.id, "token": usage.token(alice)},
            expect=500,
        )

        assert routed and provider.calls >= 1
        assert await usage.quota_used(alice) == provider.billed

    async def test_usage_without_conversation_id(
        self, usage: Usage, provider: Provider, alice: User  # noqa: F811
    ) -> None:
        sent = await usage.turn("/api/react", alice)

        assert "done" in [name for name, _ in sent]
        assert await usage.quota_used(alice) == provider.billed > 0

    async def test_usage_sub_agent_counted(
        self, usage: Usage, provider: Provider, alice: User  # noqa: F811
    ) -> None:
        specialist = await usage.agent(alice, name="Specialist")
        conversation = await usage.conversation(alice)
        provider.use_tool("call_agent", {"agent_id": specialist.id, "task": "say pong"})

        sent = await usage.turn("/api/react", alice, conversation=conversation)

        assert "call_agent" in str(sent)
        assert await usage.quota_used(alice) == provider.billed
        # More than the turn's own result reports: the delegate's calls are in.
        (done,) = [data for name, data in sent if name == "done"]
        assert provider.billed > done["data"]["usage"]["total_tokens"]

    @pytest.mark.parametrize(
        ("path", "body"),
        [
            ("/api/agents/ai/create", {"instruction": "an agent that says pong"}),
            ("/api/agents/{agent}/ai/refine", {"instruction": "be brief"}),
            ("/api/connectors/ai/create", {"instruction": "a connector for the pong API"}),
            ("/api/connectors/conn-api/ai/generate-actions", {"instruction": "add ping"}),
            (
                "/api/connectors/conn-api/ai/refine-action",
                {"instruction": "rename it", "action_id": "act-1"},
            ),
            ("/api/connectors/conn-db/ai/db-chat", {"message": "what is in t0?"}),
            ("/api/connectors/conn-db/ai/annotate", {"table_ids": ["tbl-0"]}),
            ("/api/connectors/conn-db/ai/annotate", {}),
            ("/api/knowledge-bases/kb-1/ai/chat", {"message": "what is in here?"}),
        ],
        ids=[
            "agent-create", "agent-refine", "connector-create", "connector-actions",
            "connector-action-refine", "db-chat", "db-annotate", "db-annotate-all", "kb-chat",
        ],  # fmt: skip
    )
    async def test_usage_ai_endpoints_counted(
        self, path: str, body: dict[str, Any], usage: Usage, provider: Provider, alice: User  # noqa: F811
    ) -> None:
        from fim_one.db.models.knowledge_base import KnowledgeBase

        from .test_entry_matrix import TestTheModelBackedHelperEndpoints as Helpers

        agent = await usage.agent(alice)
        await Helpers()._connector(usage, alice)
        await Helpers()._database(usage, alice)
        await usage.add(KnowledgeBase(id="kb-1", user_id=alice.id, name="handbook"))

        await usage.call(
            "POST", path.format(agent=agent.id), by=alice, json=body, expect=tuple(range(200, 600))
        )

        assert await usage.quota_used(alice) == provider.billed > 0

    async def test_usage_eval_run_counted(
        self, usage: Usage, provider: Provider, alice: User  # noqa: F811
    ) -> None:
        from fim_one.db.models.eval import EvalCase, EvalDataset

        agent = await usage.agent(alice)
        await usage.add(
            EvalDataset(id="ds-1", user_id=alice.id, name="pongs"),
            EvalCase(
                id="case-1", dataset_id="ds-1", user_id=alice.id,
                prompt="say pong", expected_behavior="says pong",
            ),  # fmt: skip
        )

        await usage.call(
            "POST", "/api/eval/runs", by=alice, json={"agent_id": agent.id, "dataset_id": "ds-1"}
        )

        assert await usage.quota_used(alice) == provider.billed > 0


class TestTheQuotaHolds:
    async def test_turns_abandoned_before_they_finish_run_into_the_quota(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        """Leaving before the answer ended used to cost nothing, without limit."""
        from fim_one.web.api import chat

        await usage.cap(5 * UNIT)
        alice = await usage.user("alice")

        class _Client:
            headers: dict[str, str] = {}

            async def is_disconnected(self) -> bool:
                return provider.hung.is_set()

        refused = None
        for _attempt in range(10):
            provider.hung = asyncio.Event()
            provider.hang_at = provider.calls + 3
            try:
                response = await chat.react_endpoint(
                    _Client(),  # type: ignore[arg-type]
                    chat.ChatStreamRequest(q="say pong", token=usage.token(alice)),
                )
            except Exception as exc:
                refused = exc
                break
            async for _chunk in response.body_iterator:
                pass
            await usage.settle()

        assert refused is not None, "ten abandoned turns and the quota never refused"
        assert getattr(refused, "error_code", None) == "token_quota_exceeded"

    async def test_usage_recorded_before_the_window_began_is_not_counted(
        self, usage: Usage, provider: Provider, alice: User  # noqa: F811
    ) -> None:
        from fim_one.core.usage.ledger import UsageEntry, write_entries
        from fim_one.db import create_session
        from fim_one.db.models.usage_ledger import UsageLedger
        from sqlalchemy import select

        await write_entries(
            [
                UsageEntry(
                    user_id=alice.id, org_id=None, source_type="conversation", source_id="c",
                    entry="chat", model="m", total_tokens=5000,
                )
            ]
        )  # fmt: skip
        assert await usage.quota_used(alice) == 5000
        async with create_session() as db:
            row = (await db.execute(select(UsageLedger))).scalar_one()
            row.created_at = datetime.now(UTC) - timedelta(days=45)  # the test's own doing
            await db.commit()

        assert await usage.quota_used(alice) == 0

    async def test_usage_card_matches_enforcement(
        self, usage: Usage, provider: Provider, alice: User, runs: Any  # noqa: F811
    ) -> None:
        await _a_turn(usage, alice)
        await _workflow(usage, alice)
        await usage.call("POST", "/api/workflows/wf-1/run", by=alice, json={"inputs": {}})
        await usage.call(
            "POST", "/api/agents/ai/create", by=alice, json={"instruction": "x"},
            expect=tuple(range(200, 600)),
        )  # fmt: skip

        card = await usage.card(alice)

        assert card["window_tokens"] == await usage.quota_used(alice) == provider.billed
        assert card["quota"] == 10_000_000
        assert card["quota_used_pct"] == round(provider.billed / 10_000_000 * 100, 2)
