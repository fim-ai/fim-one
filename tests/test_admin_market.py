"""Tests for admin Market resource management endpoints.

Covers:
- MarketResourceInfo schema construction (including owner_username)
- list_market_resources endpoint (only published resources, owner lookup)
- delete_market_resource endpoint (with agent skill cascade)
- unpublish_market_resource endpoint
- _get_market_resource helper validation
- Invalid resource type handling
- Not-found handling
- Unsupported unpublish for mcp_server
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.web.api.admin_market import (
    MarketResourceInfo,
    _get_market_resource,
    _PUBLISHABLE_TYPES,
    _RESOURCE_MODELS,
    delete_market_resource,
    list_market_resources,
    unpublish_market_resource,
)
from fim_one.web.exceptions import AppError
from fim_one.web.platform import MARKET_ORG_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_admin_user() -> MagicMock:
    """Create a mock admin user."""
    user = MagicMock()
    user.id = str(uuid.uuid4())
    user.username = "admin"
    user.email = "admin@test.com"
    user.is_admin = True
    user.is_active = True
    return user


def _mock_resource(
    resource_id: str | None = None,
    name: str = "Test Resource",
    description: str | None = "A test resource",
    org_id: str = MARKET_ORG_ID,
    status: str | None = "published",
    publish_status: str | None = "approved",
    skill_ids: list[str] | None = None,
    user_id: str | None = None,
) -> MagicMock:
    """Create a mock ORM resource object."""
    obj = MagicMock()
    obj.id = resource_id or str(uuid.uuid4())
    obj.name = name
    obj.description = description
    obj.org_id = org_id
    obj.status = status
    obj.publish_status = publish_status
    obj.created_at = datetime.now(timezone.utc)
    obj.skill_ids = skill_ids
    obj.user_id = user_id or str(uuid.uuid4())
    return obj


def _mock_resource_no_status(
    resource_id: str | None = None,
    name: str = "MCP Server",
    user_id: str | None = None,
) -> MagicMock:
    """Create a mock resource without a status attribute (MCPServer)."""
    obj = MagicMock(spec=[
        "id", "name", "description", "org_id", "publish_status", "created_at",
        "skill_ids", "user_id",
    ])
    obj.id = resource_id or str(uuid.uuid4())
    obj.name = name
    obj.description = "An MCP server"
    obj.org_id = MARKET_ORG_ID
    obj.publish_status = None
    obj.created_at = datetime.now(timezone.utc)
    obj.user_id = user_id or str(uuid.uuid4())
    return obj


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestMarketResourceInfo:
    def test_minimal_fields(self) -> None:
        info = MarketResourceInfo(id="abc", resource_type="agent", name="My Agent")
        assert info.id == "abc"
        assert info.resource_type == "agent"
        assert info.name == "My Agent"
        assert info.description is None
        assert info.status is None
        assert info.publish_status is None
        assert info.owner_username is None
        assert info.created_at is None

    def test_all_fields(self) -> None:
        info = MarketResourceInfo(
            id="abc",
            resource_type="connector",
            name="My Connector",
            description="Does things",
            status="published",
            publish_status="approved",
            owner_username="admin",
            created_at="2026-01-01T00:00:00",
        )
        assert info.status == "published"
        assert info.publish_status == "approved"
        assert info.owner_username == "admin"
        assert info.created_at == "2026-01-01T00:00:00"


# ---------------------------------------------------------------------------
# _get_market_resource helper tests
# ---------------------------------------------------------------------------


class TestGetMarketResource:
    @pytest.mark.asyncio
    async def test_invalid_resource_type_raises_400(self) -> None:
        db = AsyncMock()
        with pytest.raises(AppError) as exc_info:
            await _get_market_resource(db, "invalid_type", "some-id")
        assert exc_info.value.status_code == 400
        assert exc_info.value.error_code == "invalid_resource_type"

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self) -> None:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute.return_value = mock_result

        with pytest.raises(AppError) as exc_info:
            await _get_market_resource(db, "agent", "nonexistent-id")
        assert exc_info.value.status_code == 404
        assert exc_info.value.error_code == "not_found"

    @pytest.mark.asyncio
    async def test_found_returns_resource(self) -> None:
        resource = _mock_resource(resource_id="res-1")
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = resource

        db = AsyncMock()
        db.execute.return_value = mock_result

        result = await _get_market_resource(db, "agent", "res-1")
        assert result.id == "res-1"


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------


class TestConstants:
    def test_resource_models_contains_all_types(self) -> None:
        expected = {"agent", "skill", "connector", "mcp_server", "workflow", "knowledge_base"}
        assert set(_RESOURCE_MODELS.keys()) == expected

    def test_mcp_server_not_publishable(self) -> None:
        assert "mcp_server" not in _PUBLISHABLE_TYPES

    def test_publishable_types(self) -> None:
        assert _PUBLISHABLE_TYPES == {"agent", "skill", "connector", "workflow", "knowledge_base"}


# ---------------------------------------------------------------------------
# list_market_resources endpoint tests
# ---------------------------------------------------------------------------


class TestListMarketResources:
    @pytest.mark.asyncio
    async def test_returns_published_resources_with_owner(self) -> None:
        """list_market_resources only returns published resources and includes owner_username."""
        owner_id = str(uuid.uuid4())
        agent = _mock_resource(resource_id="a1", name="Agent 1", user_id=owner_id)
        skill = _mock_resource(resource_id="s1", name="Skill 1", user_id=owner_id)

        # Mock user for username lookup
        mock_user = MagicMock()
        mock_user.id = owner_id
        mock_user.username = "testadmin"

        # 6 resource queries + 1 user query = 7 calls
        call_count = 0
        results_by_call = [
            [agent],      # agent (published)
            [skill],      # skill (published)
            [],           # connector
            [],           # mcp_server
            [],           # workflow
            [],           # knowledge_base
            [mock_user],  # user lookup
        ]

        async def mock_execute(stmt: object) -> MagicMock:
            nonlocal call_count
            idx = call_count
            call_count += 1
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = (
                results_by_call[idx] if idx < len(results_by_call) else []
            )
            return mock_result

        db = AsyncMock()
        db.execute.side_effect = mock_execute

        admin_user = _mock_admin_user()

        result = await list_market_resources(resource_type=None, db=db, admin=admin_user)

        assert result["total"] == 2
        items = result["items"]
        assert len(items) == 2
        names = [item["name"] for item in items]
        assert "Agent 1" in names
        assert "Skill 1" in names
        # Check owner_username is populated
        for item in items:
            assert item["owner_username"] == "testadmin"

    @pytest.mark.asyncio
    async def test_empty_market_returns_zero(self) -> None:
        """When the Market org has no published resources, returns empty list."""
        async def mock_execute(stmt: object) -> MagicMock:
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []
            return mock_result

        db = AsyncMock()
        db.execute.side_effect = mock_execute

        admin_user = _mock_admin_user()

        result = await list_market_resources(resource_type=None, db=db, admin=admin_user)

        assert result["total"] == 0
        assert result["items"] == []

    @pytest.mark.asyncio
    async def test_resource_type_filter(self) -> None:
        """When resource_type is provided, only that type is queried."""
        owner_id = str(uuid.uuid4())
        connector = _mock_resource(resource_id="c1", name="My Connector", user_id=owner_id)

        mock_user = MagicMock()
        mock_user.id = owner_id
        mock_user.username = "owner1"

        call_count = 0
        # Only 1 resource query (connector) + 1 user query
        results_by_call = [
            [connector],   # connector query
            [mock_user],   # user lookup
        ]

        async def mock_execute(stmt: object) -> MagicMock:
            nonlocal call_count
            idx = call_count
            call_count += 1
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = (
                results_by_call[idx] if idx < len(results_by_call) else []
            )
            return mock_result

        db = AsyncMock()
        db.execute.side_effect = mock_execute

        admin_user = _mock_admin_user()

        result = await list_market_resources(
            resource_type="connector", db=db, admin=admin_user,
        )

        assert result["total"] == 1
        item = result["items"][0]
        assert item["resource_type"] == "connector"
        assert item["name"] == "My Connector"
        assert item["owner_username"] == "owner1"
        # Only 2 db calls (1 resource + 1 user), not 6+1
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_invalid_resource_type_filter_queries_all(self) -> None:
        """An invalid resource_type filter falls through to query all types."""
        async def mock_execute(stmt: object) -> MagicMock:
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []
            return mock_result

        db = AsyncMock()
        db.execute.side_effect = mock_execute

        admin_user = _mock_admin_user()

        result = await list_market_resources(
            resource_type="nonexistent_type", db=db, admin=admin_user,
        )

        assert result["total"] == 0
        # Should have queried all 6 types
        assert db.execute.call_count == 6


# ---------------------------------------------------------------------------
# delete_market_resource endpoint tests
# ---------------------------------------------------------------------------


class TestDeleteMarketResource:
    @pytest.mark.asyncio
    async def test_deletes_simple_resource(self) -> None:
        """Deleting a non-agent resource removes it and returns ok."""
        resource = _mock_resource(resource_id="c1", name="Connector X")

        with (
            patch(
                "fim_one.web.api.admin_market._get_market_resource",
                new_callable=AsyncMock,
                return_value=resource,
            ),
            patch(
                "fim_one.web.api.admin_market.write_audit",
                new_callable=AsyncMock,
            ),
        ):
            db = AsyncMock()
            db.commit = AsyncMock()
            admin_user = _mock_admin_user()

            result = await delete_market_resource(
                resource_type="connector",
                resource_id="c1",
                db=db,
                admin=admin_user,
            )

        assert result == {"ok": True}
        db.delete.assert_called_once_with(resource)
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deletes_agent_with_linked_skills(self) -> None:
        """Deleting an agent also deletes its linked skills."""
        skill1 = _mock_resource(resource_id="sk1", name="Skill 1")
        skill2 = _mock_resource(resource_id="sk2", name="Skill 2")
        agent = _mock_resource(
            resource_id="a1",
            name="Agent A",
            skill_ids=["sk1", "sk2"],
        )

        skill_result = MagicMock()
        skill_result.scalars.return_value.all.return_value = [skill1, skill2]

        with (
            patch(
                "fim_one.web.api.admin_market._get_market_resource",
                new_callable=AsyncMock,
                return_value=agent,
            ),
            patch(
                "fim_one.web.api.admin_market.write_audit",
                new_callable=AsyncMock,
            ),
        ):
            db = AsyncMock()
            db.execute.return_value = skill_result
            db.commit = AsyncMock()
            admin_user = _mock_admin_user()

            result = await delete_market_resource(
                resource_type="agent",
                resource_id="a1",
                db=db,
                admin=admin_user,
            )

        assert result == {"ok": True}
        # Should delete 2 skills + 1 agent = 3 delete calls
        assert db.delete.call_count == 3

    @pytest.mark.asyncio
    async def test_deletes_agent_without_skills(self) -> None:
        """Deleting an agent with no skill_ids only deletes the agent."""
        agent = _mock_resource(
            resource_id="a1",
            name="Agent No Skills",
            skill_ids=[],
        )

        with (
            patch(
                "fim_one.web.api.admin_market._get_market_resource",
                new_callable=AsyncMock,
                return_value=agent,
            ),
            patch(
                "fim_one.web.api.admin_market.write_audit",
                new_callable=AsyncMock,
            ),
        ):
            db = AsyncMock()
            db.commit = AsyncMock()
            admin_user = _mock_admin_user()

            result = await delete_market_resource(
                resource_type="agent",
                resource_id="a1",
                db=db,
                admin=admin_user,
            )

        assert result == {"ok": True}
        db.delete.assert_called_once_with(agent)

    @pytest.mark.asyncio
    async def test_invalid_type_raises_400(self) -> None:
        """Deleting with an invalid resource_type raises 400 via the helper."""
        db = AsyncMock()
        admin_user = _mock_admin_user()

        with pytest.raises(AppError) as exc_info:
            await delete_market_resource(
                resource_type="invalid",
                resource_id="some-id",
                db=db,
                admin=admin_user,
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self) -> None:
        """Deleting a nonexistent resource raises 404 via the helper."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute.return_value = mock_result
        admin_user = _mock_admin_user()

        with pytest.raises(AppError) as exc_info:
            await delete_market_resource(
                resource_type="agent",
                resource_id="nonexistent",
                db=db,
                admin=admin_user,
            )
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# unpublish_market_resource endpoint tests
# ---------------------------------------------------------------------------


class TestUnpublishMarketResource:
    @pytest.mark.asyncio
    async def test_unpublishes_resource(self) -> None:
        """Unpublishing sets status to draft."""
        resource = _mock_resource(resource_id="a1", name="Agent A", status="published")

        with (
            patch(
                "fim_one.web.api.admin_market._get_market_resource",
                new_callable=AsyncMock,
                return_value=resource,
            ),
            patch(
                "fim_one.web.api.admin_market.write_audit",
                new_callable=AsyncMock,
            ),
        ):
            db = AsyncMock()
            db.commit = AsyncMock()
            admin_user = _mock_admin_user()

            result = await unpublish_market_resource(
                resource_type="agent",
                resource_id="a1",
                db=db,
                admin=admin_user,
            )

        assert result == {"ok": True, "status": "draft"}
        assert resource.status == "draft"
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mcp_server_not_supported(self) -> None:
        """Unpublishing an mcp_server raises 400."""
        db = AsyncMock()
        admin_user = _mock_admin_user()

        with pytest.raises(AppError) as exc_info:
            await unpublish_market_resource(
                resource_type="mcp_server",
                resource_id="m1",
                db=db,
                admin=admin_user,
            )
        assert exc_info.value.status_code == 400
        assert exc_info.value.error_code == "unpublish_not_supported"

    @pytest.mark.asyncio
    async def test_invalid_type_raises_400(self) -> None:
        """Unpublishing an invalid resource type raises 400."""
        db = AsyncMock()
        admin_user = _mock_admin_user()

        with pytest.raises(AppError) as exc_info:
            await unpublish_market_resource(
                resource_type="bogus",
                resource_id="x1",
                db=db,
                admin=admin_user,
            )
        assert exc_info.value.status_code == 400
