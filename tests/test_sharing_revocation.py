"""Taking a shared resource back actually removes access.

A subscription row used to be sufficient proof of access on its own, so
every path that stopped sharing a resource — the owner unpublishing it, an
admin pulling it out of the Market, an org being deleted — left the people
who had subscribed still holding it. These tests pin the revocation side of
each of those paths, plus the cleanup that keeps subscription rows from
outliving the resources they name.
"""
from __future__ import annotations

import uuid
from typing import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.db.models.agent import Agent
from fim_one.db.models.connector import Connector
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.db.models.skill import Skill
from fim_one.db.models.user import User
from fim_one.web.api.market import purge_resource_subscriptions
from fim_one.web.api.organizations import _dissolve_org_sharing
from fim_one.web.platform import MARKET_ORG_ID
from fim_one.web.visibility import build_visibility_filter

OWNER = "owner-user"
SUBSCRIBER = "subscriber-user"
ORG_ID = "org-1"


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    import fim_one.db.models  # noqa: F401 — register all models

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _visible_agent_ids(db: AsyncSession, user_id: str) -> set[str]:
    """Agent ids *user_id* can reach, through the shared visibility filter."""
    subscribed = (
        await db.execute(
            select(ResourceSubscription.resource_id).where(
                ResourceSubscription.user_id == user_id,
                ResourceSubscription.resource_type == "agent",
            )
        )
    ).scalars().all()
    clause = build_visibility_filter(
        Agent, user_id, [], subscribed_ids=list(subscribed)
    )
    result = await db.execute(select(Agent.id).where(clause))
    return set(result.scalars().all())


async def _publish_agent_to(
    db: AsyncSession, *, agent_id: str, org_id: str
) -> Agent:
    agent = Agent(
        id=agent_id,
        user_id=OWNER,
        name=f"agent-{agent_id}",
        visibility="org",
        org_id=org_id,
        status="published",
    )
    db.add(agent)
    db.add(
        ResourceSubscription(
            user_id=SUBSCRIBER,
            resource_type="agent",
            resource_id=agent_id,
            org_id=org_id,
        )
    )
    await db.commit()
    return agent


class TestUnpublishRevokes:
    @pytest.mark.asyncio
    async def test_subscriber_loses_access_when_owner_unpublishes(
        self, session: AsyncSession
    ) -> None:
        agent = await _publish_agent_to(
            session, agent_id="a1", org_id=MARKET_ORG_ID
        )
        assert "a1" in await _visible_agent_ids(session, SUBSCRIBER)

        # What POST /agents/{id}/unpublish writes.
        agent.visibility = "personal"
        agent.org_id = None
        agent.publish_status = None
        agent.status = "draft"
        await session.commit()

        assert "a1" not in await _visible_agent_ids(session, SUBSCRIBER)
        assert "a1" in await _visible_agent_ids(session, OWNER)

    @pytest.mark.asyncio
    async def test_pending_review_is_not_reachable(
        self, session: AsyncSession
    ) -> None:
        agent = await _publish_agent_to(
            session, agent_id="a2", org_id=MARKET_ORG_ID
        )
        agent.publish_status = "pending_review"
        await session.commit()

        assert "a2" not in await _visible_agent_ids(session, SUBSCRIBER)

    @pytest.mark.asyncio
    async def test_approved_stays_reachable(self, session: AsyncSession) -> None:
        agent = await _publish_agent_to(
            session, agent_id="a3", org_id=MARKET_ORG_ID
        )
        agent.publish_status = "approved"
        await session.commit()

        assert "a3" in await _visible_agent_ids(session, SUBSCRIBER)


class TestOrgDeletion:
    @pytest.mark.asyncio
    async def test_deleting_an_org_detaches_its_resources(
        self, session: AsyncSession
    ) -> None:
        """Also the fix for a hard failure: ``org_id`` is a foreign key with
        no ``ON DELETE``, so deleting an org anyone published into used to
        fail on the constraint under PostgreSQL."""
        session.add(
            Organization(id=ORG_ID, name="Org", slug="org", owner_id=OWNER)
        )
        session.add(
            OrgMembership(org_id=ORG_ID, user_id=SUBSCRIBER, role="member")
        )
        await session.commit()
        await _publish_agent_to(session, agent_id="a4", org_id=ORG_ID)
        session.add(
            Connector(
                id="c1",
                user_id=OWNER,
                name="conn",
                type="api",
                visibility="org",
                org_id=ORG_ID,
            )
        )
        await session.commit()

        await _dissolve_org_sharing(session, ORG_ID)
        org = await session.get(Organization, ORG_ID)
        assert org is not None
        await session.delete(org)
        await session.commit()

        agent = await session.get(Agent, "a4")
        assert agent is not None
        assert agent.org_id is None
        assert agent.visibility == "personal"

        connector = await session.get(Connector, "c1")
        assert connector is not None
        assert connector.org_id is None

        remaining = await session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.org_id == ORG_ID
            )
        )
        assert remaining.scalars().all() == []

    @pytest.mark.asyncio
    async def test_market_subscriptions_survive_an_unrelated_org_deletion(
        self, session: AsyncSession
    ) -> None:
        session.add(
            Organization(id=ORG_ID, name="Org", slug="org", owner_id=OWNER)
        )
        session.add(
            OrgMembership(org_id=ORG_ID, user_id=SUBSCRIBER, role="member")
        )
        await session.commit()
        await _publish_agent_to(session, agent_id="a5", org_id=MARKET_ORG_ID)

        await _dissolve_org_sharing(session, ORG_ID)
        await session.commit()

        assert "a5" in await _visible_agent_ids(session, SUBSCRIBER)


class TestSubscriptionCleanup:
    @pytest.mark.asyncio
    async def test_deleting_a_resource_drops_its_subscriptions(
        self, session: AsyncSession
    ) -> None:
        await _publish_agent_to(session, agent_id="a6", org_id=MARKET_ORG_ID)

        await purge_resource_subscriptions(
            session, resource_type="agent", resource_id="a6"
        )
        await session.commit()

        rows = await session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.resource_id == "a6"
            )
        )
        assert rows.scalars().all() == []

    @pytest.mark.asyncio
    async def test_purge_is_scoped_to_one_resource_type(
        self, session: AsyncSession
    ) -> None:
        """Ids come from different tables; a skill must not clear an agent."""
        shared_id = str(uuid.uuid4())
        session.add(
            ResourceSubscription(
                user_id=SUBSCRIBER,
                resource_type="agent",
                resource_id=shared_id,
                org_id=MARKET_ORG_ID,
            )
        )
        session.add(
            ResourceSubscription(
                user_id=SUBSCRIBER,
                resource_type="skill",
                resource_id=shared_id,
                org_id=MARKET_ORG_ID,
            )
        )
        await session.commit()

        await purge_resource_subscriptions(
            session, resource_type="skill", resource_id=shared_id
        )
        await session.commit()

        rows = (
            await session.execute(
                select(ResourceSubscription.resource_type).where(
                    ResourceSubscription.resource_id == shared_id
                )
            )
        ).scalars().all()
        assert rows == ["agent"]


class TestSubscriptionSourceDefault:
    @pytest.mark.asyncio
    async def test_a_row_written_without_a_source_is_direct(
        self, session: AsyncSession
    ) -> None:
        """Rows predating the column read as ``direct``, so the cascade
        leaves them alone rather than guessing."""
        session.add(
            ResourceSubscription(
                user_id=SUBSCRIBER,
                resource_type="skill",
                resource_id="s1",
                org_id=MARKET_ORG_ID,
            )
        )
        await session.commit()

        row = (
            await session.execute(
                select(ResourceSubscription).where(
                    ResourceSubscription.resource_id == "s1"
                )
            )
        ).scalar_one()
        assert row.source == "direct"


class TestMarketSkillDeletion:
    @pytest.mark.asyncio
    async def test_a_skill_two_agents_use_is_not_orphan(
        self, session: AsyncSession
    ) -> None:
        """The reference scan behind admin Market deletion.

        Deleting one agent used to take its listed skills with it, breaking
        any other Market agent that listed the same skill.
        """
        session.add_all(
            [
                Agent(
                    id="a7",
                    user_id=OWNER,
                    name="first",
                    org_id=MARKET_ORG_ID,
                    visibility="org",
                    skill_ids=["sk1", "sk2"],
                ),
                Agent(
                    id="a8",
                    user_id=OWNER,
                    name="second",
                    org_id=MARKET_ORG_ID,
                    visibility="org",
                    skill_ids=["sk2"],
                ),
                Skill(id="sk1", user_id=OWNER, name="only-first", content="x"),
                Skill(id="sk2", user_id=OWNER, name="shared", content="x"),
            ]
        )
        await session.commit()

        others = await session.execute(
            select(Agent.skill_ids).where(
                Agent.org_id == MARKET_ORG_ID, Agent.id != "a7"
            )
        )
        still_referenced: set[str] = set()
        for (skill_ids,) in others.all():
            still_referenced.update(skill_ids or [])

        orphans = [s for s in ["sk1", "sk2"] if s not in still_referenced]
        assert orphans == ["sk1"]
