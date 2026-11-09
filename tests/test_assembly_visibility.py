"""The use-time visibility rule (``core/assembly/visibility.py``).

One rule for every resource kind: the subject (the binding's owner, else the
acting principal) must be able to see the resource and it must be switched
on; knowledge bases are owner-only; database connectors additionally require
the acting principal to be the owner.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import (
    Binding,
    CallContext,
    provisional_principal,
)
from fim_one.core.assembly.visibility import (
    ResourceKind,
    authorize_entry,
    authorize_use,
    build_visibility_filter,
    is_shared_clause,
    list_usable,
    visibility_clause,
    visible_clause_for,
)
from fim_one.db.base import Base
from fim_one.db.models.agent import Agent
from fim_one.db.models.connector import Connector, ConnectorAction
from fim_one.db.models.knowledge_base import KnowledgeBase
from fim_one.db.models.mcp_server import MCPServer
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.db.models.skill import Skill
from fim_one.db.models.workflow import Workflow

OWNER = "u-owner"
MEMBER = "u-member"  # same org, not subscribed
SUBSCRIBER = "u-subscriber"
OUTSIDER = "u-outsider"  # another org
ORG = "org-1"

KINDS = [
    ResourceKind.AGENT,
    ResourceKind.CONNECTOR,
    ResourceKind.MCP_SERVER,
    ResourceKind.SKILL,
    ResourceKind.WORKFLOW,
]


def _shared(kind: ResourceKind, rid: str, **extra: Any) -> Any:
    """A resource OWNER published to ORG."""
    common: dict[str, Any] = {
        "id": rid,
        "user_id": OWNER,
        "name": rid,
        "visibility": "org",
        "org_id": ORG,
        "is_active": True,
    }
    common.update(extra)
    if kind is ResourceKind.AGENT:
        return Agent(**common)
    if kind is ResourceKind.CONNECTOR:
        common.setdefault("type", "api")
        return Connector(base_url="https://api.example.com", **common)
    if kind is ResourceKind.MCP_SERVER:
        return MCPServer(transport="streamable_http", url="https://mcp.example.com", **common)
    if kind is ResourceKind.SKILL:
        return Skill(content="do the thing", **common)
    if kind is ResourceKind.WORKFLOW:
        return Workflow(blueprint={"nodes": [], "edges": []}, **common)
    return KnowledgeBase(**common)


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        for kind in KINDS:
            s.add(_shared(kind, f"{kind.value}-on"))
            s.add(_shared(kind, f"{kind.value}-off", is_active=False))
            s.add(_shared(kind, f"{kind.value}-private", visibility="personal", org_id=None))
            for rid in (f"{kind.value}-on", f"{kind.value}-off", f"{kind.value}-private"):
                # The subscription to the private row is stale: it must grant nothing.
                s.add(
                    ResourceSubscription(
                        user_id=SUBSCRIBER, resource_type=kind.value, resource_id=rid, org_id=ORG
                    )
                )
        s.add(_shared(ResourceKind.CONNECTOR, "db-conn", type="database"))
        s.add(ConnectorAction(connector_id="connector-on", name="ping", method="GET", path="/p"))
        s.add(
            ResourceSubscription(
                user_id=SUBSCRIBER, resource_type="connector", resource_id="db-conn", org_id=ORG
            )
        )
        s.add(KnowledgeBase(id="kb-on", user_id=OWNER, name="kb-on"))
        s.add(KnowledgeBase(id="kb-off", user_id=OWNER, name="kb-off", is_active=False))
        # A KB cannot be shared; a leftover org-visible row with a subscription
        # is the worst case and still grants nothing.
        s.add(
            KnowledgeBase(id="kb-leftover", user_id=OWNER, name="l", visibility="org", org_id=ORG)
        )
        s.add(
            ResourceSubscription(
                user_id=SUBSCRIBER,
                resource_type="knowledge_base",
                resource_id="kb-leftover",
                org_id=ORG,
            )
        )
        await s.commit()
        yield s
    await engine.dispose()


def _direct(user_id: str) -> CallContext:
    return CallContext(principal=provisional_principal(user_id), binding=None, entry="chat_noagent")


def _through_owner_agent(user_id: str) -> CallContext:
    return CallContext(
        principal=provisional_principal(user_id),
        binding=Binding(kind="agent", id="agent-on", owner_id=OWNER, org_id=ORG),
        entry="chat",
    )


class TestClause:
    def test_build_visibility_filter_is_the_same_clause(self) -> None:
        old = build_visibility_filter(Connector, OWNER, ["ignored"], subscribed_ids=["c1"])
        new = visibility_clause(Connector, OWNER, ["c1"])

        assert str(old.compile(compile_kwargs={"literal_binds": True})) == str(
            new.compile(compile_kwargs={"literal_binds": True})
        )

    def test_shared_means_org_visible_and_not_held_by_review(self) -> None:
        sql = str(is_shared_clause(Connector).compile(compile_kwargs={"literal_binds": True}))

        assert "visibility = 'org'" in sql
        assert "publish_status IS NULL" in sql
        assert "publish_status = 'approved'" in sql

    async def test_visible_clause_for_reads_the_users_subscriptions(
        self, session: AsyncSession
    ) -> None:
        from sqlalchemy import select

        clause = await visible_clause_for(session, ResourceKind.CONNECTOR, SUBSCRIBER)
        ids = set((await session.execute(select(Connector.id).where(clause))).scalars().all())

        assert ids == {"connector-on", "connector-off", "db-conn"}


class TestAuthorizeUse:
    @pytest.mark.parametrize("kind", KINDS)
    @pytest.mark.parametrize(
        ("actor", "usable"),
        [(OWNER, True), (SUBSCRIBER, True), (MEMBER, False), (OUTSIDER, False)],
    )
    async def test_direct_use_follows_the_actors_own_view(
        self, session: AsyncSession, kind: ResourceKind, actor: str, usable: bool
    ) -> None:
        rid = f"{kind.value}-on"

        decision = await authorize_use(session, _direct(actor), kind, [rid])

        if usable:
            assert [r.id for r in decision.usable] == [rid]
            assert decision.refused == {}
        else:
            assert decision.usable == []
            assert decision.refused[rid].error_code == "resource_not_found"
            assert decision.refused[rid].status_code == 404

    @pytest.mark.parametrize("kind", KINDS)
    @pytest.mark.parametrize("actor", [OWNER, SUBSCRIBER, MEMBER, OUTSIDER])
    async def test_a_binding_delegates_the_owners_view(
        self, session: AsyncSession, kind: ResourceKind, actor: str
    ) -> None:
        """Whoever runs the owner's agent uses what the owner can see."""
        rid = f"{kind.value}-on"

        decision = await authorize_use(session, _through_owner_agent(actor), kind, [rid])

        assert [r.id for r in decision.usable] == [rid]

    @pytest.mark.parametrize("kind", KINDS)
    async def test_switched_off_is_inactive_not_missing(
        self, session: AsyncSession, kind: ResourceKind
    ) -> None:
        rid = f"{kind.value}-off"

        decision = await authorize_use(session, _direct(OWNER), kind, [rid])

        assert decision.usable == []
        refusal = decision.refused[rid]
        assert (refusal.error_code, refusal.status_code) == ("resource_inactive", 409)
        assert refusal.detail.endswith(f"'{rid}' is disabled")

    @pytest.mark.parametrize("kind", KINDS)
    async def test_switched_off_and_invisible_stays_missing(
        self, session: AsyncSession, kind: ResourceKind
    ) -> None:
        """Existence is not disclosed to someone who cannot see the row."""
        rid = f"{kind.value}-off"

        decision = await authorize_use(session, _direct(OUTSIDER), kind, [rid])

        assert decision.refused[rid].error_code == "resource_not_found"

    @pytest.mark.parametrize("kind", KINDS)
    async def test_a_subscription_to_an_unpublished_resource_grants_nothing(
        self, session: AsyncSession, kind: ResourceKind
    ) -> None:
        rid = f"{kind.value}-private"

        decision = await authorize_use(session, _direct(SUBSCRIBER), kind, [rid])

        assert decision.refused[rid].error_code == "resource_not_found"

    async def test_order_follows_the_request_and_duplicates_collapse(
        self, session: AsyncSession
    ) -> None:
        asked = ["connector-private", "missing", "connector-on", "connector-private"]

        decision = await authorize_use(session, _direct(OWNER), ResourceKind.CONNECTOR, asked)

        assert [r.id for r in decision.usable] == ["connector-private", "connector-on"]
        assert list(decision.refused) == ["missing"]

    async def test_no_ids_asks_nothing(self, session: AsyncSession) -> None:
        decision = await authorize_use(session, _direct(OWNER), ResourceKind.CONNECTOR, [])

        assert decision.usable == [] and decision.refused == {}

    async def test_loader_options_are_applied(self, session: AsyncSession) -> None:
        decision = await authorize_use(
            session,
            _direct(SUBSCRIBER),
            ResourceKind.CONNECTOR,
            ["connector-on"],
            options=[selectinload(Connector.actions)],
        )
        session.expunge_all()

        assert [a.name for a in decision.usable[0].actions] == ["ping"]


class TestKnowledgeBasesAreOwnerOnly:
    async def test_owner_uses_own_kb(self, session: AsyncSession) -> None:
        decision = await authorize_use(
            session, _direct(OWNER), ResourceKind.KNOWLEDGE_BASE, ["kb-on", "kb-off"]
        )

        assert [r.id for r in decision.usable] == ["kb-on"]
        assert decision.refused["kb-off"].error_code == "resource_inactive"

    async def test_a_binding_delegates_the_owners_kb(self, session: AsyncSession) -> None:
        decision = await authorize_use(
            session, _through_owner_agent(OUTSIDER), ResourceKind.KNOWLEDGE_BASE, ["kb-on"]
        )

        assert [(r.id, r.user_id) for r in decision.usable] == [("kb-on", OWNER)]

    async def test_a_leftover_subscription_grants_nothing(self, session: AsyncSession) -> None:
        decision = await authorize_use(
            session, _direct(SUBSCRIBER), ResourceKind.KNOWLEDGE_BASE, ["kb-leftover"]
        )

        assert decision.usable == []
        assert decision.refused["kb-leftover"].error_code == "resource_not_found"
        assert decision.including("resource_not_found") == []


class TestDatabaseConnectorsAreOwnerOnly:
    async def test_owner_may_use_it(self, session: AsyncSession) -> None:
        decision = await authorize_use(session, _direct(OWNER), ResourceKind.CONNECTOR, ["db-conn"])

        assert [r.id for r in decision.usable] == ["db-conn"]

    @pytest.mark.parametrize("ctx_for", [_direct, _through_owner_agent])
    async def test_anyone_else_is_refused_even_through_the_owners_agent(
        self, session: AsyncSession, ctx_for: Any
    ) -> None:
        decision = await authorize_use(
            session, ctx_for(SUBSCRIBER), ResourceKind.CONNECTOR, ["db-conn"]
        )

        assert decision.usable == []
        refusal = decision.refused["db-conn"]
        assert (refusal.error_code, refusal.status_code) == ("owner_only", 403)


class TestIncluding:
    async def test_names_the_refusals_a_caller_overrides(self, session: AsyncSession) -> None:
        decision = await authorize_use(
            session,
            _direct(SUBSCRIBER),
            ResourceKind.CONNECTOR,
            ["connector-off", "db-conn", "connector-on", "missing"],
        )

        assert [r.id for r in decision.usable] == ["connector-on"]
        assert [r.id for r in decision.including()] == ["connector-on"]
        assert [r.id for r in decision.including("resource_inactive")] == [
            "connector-off",
            "connector-on",
        ]
        assert [r.id for r in decision.including("owner_only")] == ["db-conn", "connector-on"]

    async def test_never_returns_a_row_the_subject_cannot_see(self, session: AsyncSession) -> None:
        decision = await authorize_use(
            session, _direct(OUTSIDER), ResourceKind.CONNECTOR, ["connector-on", "connector-off"]
        )

        assert decision.including("resource_not_found", "resource_inactive", "owner_only") == []


class TestListUsable:
    @pytest.mark.parametrize("kind", KINDS)
    async def test_lists_what_the_actor_sees_and_is_switched_on(
        self, session: AsyncSession, kind: ResourceKind
    ) -> None:
        owner_rows = await list_usable(session, _direct(OWNER), kind)
        subscriber_rows = await list_usable(session, _direct(SUBSCRIBER), kind)
        outsider_rows = await list_usable(session, _direct(OUTSIDER), kind)

        owned = {f"{kind.value}-on", f"{kind.value}-private"}
        if kind is ResourceKind.CONNECTOR:
            owned.add("db-conn")
        assert {r.id for r in owner_rows} == owned
        # A non-owner never gets the database connector.
        assert {r.id for r in subscriber_rows} == {f"{kind.value}-on"}
        assert outsider_rows == []

    async def test_knowledge_bases_list_only_the_actors_own(self, session: AsyncSession) -> None:
        assert {
            r.id for r in await list_usable(session, _direct(OWNER), ResourceKind.KNOWLEDGE_BASE)
        } == {"kb-on", "kb-leftover"}
        assert await list_usable(session, _direct(SUBSCRIBER), ResourceKind.KNOWLEDGE_BASE) == []

    async def test_columns_returns_rows_of_those_columns(self, session: AsyncSession) -> None:
        rows = await list_usable(
            session, _direct(SUBSCRIBER), ResourceKind.SKILL, columns=[Skill.id, Skill.name]
        )

        assert [tuple(r) for r in rows] == [("skill-on", "skill-on")]

    async def test_columns_is_refused_where_the_rule_reads_the_row(
        self, session: AsyncSession
    ) -> None:
        with pytest.raises(ValueError, match="not supported"):
            await list_usable(
                session, _direct(OWNER), ResourceKind.CONNECTOR, columns=[Connector.id]
            )


class TestAuthorizeEntry:
    @pytest.mark.parametrize("kind", [ResourceKind.AGENT, ResourceKind.WORKFLOW])
    async def test_returns_the_row_for_someone_who_sees_it(
        self, session: AsyncSession, kind: Any
    ) -> None:
        row = await authorize_entry(
            session, provisional_principal(SUBSCRIBER), kind, f"{kind.value}-on"
        )

        assert row.id == f"{kind.value}-on"

    async def test_invisible_is_not_found(self, session: AsyncSession) -> None:
        with pytest.raises(AssemblyRefusal) as exc:
            await authorize_entry(
                session, provisional_principal(OUTSIDER), ResourceKind.AGENT, "agent-on"
            )

        assert (exc.value.error_code, exc.value.status_code) == ("resource_not_found", 404)

    async def test_switched_off_is_inactive(self, session: AsyncSession) -> None:
        with pytest.raises(AssemblyRefusal) as exc:
            await authorize_entry(
                session, provisional_principal(OWNER), ResourceKind.WORKFLOW, "workflow-off"
            )

        assert (exc.value.error_code, exc.value.status_code) == ("resource_inactive", 409)
