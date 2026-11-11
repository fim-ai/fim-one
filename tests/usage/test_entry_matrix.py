"""Every entry that can reach a model records what it consumed, once.

One test per entry. Each drives the real entry point with the real model
client and a provider that bills :data:`UNIT` tokens per call
(``tests/usage/provider.py``), then holds the usage ledger to the bill:

- the rows are the acting user's (the author's for a trigger key or a
  schedule) and carry the organization the agent or workflow belongs to;
- their sum equals ``provider.billed``: less means a call was not
  recorded, more means one was recorded twice;
- they sit on the right source (the conversation, the run, the endpoint).

Strict metering is on (``tests/usage/harness.py``): a model call made with
no meter open fails the test where it happens. A new entry point that
calls a model belongs in this file.
"""

from __future__ import annotations

from typing import Any

import pytest

from fim_one.db.models import User
from fim_one.db.models.usage_ledger import UsageLedger

from .harness import Usage, usage  # noqa: F401
from .provider import UNIT, Provider, provider  # noqa: F401


async def _recorded(
    usage: Usage,  # noqa: F811
    provider: Provider,  # noqa: F811
    *,
    by: User,
    org: Any = None,
) -> list[UsageLedger]:
    """Every call the provider billed is in the ledger once, against *by*."""
    rows = await usage.rows()
    assert provider.calls > 0, "the entry never reached the model"
    assert sum(row.total_tokens for row in rows) == provider.billed, (
        f"{provider.calls} calls billed {provider.billed}, the ledger holds "
        f"{[(r.entry, r.source_type, r.model, r.total_tokens) for r in rows]}"
    )
    assert {row.user_id for row in rows} == {by.id}
    assert {row.org_id for row in rows} == {org.id if org is not None else None}
    for row in rows:
        assert (row.kind, row.unit, row.estimated) == ("usage", "llm_token", False)
        assert row.total_tokens > 0 and row.total_tokens % UNIT == 0
        assert row.prompt_tokens is not None and row.completion_tokens is not None
        assert row.prompt_tokens + row.completion_tokens == row.total_tokens
        assert row.model
    return rows


def _sources(rows: list[UsageLedger]) -> set[tuple[str, str | None]]:
    return {(row.source_type, row.source_id) for row in rows}


def _entries(rows: list[UsageLedger]) -> set[str]:
    return {row.entry for row in rows}


class TestChat:
    async def test_a_react_turn(self, usage: Usage, provider: Provider) -> None:  # noqa: F811
        alice = await usage.user("alice")
        conversation = await usage.conversation(alice)

        sent = await usage.turn("/api/react", alice, conversation=conversation)

        assert "done" in [name for name, _ in sent]
        rows = await _recorded(usage, provider, by=alice)
        assert _sources(rows) == {("conversation", conversation.id)}
        # The turn, and the title written after the response ended.
        assert _entries(rows) == {"chat", "background"}

    async def test_a_turn_with_an_organizations_agent(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        owner, alice = await usage.user("owner"), await usage.user("alice")
        org = await usage.org("acme", owner, alice)
        agent = await usage.agent(owner, org=org)
        await usage.subscribe(alice, "agent", agent)
        conversation = await usage.conversation(alice, agent=agent)

        sent = await usage.turn("/api/react", alice, conversation=conversation, agent=agent)

        assert "done" in [name for name, _ in sent]
        # The user who chats pays; the agent's organization is on the row.
        rows = await _recorded(usage, provider, by=alice, org=org)
        assert {row.agent_id for row in rows} == {agent.id}

    async def test_a_plan_turn(self, usage: Usage, provider: Provider) -> None:  # noqa: F811
        alice = await usage.user("alice")
        conversation = await usage.conversation(alice, mode="dag")

        sent = await usage.turn("/api/dag", alice, conversation=conversation)

        assert "done" in [name for name, _ in sent]
        rows = await _recorded(usage, provider, by=alice)
        assert _sources(rows) == {("conversation", conversation.id)}
        assert _entries(rows) == {"dag", "background"}

    async def test_an_auto_turn_counts_the_routing_call(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        alice = await usage.user("alice")
        conversation = await usage.conversation(alice, mode="auto")

        sent = await usage.turn("/api/auto", alice, conversation=conversation)

        assert sent[0][0] == "routing" and "done" in [name for name, _ in sent]
        rows = await _recorded(usage, provider, by=alice)
        assert _sources(rows) == {("conversation", conversation.id)}
        assert _entries(rows) == {"auto", "background"}

    async def test_an_api_call_without_a_conversation(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        alice = await usage.user("alice")

        for endpoint in ("/api/react", "/api/dag", "/api/auto"):
            sent = await usage.turn(endpoint, alice)
            assert "done" in [name for name, _ in sent], endpoint

        rows = await _recorded(usage, provider, by=alice)
        assert _sources(rows) == {("endpoint", None)}
        assert _entries(rows) == {"chat", "dag", "auto"}

    async def test_a_delegated_agent_is_counted_once_on_the_conversation(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        alice = await usage.user("alice")
        specialist = await usage.agent(alice, name="Specialist")
        conversation = await usage.conversation(alice)
        provider.use_tool("call_agent", {"agent_id": specialist.id, "task": "say pong"})

        sent = await usage.turn("/api/react", alice, conversation=conversation)

        tools = [d.get("tool_name") for name, d in sent if name == "step" and isinstance(d, dict)]
        assert "call_agent" in tools, tools
        rows = await _recorded(usage, provider, by=alice)
        assert _sources(rows) == {("conversation", conversation.id)}


class TestTheModelBackedHelperEndpoints:
    """AI assist for agents and connectors, KB chat, DB chat, schema annotation."""

    async def _connector(
        self, usage: Usage, owner: User, org: Any = None  # noqa: F811
    ) -> Any:
        from fim_one.db.models.connector import Connector, ConnectorAction

        connector = Connector(
            id="conn-api",
            user_id=owner.id,
            org_id=org.id if org is not None else None,
            name="crm",
            type="api",
            base_url="https://crm.example.test",
            auth_type="none",
        )
        action = ConnectorAction(
            id="act-1", connector_id="conn-api", name="ping", method="GET", path="/ping"
        )
        await usage.add(connector, action)
        return connector

    async def _database(self, usage: Usage, owner: User, tables: int = 2) -> Any:  # noqa: F811
        from fim_one.db.models.connector import Connector
        from fim_one.db.models.database_schema import DatabaseSchema, SchemaColumn

        connector = Connector(
            id="conn-db",
            user_id=owner.id,
            name="warehouse",
            type="database",
            db_config={"driver": "postgresql", "host": "db.test", "read_only": True},
        )
        await usage.add(connector)
        for index in range(tables):
            await usage.add(
                DatabaseSchema(id=f"tbl-{index}", connector_id="conn-db", table_name=f"t{index}"),
                SchemaColumn(
                    id=f"col-{index}", schema_id=f"tbl-{index}", column_name="id", data_type="int"
                ),
            )
        return connector

    async def _helper(
        self,
        usage: Usage,  # noqa: F811
        provider: Provider,  # noqa: F811
        by: User,
        path: str,
        body: dict[str, Any],
        entry: str,
        source_id: str | None,
        org: Any = None,
    ) -> None:
        # Whatever the endpoint makes of the model's reply: what it asked
        # the model is recorded.
        await usage.call("POST", path, by=by, json=body, expect=tuple(range(200, 600)))

        rows = await _recorded(usage, provider, by=by, org=org)
        assert _sources(rows) == {("endpoint", source_id)}
        assert _entries(rows) == {entry}

    async def test_agent_create(self, usage: Usage, provider: Provider) -> None:  # noqa: F811
        alice = await usage.user("alice")
        await self._helper(
            usage, provider, alice, "/api/agents/ai/create",
            {"instruction": "an agent that says pong"}, "ai_assist.agent_create", None,
        )  # fmt: skip

    async def test_agent_refine(self, usage: Usage, provider: Provider) -> None:  # noqa: F811
        alice = await usage.user("alice")
        agent = await usage.agent(alice)
        await self._helper(
            usage, provider, alice, f"/api/agents/{agent.id}/ai/refine",
            {"instruction": "be brief"}, "ai_assist.agent_refine", agent.id,
        )  # fmt: skip

    async def test_an_organizations_agent_puts_its_organization_on_the_row(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        alice = await usage.user("alice")
        org = await usage.org("acme", alice)
        agent = await usage.agent(alice, org=org)
        await self._helper(
            usage, provider, alice, f"/api/agents/{agent.id}/ai/refine",
            {"instruction": "be brief"}, "ai_assist.agent_refine", agent.id, org,
        )  # fmt: skip

    async def test_an_organizations_connector_puts_its_organization_on_the_row(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        alice = await usage.user("alice")
        org = await usage.org("acme", alice)
        await self._connector(usage, alice, org)
        await self._helper(
            usage, provider, alice, "/api/connectors/conn-api/ai/generate-actions",
            {"instruction": "add a ping action"}, "ai_assist.connector_actions", "conn-api", org,
        )  # fmt: skip

    async def test_an_organizations_knowledge_base_puts_its_organization_on_the_row(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        from fim_one.db.models.knowledge_base import KnowledgeBase

        alice = await usage.user("alice")
        org = await usage.org("acme", alice)
        await usage.add(KnowledgeBase(id="kb-1", user_id=alice.id, name="handbook", org_id=org.id))
        await self._helper(
            usage, provider, alice, "/api/knowledge-bases/kb-1/ai/chat",
            {"message": "what is in here?"}, "kb_chat", "kb-1", org,
        )  # fmt: skip

    async def test_connector_create(self, usage: Usage, provider: Provider) -> None:  # noqa: F811
        alice = await usage.user("alice")
        await self._helper(
            usage, provider, alice, "/api/connectors/ai/create",
            {"instruction": "a connector for the pong API"}, "ai_assist.connector_create", None,
        )  # fmt: skip

    async def test_connector_generate_actions(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        alice = await usage.user("alice")
        await self._connector(usage, alice)
        await self._helper(
            usage, provider, alice, "/api/connectors/conn-api/ai/generate-actions",
            {"instruction": "add a ping action"}, "ai_assist.connector_actions", "conn-api",
        )  # fmt: skip

    async def test_connector_refine_action(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        alice = await usage.user("alice")
        await self._connector(usage, alice)
        await self._helper(
            usage, provider, alice, "/api/connectors/conn-api/ai/refine-action",
            {"instruction": "rename it", "action_id": "act-1"},
            "ai_assist.connector_action_refine", "conn-api",
        )  # fmt: skip

    async def test_db_chat(self, usage: Usage, provider: Provider) -> None:  # noqa: F811
        alice = await usage.user("alice")
        await self._database(usage, alice)
        await self._helper(
            usage, provider, alice, "/api/connectors/conn-db/ai/db-chat",
            {"message": "what is in t0?"}, "db_chat", "conn-db",
        )  # fmt: skip

    async def test_db_annotate_one_table(self, usage: Usage, provider: Provider) -> None:  # noqa: F811
        alice = await usage.user("alice")
        await self._database(usage, alice)
        await self._helper(
            usage, provider, alice, "/api/connectors/conn-db/ai/annotate",
            {"table_ids": ["tbl-0"]}, "ai_assist.db_annotate", "conn-db",
        )  # fmt: skip

    async def test_db_annotate_all_tables_in_the_background(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        """The request answers with a job id; the job's calls are the request's user's."""
        alice = await usage.user("alice")
        await self._database(usage, alice, tables=3)

        answer = await usage.call("POST", "/api/connectors/conn-db/ai/annotate", by=alice, json={})

        assert answer.json()["data"]["job_id"]
        rows = await _recorded(usage, provider, by=alice)
        assert _sources(rows) == {("endpoint", "conn-db")}
        assert _entries(rows) == {"ai_assist.db_annotate"}

    async def test_kb_chat(self, usage: Usage, provider: Provider) -> None:  # noqa: F811
        from fim_one.db.models.knowledge_base import KnowledgeBase

        alice = await usage.user("alice")
        await usage.add(KnowledgeBase(id="kb-1", user_id=alice.id, name="handbook"))
        await self._helper(
            usage, provider, alice, "/api/knowledge-bases/kb-1/ai/chat",
            {"message": "what is in here?"}, "kb_chat", "kb-1",
        )  # fmt: skip

    async def test_a_refused_request_opens_nothing_and_records_nothing(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        await usage.call(
            "POST", "/api/agents/ai/create", json={"instruction": "x"}, expect=(401, 403)
        )

        assert provider.calls == 0
        assert await usage.rows() == []


class TestOtherEntries:
    async def test_an_eval_run(self, usage: Usage, provider: Provider) -> None:  # noqa: F811
        from fim_one.db.models.eval import EvalCase, EvalDataset

        owner = await usage.user("owner")
        org = await usage.org("acme", owner)
        agent = await usage.agent(owner, org=org)
        await usage.add(
            EvalDataset(id="ds-1", user_id=owner.id, name="pongs"),
            *(
                EvalCase(
                    id=f"case-{index}",
                    dataset_id="ds-1",
                    user_id=owner.id,
                    prompt="say pong",
                    expected_behavior="says pong",
                )
                for index in range(3)
            ),
        )

        started = await usage.call(
            "POST", "/api/eval/runs", by=owner, json={"agent_id": agent.id, "dataset_id": "ds-1"}
        )

        run_id = started.json()["data"]["id"]
        rows = await _recorded(usage, provider, by=owner, org=org)
        assert _sources(rows) == {("eval_run", run_id)}
        assert _entries(rows) == {"eval"}
        assert {row.agent_id for row in rows} == {agent.id}
        # The agent and the grader, for each of the three cases.
        assert provider.calls >= 6

    async def test_an_eval_run_that_uses_up_the_quota_starts_no_further_case(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        """Admitted once; between cases it is checked from the run's own meter."""
        from sqlalchemy import select

        from fim_one.db import create_session
        from fim_one.db.models.eval import EvalCase, EvalCaseResult, EvalDataset, EvalRun

        owner = await usage.user("owner")
        await usage.cap(1)
        agent = await usage.agent(owner)
        await usage.add(
            EvalDataset(id="ds-1", user_id=owner.id, name="pongs"),
            *(
                EvalCase(
                    id=f"case-{index}", dataset_id="ds-1", user_id=owner.id,
                    prompt="say pong", expected_behavior="says pong",
                )  # fmt: skip
                for index in range(8)
            ),
        )

        started = await usage.call(
            "POST", "/api/eval/runs", by=owner, json={"agent_id": agent.id, "dataset_id": "ds-1"}
        )

        run_id = started.json()["data"]["id"]
        await _recorded(usage, provider, by=owner)
        async with create_session() as db:
            run = await db.get(EvalRun, run_id)
            results = (
                await db.execute(select(EvalCaseResult).where(EvalCaseResult.run_id == run_id))
            ).scalars().all()
        # Five cases run at a time: the first five started on an unused
        # quota and finished; the other three did not start.
        assert len(results) == 5
        assert run is not None and run.status == "failed"
        assert "token_quota_exceeded" in (run.error_message or "")
        assert "3 of 8 cases were not run" in (run.error_message or "")

    async def test_a_chat_with_a_builder_assistant(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        alice = await usage.user("alice")
        target = await usage.agent(alice, name="Target")
        session = await usage.call(
            "POST",
            "/api/builder/session",
            by=alice,
            json={"target_type": "agent", "target_id": target.id},
        )
        builder_id = session.json()["data"]["builder_agent_id"]
        provider.use_tool("agent_get_settings", {})

        sent = await usage.call(
            "POST",
            "/api/react",
            json={"q": "what is it set to?", "agent_id": builder_id, "token": usage.token(alice)},
        )

        assert "agent_get_settings" in sent.text and "event: done" in sent.text
        rows = await _recorded(usage, provider, by=alice)
        assert {row.agent_id for row in rows} == {builder_id}
        assert _entries(rows) == {"chat"}

    async def test_the_vision_precheck_calls_no_model(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        """It reads the model's configuration; nothing is consumed, nothing recorded."""
        alice = await usage.user("alice")

        answer = await usage.call("GET", "/api/models/effective/vision", by=alice)

        assert answer.json()["data"] == {"supports_vision": False}
        assert provider.calls == 0
        assert await usage.rows() == []

    async def test_a_document_read_by_the_vision_model_while_it_is_ingested(
        self, usage: Usage, provider: Provider, monkeypatch: Any, tmp_path: Any  # noqa: F811
    ) -> None:
        """Adding a document to a knowledge base may have a vision model read
        its images. The loader runs in a worker thread."""
        import asyncio

        from fim_one.core.model.litellm_openai_shim import LiteLLMOpenAIShim
        from fim_one.db.models.knowledge_base import KBDocument, KnowledgeBase
        from fim_one.web.api import knowledge_bases as kb_api
        from fim_one.web.deps import get_llm

        alice = await usage.user("alice")
        org = await usage.org("acme", alice)
        await usage.add(
            KnowledgeBase(id="kb-1", user_id=alice.id, name="handbook", org_id=org.id),
            KBDocument(
                id="doc-1", kb_id="kb-1", filename="scan.pdf", file_path="scan.pdf",
                file_size=1, file_type="pdf", status="processing",
            ),  # fmt: skip
        )

        class _Manager:
            async def ingest_file(self, _path: Any, *, vision_llm: Any, **_kw: Any) -> Any:
                def _load() -> None:
                    shim = LiteLLMOpenAIShim(vision_llm)
                    for _page in range(2):
                        shim.chat.completions.create(messages=[{"role": "user", "content": "x"}])

                await asyncio.to_thread(_load)
                return 2, "hash"

        async def _vision(_agent_cfg: Any, _db: Any) -> Any:
            return get_llm()

        monkeypatch.setattr(kb_api, "get_kb_manager", lambda: _Manager())
        monkeypatch.setattr("fim_one.web.api.chat._build_markitdown_vision_deps", _vision)

        await kb_api._ingest_document(
            doc_id="doc-1", kb_id="kb-1", user_id=alice.id, file_path=tmp_path / "scan.pdf",
            chunk_strategy="recursive", chunk_size=1000, chunk_overlap=200,
        )  # fmt: skip

        # The knowledge base's organization is on the row.
        rows = await _recorded(usage, provider, by=alice, org=org)
        assert provider.calls == 2
        assert _sources(rows) == {("endpoint", "kb-1")}
        assert _entries(rows) == {"kb_ingest"}


    @pytest.mark.parametrize("refusal", ["over_quota", "unentitled"])
    async def test_a_user_the_quota_refuses_gets_the_document_read_without_the_vision_model(
        self,
        usage: Usage,  # noqa: F811
        provider: Provider,  # noqa: F811
        monkeypatch: Any,
        tmp_path: Any,
        caplog: pytest.LogCaptureFixture,
        refusal: str,
    ) -> None:
        """The upload is not refused; the vision model is left out, and that is logged."""
        import asyncio
        import logging

        from fim_one.core.usage.ledger import UsageEntry, write_entries
        from fim_one.db.models import SystemSetting
        from fim_one.db.models.knowledge_base import KBDocument, KnowledgeBase
        from fim_one.web.api import knowledge_bases as kb_api
        from fim_one.web.deps import get_llm

        alice = await usage.user("alice")
        if refusal == "over_quota":
            await usage.cap(50)
            await write_entries(
                [
                    UsageEntry(
                        user_id=alice.id, org_id=None, source_type="conversation",
                        source_id="c-1", entry="chat", model="m", total_tokens=60,
                    )
                ]
            )  # fmt: skip
        else:
            await usage.add(SystemSetting(key="access_model", value="paid_only"))
        await usage.add(
            KnowledgeBase(id="kb-1", user_id=alice.id, name="handbook"),
            KBDocument(
                id="doc-1", kb_id="kb-1", filename="scan.pdf", file_path="scan.pdf",
                file_size=1, file_type="pdf", status="processing",
            ),  # fmt: skip
        )
        given: list[Any] = []

        class _Manager:
            async def ingest_file(self, _path: Any, *, vision_llm: Any, **_kw: Any) -> Any:
                given.append(vision_llm)
                await asyncio.sleep(0)
                return 2, "hash"

        async def _vision(_agent_cfg: Any, _db: Any) -> Any:
            return get_llm()

        monkeypatch.setattr(kb_api, "get_kb_manager", lambda: _Manager())
        monkeypatch.setattr("fim_one.web.api.chat._build_markitdown_vision_deps", _vision)

        with caplog.at_level(logging.INFO, logger="fim_one.web.api.knowledge_bases"):
            await kb_api._ingest_document(
                doc_id="doc-1", kb_id="kb-1", user_id=alice.id, file_path=tmp_path / "scan.pdf",
                chunk_strategy="recursive", chunk_size=1000, chunk_overlap=200,
            )  # fmt: skip

        assert given == [None]
        assert provider.calls == 0
        assert "without the vision model" in caplog.text
        from fim_one.db import create_session

        async with create_session() as db:
            doc = await db.get(KBDocument, "doc-1")
            assert doc is not None and doc.chunk_count == 2



# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


def _node(node_id: str, kind: str, type_name: str, **data: Any) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": kind,
        "position": {"x": 0, "y": 0},
        "data": {"type": type_name, **data},
    }


def _blueprint(*steps: dict[str, Any]) -> dict[str, Any]:
    """start -> the steps in a row -> end."""
    nodes = [_node("start_1", "start", "START"), *steps, _node("end_1", "end", "END")]
    ids = [node["id"] for node in nodes]
    return {
        "schema_version": 2,
        "nodes": nodes,
        "edges": [
            {"id": f"{a}->{b}", "source": a, "target": b} for a, b in zip(ids, ids[1:], strict=False)
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


def _llm(node_id: str) -> dict[str, Any]:
    return _node(node_id, "llm", "LLM", prompt_template="say pong")


# One model call per run.
ONE_STEP = _blueprint(_llm("llm_1"))
# A model call, a wait for a person, another model call.
WITH_APPROVAL = _blueprint(
    _llm("llm_1"),
    _node("human_1", "humanIntervention", "HUMAN_INTERVENTION", title="Go on?", timeout_hours=1),
    _llm("llm_2"),
)


@pytest.fixture()
async def runs(usage: Usage, tmp_path: Any, monkeypatch: Any) -> Any:  # noqa: F811
    """The run service as a server process has it, with its limits out of the way."""
    import asyncio

    from fim_one.core.workflow import run_service as rs
    from fim_one.core.workflow.rate_limiter import WorkflowRateLimiter
    from fim_one.core.workflow.run_recovery import install_resume_on_decision

    service = rs.WorkflowRunService(
        rate_limiter=WorkflowRateLimiter(max_runs_per_minute=100_000, max_concurrent_runs=1000)
    )
    monkeypatch.setattr(rs, "_default_service", service)
    monkeypatch.setattr("fim_one.core.assembly.sandbox._BASE", tmp_path / "run-sandboxes")
    monkeypatch.setenv("WORKFLOW_HUMAN_INTERVENTION_ENABLED", "true")
    # The module is shelved until an administrator switches it on.
    from fim_one.db.models import SystemSetting

    await usage.add(SystemSetting(key="feature.workflows", value="true"))
    remove = install_resume_on_decision(service)
    yield service
    for _ in range(200):
        if not service.active_run_ids():
            break
        await asyncio.sleep(0.02)
    remove()


async def _workflow(
    usage: Usage,  # noqa: F811
    owner: User,
    blueprint: dict[str, Any] = ONE_STEP,
    **fields: Any,
) -> Any:
    from fim_one.db.models.workflow import Workflow

    workflow = Workflow(
        id=fields.pop("id", "wf-1"),
        user_id=owner.id,
        name=fields.pop("name", "pong"),
        blueprint=blueprint,
        status="active",
        is_active=True,
        **fields,
    )
    await usage.add(workflow)
    return workflow


async def _run_rows(usage: Usage, entry: str) -> list[UsageLedger]:  # noqa: F811
    """The ledger's rows of workflow runs, all of them from *entry*."""
    rows = await usage.rows(source_type="workflow_run")
    assert rows and _entries(rows) == {entry}
    assert {row.workflow_id for row in rows} == {"wf-1"}
    return rows


async def _stored_runs(mode: str | None = None) -> list[Any]:
    from sqlalchemy import select

    from fim_one.db import create_session
    from fim_one.db.models.workflow import WorkflowRun

    async with create_session() as db:
        query = select(WorkflowRun)
        if mode is not None:
            query = query.where(WorkflowRun.mode == mode)
        return list((await db.execute(query)).scalars().all())


class TestWorkflows:
    async def test_an_interactive_run(
        self, usage: Usage, provider: Provider, runs: Any  # noqa: F811
    ) -> None:
        owner, alice = await usage.user("owner"), await usage.user("alice")
        org = await usage.org("acme", owner, alice)
        workflow = await _workflow(usage, owner, org_id=org.id, visibility="org")
        await usage.subscribe(alice, "workflow", workflow)

        stream = await usage.call("POST", "/api/workflows/wf-1/run", by=alice, json={"inputs": {}})

        assert "run_completed" in stream.text
        # Whoever runs it pays; the workflow's organization is on the row.
        rows = await _recorded(usage, provider, by=alice, org=org)
        (run,) = await _stored_runs()
        assert _sources(rows) == {("workflow_run", run.id)}
        assert _entries(rows) == {"interactive"}
        assert sum(row.total_tokens for row in rows) == run.total_tokens == UNIT

    async def test_a_batch(self, usage: Usage, provider: Provider, runs: Any) -> None:  # noqa: F811
        alice = await usage.user("alice")
        await _workflow(usage, alice)

        await usage.call(
            "POST", "/api/workflows/wf-1/batch-run", by=alice, json={"inputs": [{}, {}, {}]}
        )

        rows = await _recorded(usage, provider, by=alice)
        stored = await _stored_runs("batch")
        assert len(stored) == 3
        # One row per item, each on its own run.
        assert _sources(rows) == {("workflow_run", run.id) for run in stored}
        assert _entries(rows) == {"batch"}
        assert provider.calls == 3

    async def test_a_batch_that_uses_up_the_quota_starts_no_further_item(
        self, usage: Usage, provider: Provider, runs: Any  # noqa: F811
    ) -> None:
        """Admitted once; between items it is checked from what the items consumed."""
        alice = await usage.user("alice")
        await usage.cap(UNIT)
        await _workflow(usage, alice)

        answer = await usage.call(
            "POST", "/api/workflows/wf-1/batch-run", by=alice,
            json={"inputs": [{}, {}, {}], "max_parallel": 1},
        )  # fmt: skip

        results = answer.json()["data"]["results"]
        assert [item["status"] for item in results] == ["completed", "failed", "failed"]
        assert all("token_quota_exceeded" in item["error"] for item in results[1:])
        assert provider.calls == 1
        await _recorded(usage, provider, by=alice)

    async def test_a_trigger_key_is_billed_to_the_author(
        self, usage: Usage, provider: Provider, runs: Any  # noqa: F811
    ) -> None:
        author = await usage.user("author")
        await _workflow(usage, author)
        key = (
            await usage.call("POST", "/api/workflows/wf-1/generate-api-key", by=author)
        ).json()["data"]["api_key"]

        # Nobody is signed in: the key is all the caller has.
        answer = await usage.call(
            "POST",
            "/api/workflows/trigger",
            json={"inputs": {}},
            headers={"Authorization": f"Bearer {key}"},
        )

        assert answer.json()["data"]["status"] == "completed"
        rows = await _recorded(usage, provider, by=author)
        await _run_rows(usage, "trigger")
        assert len(rows) == 1

    async def test_a_schedule_is_billed_to_the_author(
        self, usage: Usage, provider: Provider, runs: Any  # noqa: F811
    ) -> None:
        from fim_one.core.workflow.scheduler import WorkflowScheduler

        author = await usage.user("author")
        workflow = await _workflow(usage, author, schedule_cron="* * * * *", schedule_enabled=True)

        await WorkflowScheduler()._execute_workflow(workflow)

        rows = await _recorded(usage, provider, by=author)
        await _run_rows(usage, "schedule")
        assert len(rows) == 1

    async def test_a_run_an_agent_starts_as_a_tool_is_recorded_on_the_run_only(
        self, usage: Usage, provider: Provider, runs: Any  # noqa: F811
    ) -> None:
        alice = await usage.user("alice")
        await _workflow(usage, alice)
        conversation = await usage.conversation(alice)
        provider.use_tool("run_workflow", {"name": "pong"})

        sent = await usage.turn("/api/react", alice, conversation=conversation)

        tools = [d.get("tool_name") for name, d in sent if name == "step" and isinstance(d, dict)]
        assert "run_workflow" in tools, tools
        rows = await _recorded(usage, provider, by=alice)
        (run,) = await _stored_runs("tool")
        on_the_run = [row for row in rows if row.source_type == "workflow_run"]
        on_the_conversation = [row for row in rows if row.source_type == "conversation"]
        # The run's one model call is on the run, and not also on the turn.
        assert [(row.source_id, row.entry, row.total_tokens) for row in on_the_run] == [
            (run.id, "tool", UNIT)
        ]
        assert {row.source_id for row in on_the_conversation} == {conversation.id}
        assert sum(row.total_tokens for row in on_the_conversation) == provider.billed - UNIT
        assert len(on_the_run) + len(on_the_conversation) == len(rows)

    async def test_a_single_step_test(
        self, usage: Usage, provider: Provider, runs: Any  # noqa: F811
    ) -> None:
        alice = await usage.user("alice")
        await _workflow(usage, alice)

        answer = await usage.call(
            "POST",
            "/api/workflows/wf-1/test-node",
            by=alice,
            json={"node_id": "llm_1", "variables": {}},
        )

        assert answer.json()["data"]["status"] == "completed", answer.text
        rows = await _recorded(usage, provider, by=alice)
        (row,) = await _run_rows(usage, "test_node")
        assert row.total_tokens == UNIT == sum(r.total_tokens for r in rows)
        assert row.source_id is not None and row.source_id.startswith("test-")
        # The test is in the ledger; it leaves no run record behind.
        assert await _stored_runs() == []

    async def test_a_run_that_waited_records_each_part_once(
        self, usage: Usage, provider: Provider, runs: Any  # noqa: F811
    ) -> None:
        import asyncio

        from fim_one.core.assembly.confirmation import ConfirmationService
        from fim_one.core.assembly.principal import AuthMethod, resolve_principal
        from fim_one.db import create_session
        from fim_one.db.models.channel import ConfirmationRequest
        from fim_one.db.models.workflow import WorkflowRun
        from sqlalchemy import select

        alice = await usage.user("alice")
        await _workflow(usage, alice, WITH_APPROVAL)

        stream = await usage.call("POST", "/api/workflows/wf-1/run", by=alice, json={"inputs": {}})

        assert "run_waiting" in stream.text
        (run,) = await _stored_runs()
        # The part before the wait is in the ledger while the run waits.
        assert await usage.recorded() == UNIT == provider.billed
        assert (run.status, run.total_tokens) == ("waiting", UNIT)

        async with create_session() as db:
            request = (
                await db.execute(
                    select(ConfirmationRequest).where(ConfirmationRequest.workflow_run_id == run.id)
                )
            ).scalar_one()
            actor = await resolve_principal(db, user_id=alice.id, auth=AuthMethod.SESSION)
            await ConfirmationService(create_session).decide(
                db, confirmation_id=request.id, actor=actor, decision="approve"
            )
        status, total = "waiting", 0
        for _ in range(500):
            async with create_session() as db:
                finished = await db.get(WorkflowRun, run.id)
            assert finished is not None
            status, total = finished.status, finished.total_tokens
            if status not in ("waiting", "running", "pending"):
                break
            await asyncio.sleep(0.02)

        assert (status, total) == ("completed", 2 * UNIT)
        rows = await _recorded(usage, provider, by=alice)
        assert provider.calls == 2
        # Two parts of one run, one row each: the run's total, counted once.
        assert [(row.source_id, row.entry, row.total_tokens) for row in rows] == [
            (run.id, "interactive", UNIT)
        ] * 2


class TestWhatOutlivesItsSource:
    async def test_deleting_the_user_keeps_what_they_consumed_in_the_ledger(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        """The ledger has no foreign key: deleting the account neither fails
        on it nor removes its rows."""
        alice = await usage.user("alice")
        conversation = await usage.conversation(alice)
        await usage.turn("/api/react", alice, conversation=conversation)
        before = await usage.rows()
        assert before

        await usage.call("DELETE", "/api/auth/account", by=alice)

        after = await usage.rows()
        assert sorted(row.id for row in after) == sorted(row.id for row in before)
        assert {row.user_id for row in after} == {alice.id}
        assert sum(row.total_tokens for row in after) == provider.billed
