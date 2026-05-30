"""Tests for Market Solution Template import.

Covers:
1. import_solution_templates creates 8 agents + 8 skills in the Market org.
2. Idempotency -- calling twice does not duplicate records (second call updates).
3. Upsert behaviour -- second import updates existing records, not duplicates.
4. Agent.skill_ids is NOT set (binding via Skill.resource_refs instead).
5. Each skill has resource_refs pointing to the linked agent.
6. Correct field values (visibility, status, publish_status, etc.).
7. Templates are in English.
8. resource_refs are maintained on update (upsert).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.db.models.agent import Agent
from fim_one.db.models.skill import Skill
from fim_one.web.platform import MARKET_ORG_ID, ensure_market_org
from fim_one.web.solution_seeds import SOLUTION_TEMPLATES, import_solution_templates


# ---------------------------------------------------------------------------
# Fixtures -- in-memory SQLite async database
# ---------------------------------------------------------------------------


@pytest.fixture()
async def async_session():
    """Create an in-memory SQLite database with all required tables."""
    import fim_one.db.models  # noqa: F401 -- register all models

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture()
def owner_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestImportSolutionTemplates:
    """Tests for the import_solution_templates function."""

    @pytest.mark.asyncio
    async def test_creates_8_agents_and_8_skills(
        self, async_session: AsyncSession, owner_id: str
    ) -> None:
        """import_solution_templates should create exactly 8 agents and 8 skills."""
        await ensure_market_org(async_session, owner_id=owner_id)
        await async_session.flush()

        result = await import_solution_templates(
            async_session, market_org_id=MARKET_ORG_ID, owner_id=owner_id
        )
        await async_session.flush()

        assert result["created"] == len(SOLUTION_TEMPLATES)
        assert result["updated"] == 0
        assert result["skipped"] == 0

        # Count agents in Market org
        agent_count_result = await async_session.execute(
            select(func.count(Agent.id)).where(Agent.org_id == MARKET_ORG_ID)
        )
        agent_count = agent_count_result.scalar_one()
        assert agent_count == len(SOLUTION_TEMPLATES), (
            f"Expected {len(SOLUTION_TEMPLATES)} agents, got {agent_count}"
        )

        # Count skills in Market org
        skill_count_result = await async_session.execute(
            select(func.count(Skill.id)).where(Skill.org_id == MARKET_ORG_ID)
        )
        skill_count = skill_count_result.scalar_one()
        assert skill_count == len(SOLUTION_TEMPLATES), (
            f"Expected {len(SOLUTION_TEMPLATES)} skills, got {skill_count}"
        )

    @pytest.mark.asyncio
    async def test_idempotency_no_duplicates(
        self, async_session: AsyncSession, owner_id: str
    ) -> None:
        """Calling import_solution_templates twice should not create duplicates."""
        await ensure_market_org(async_session, owner_id=owner_id)
        await async_session.flush()

        # First call -- creates
        await import_solution_templates(
            async_session, market_org_id=MARKET_ORG_ID, owner_id=owner_id
        )
        await async_session.flush()

        # Second call -- updates
        await import_solution_templates(
            async_session, market_org_id=MARKET_ORG_ID, owner_id=owner_id
        )
        await async_session.flush()

        # Should still have exactly 8 agents
        agent_count_result = await async_session.execute(
            select(func.count(Agent.id)).where(Agent.org_id == MARKET_ORG_ID)
        )
        agent_count = agent_count_result.scalar_one()
        assert agent_count == len(SOLUTION_TEMPLATES)

        # Should still have exactly 8 skills
        skill_count_result = await async_session.execute(
            select(func.count(Skill.id)).where(Skill.org_id == MARKET_ORG_ID)
        )
        skill_count = skill_count_result.scalar_one()
        assert skill_count == len(SOLUTION_TEMPLATES)

    @pytest.mark.asyncio
    async def test_upsert_updates_on_second_import(
        self, async_session: AsyncSession, owner_id: str
    ) -> None:
        """Second import should return updated counts and actually update records."""
        await ensure_market_org(async_session, owner_id=owner_id)
        await async_session.flush()

        # First import -- all created
        result1 = await import_solution_templates(
            async_session, market_org_id=MARKET_ORG_ID, owner_id=owner_id
        )
        await async_session.flush()

        assert result1["created"] == len(SOLUTION_TEMPLATES)
        assert result1["updated"] == 0

        # Modify one agent's description to verify it gets updated back
        agents_result = await async_session.execute(
            select(Agent).where(
                Agent.name == "Financial Audit Assistant",
                Agent.org_id == MARKET_ORG_ID,
            )
        )
        agent = agents_result.scalar_one()
        agent.description = "MODIFIED DESCRIPTION"
        await async_session.flush()

        # Second import -- all updated
        result2 = await import_solution_templates(
            async_session, market_org_id=MARKET_ORG_ID, owner_id=owner_id
        )
        await async_session.flush()

        assert result2["created"] == 0
        assert result2["updated"] == len(SOLUTION_TEMPLATES)

        # Verify the description was restored
        agents_result = await async_session.execute(
            select(Agent).where(
                Agent.name == "Financial Audit Assistant",
                Agent.org_id == MARKET_ORG_ID,
            )
        )
        agent = agents_result.scalar_one()
        assert agent.description != "MODIFIED DESCRIPTION"
        assert "financial audit" in agent.description.lower()

    @pytest.mark.asyncio
    async def test_agent_skill_ids_not_set(
        self, async_session: AsyncSession, owner_id: str
    ) -> None:
        """Agent.skill_ids should NOT be set -- binding is via Skill.resource_refs."""
        await ensure_market_org(async_session, owner_id=owner_id)
        await async_session.flush()

        await import_solution_templates(
            async_session, market_org_id=MARKET_ORG_ID, owner_id=owner_id
        )
        await async_session.flush()

        agents_result = await async_session.execute(
            select(Agent).where(Agent.org_id == MARKET_ORG_ID)
        )
        agents = agents_result.scalars().all()

        for agent in agents:
            assert not agent.skill_ids, (
                f"Agent '{agent.name}' should not have skill_ids set, "
                f"got {agent.skill_ids}"
            )

    @pytest.mark.asyncio
    async def test_skill_resource_refs_link_to_agent(
        self, async_session: AsyncSession, owner_id: str
    ) -> None:
        """Each skill should have resource_refs containing the linked agent reference."""
        await ensure_market_org(async_session, owner_id=owner_id)
        await async_session.flush()

        await import_solution_templates(
            async_session, market_org_id=MARKET_ORG_ID, owner_id=owner_id
        )
        await async_session.flush()

        skills_result = await async_session.execute(
            select(Skill).where(Skill.org_id == MARKET_ORG_ID)
        )
        skills = skills_result.scalars().all()

        # Build a lookup of agent IDs by name
        agents_result = await async_session.execute(
            select(Agent).where(Agent.org_id == MARKET_ORG_ID)
        )
        agent_by_name = {a.name: a for a in agents_result.scalars().all()}

        for skill in skills:
            assert skill.resource_refs is not None, (
                f"Skill '{skill.name}' has no resource_refs"
            )
            assert len(skill.resource_refs) == 1, (
                f"Skill '{skill.name}' should have exactly 1 resource_ref, "
                f"got {len(skill.resource_refs)}"
            )
            ref = skill.resource_refs[0]
            assert ref["type"] == "agent"
            assert ref["id"] in {a.id for a in agent_by_name.values()}, (
                f"Skill '{skill.name}' references non-existent agent {ref['id']}"
            )
            assert ref["alias"].startswith("@"), (
                f"Skill '{skill.name}' alias should start with @, got {ref['alias']}"
            )
            # Verify the referenced agent exists and name matches
            referenced_agent_name = ref["name"]
            assert referenced_agent_name in agent_by_name, (
                f"Skill '{skill.name}' references unknown agent name '{referenced_agent_name}'"
            )

    @pytest.mark.asyncio
    async def test_resource_refs_maintained_on_update(
        self, async_session: AsyncSession, owner_id: str
    ) -> None:
        """resource_refs should be set correctly after upsert (second import)."""
        await ensure_market_org(async_session, owner_id=owner_id)
        await async_session.flush()

        # First import
        await import_solution_templates(
            async_session, market_org_id=MARKET_ORG_ID, owner_id=owner_id
        )
        await async_session.flush()

        # Second import (upsert)
        await import_solution_templates(
            async_session, market_org_id=MARKET_ORG_ID, owner_id=owner_id
        )
        await async_session.flush()

        skills_result = await async_session.execute(
            select(Skill).where(Skill.org_id == MARKET_ORG_ID)
        )
        skills = skills_result.scalars().all()

        agents_result = await async_session.execute(
            select(Agent).where(Agent.org_id == MARKET_ORG_ID)
        )
        agent_ids = {a.id for a in agents_result.scalars().all()}

        for skill in skills:
            assert skill.resource_refs is not None, (
                f"Skill '{skill.name}' lost resource_refs after upsert"
            )
            assert len(skill.resource_refs) == 1
            ref = skill.resource_refs[0]
            assert ref["type"] == "agent"
            assert ref["id"] in agent_ids
            assert ref["alias"].startswith("@")

    @pytest.mark.asyncio
    async def test_correct_field_values(
        self, async_session: AsyncSession, owner_id: str
    ) -> None:
        """Verify that agents and skills have correct visibility, status, and publish fields."""
        await ensure_market_org(async_session, owner_id=owner_id)
        await async_session.flush()

        await import_solution_templates(
            async_session, market_org_id=MARKET_ORG_ID, owner_id=owner_id
        )
        await async_session.flush()

        # Check agents
        agents_result = await async_session.execute(
            select(Agent).where(Agent.org_id == MARKET_ORG_ID)
        )
        for agent in agents_result.scalars().all():
            assert agent.visibility == "org"
            assert agent.org_id == MARKET_ORG_ID
            assert agent.user_id == owner_id
            assert agent.is_active is True
            assert agent.status == "published"
            assert agent.publish_status == "approved"
            assert agent.published_at is not None
            assert agent.execution_mode == "auto"

        # Check skills
        skills_result = await async_session.execute(
            select(Skill).where(Skill.org_id == MARKET_ORG_ID)
        )
        for skill in skills_result.scalars().all():
            assert skill.visibility == "org"
            assert skill.org_id == MARKET_ORG_ID
            assert skill.user_id == owner_id
            assert skill.is_active is True
            assert skill.status == "published"
            assert skill.publish_status == "approved"
            assert skill.published_at is not None

    @pytest.mark.asyncio
    async def test_templates_are_in_english(
        self, async_session: AsyncSession, owner_id: str
    ) -> None:
        """Verify that all template content is in English, not Chinese."""
        await ensure_market_org(async_session, owner_id=owner_id)
        await async_session.flush()

        await import_solution_templates(
            async_session, market_org_id=MARKET_ORG_ID, owner_id=owner_id
        )
        await async_session.flush()

        agents_result = await async_session.execute(
            select(Agent).where(Agent.org_id == MARKET_ORG_ID)
        )
        for agent in agents_result.scalars().all():
            # Agent names should be in English (no CJK characters)
            assert all(ord(c) < 0x4E00 or ord(c) > 0x9FFF for c in agent.name), (
                f"Agent name '{agent.name}' contains Chinese characters"
            )

        skills_result = await async_session.execute(
            select(Skill).where(Skill.org_id == MARKET_ORG_ID)
        )
        for skill in skills_result.scalars().all():
            assert all(ord(c) < 0x4E00 or ord(c) > 0x9FFF for c in skill.name), (
                f"Skill name '{skill.name}' contains Chinese characters"
            )

    @pytest.mark.asyncio
    async def test_template_aliases_are_defined(self) -> None:
        """Every template must have a non-empty alias key."""
        for template in SOLUTION_TEMPLATES:
            assert "alias" in template, (
                f"Template for agent '{template['agent']['name']}' missing 'alias' key"
            )
            assert template["alias"], (
                f"Template for agent '{template['agent']['name']}' has empty alias"
            )
            # Alias should be a slug (lowercase, hyphens only)
            alias = template["alias"]
            assert alias == alias.lower(), f"Alias '{alias}' should be lowercase"
            assert all(c.isalnum() or c == "-" for c in alias), (
                f"Alias '{alias}' should only contain alphanumeric and hyphens"
            )
