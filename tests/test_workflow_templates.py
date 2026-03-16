"""Tests for workflow template system.

Covers:
- WorkflowTemplate ORM model construction
- Schema validation (create, update, response)
- Admin CRUD endpoint logic (create, update, delete)
- Public listing with category grouping
- Create workflow from template
- Access control (non-admin gets 403 for admin endpoints)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from fim_one.web.schemas.workflow import (
    WorkflowFromTemplateRequest,
    WorkflowTemplateCreate,
    WorkflowTemplateResponse,
    WorkflowTemplateUpdate,
)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_blueprint(
    prompt: str = "Hello {{input.text}}",
) -> dict[str, Any]:
    """Create a minimal valid workflow blueprint."""
    return {
        "nodes": [
            {
                "id": "start_1",
                "type": "custom",
                "position": {"x": 100, "y": 200},
                "data": {
                    "type": "START",
                    "label": "Start",
                    "input_schema": {
                        "variables": [
                            {
                                "name": "text",
                                "type": "string",
                                "required": True,
                            }
                        ]
                    },
                },
            },
            {
                "id": "llm_1",
                "type": "custom",
                "position": {"x": 400, "y": 200},
                "data": {
                    "type": "LLM",
                    "label": "LLM",
                    "prompt_template": prompt,
                    "model": "",
                },
            },
            {
                "id": "end_1",
                "type": "custom",
                "position": {"x": 700, "y": 200},
                "data": {
                    "type": "END",
                    "label": "End",
                    "output_mapping": {"result": "{{llm_1.output}}"},
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "start_1", "target": "llm_1"},
            {"id": "e2", "source": "llm_1", "target": "end_1"},
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


def _mock_template(
    template_id: str | None = None,
    name: str = "Test Template",
    category: str = "ai",
    is_active: bool = True,
) -> MagicMock:
    """Create a mock WorkflowTemplate ORM object."""
    tpl = MagicMock()
    tpl.id = template_id or str(uuid.uuid4())
    tpl.name = name
    tpl.description = "A test template"
    tpl.icon = "MessageSquare"
    tpl.category = category
    tpl.blueprint = _make_blueprint()
    tpl.is_active = is_active
    tpl.sort_order = 0
    tpl.created_at = datetime.now(timezone.utc)
    tpl.updated_at = None
    return tpl


def _mock_admin_user() -> MagicMock:
    """Create a mock admin user."""
    user = MagicMock()
    user.id = str(uuid.uuid4())
    user.username = "admin"
    user.email = "admin@test.com"
    user.is_admin = True
    user.is_active = True
    return user


def _mock_regular_user() -> MagicMock:
    """Create a mock regular (non-admin) user."""
    user = MagicMock()
    user.id = str(uuid.uuid4())
    user.username = "user"
    user.email = "user@test.com"
    user.is_admin = False
    user.is_active = True
    return user


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestWorkflowTemplateCreate:
    def test_valid_create(self) -> None:
        schema = WorkflowTemplateCreate(
            name="My Template",
            description="A cool template",
            icon="Bot",
            category="ai",
            blueprint=_make_blueprint(),
        )
        assert schema.name == "My Template"
        assert schema.category == "ai"
        assert schema.is_active is True
        assert schema.sort_order == 0

    def test_default_icon(self) -> None:
        schema = WorkflowTemplateCreate(
            name="Test",
            description="Desc",
            category="automation",
            blueprint=_make_blueprint(),
        )
        assert schema.icon == "\U0001f504"  # 🔄

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="String should have at least 1 character"):
            WorkflowTemplateCreate(
                name="",
                description="Desc",
                category="ai",
                blueprint=_make_blueprint(),
            )

    def test_name_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError, match="String should have at most 200 characters"):
            WorkflowTemplateCreate(
                name="x" * 201,
                description="Desc",
                category="ai",
                blueprint=_make_blueprint(),
            )

    def test_empty_description_rejected(self) -> None:
        with pytest.raises(ValidationError, match="String should have at least 1 character"):
            WorkflowTemplateCreate(
                name="Test",
                description="",
                category="ai",
                blueprint=_make_blueprint(),
            )

    def test_empty_category_rejected(self) -> None:
        with pytest.raises(ValidationError, match="String should have at least 1 character"):
            WorkflowTemplateCreate(
                name="Test",
                description="Desc",
                category="",
                blueprint=_make_blueprint(),
            )

    def test_custom_sort_order(self) -> None:
        schema = WorkflowTemplateCreate(
            name="Test",
            description="Desc",
            category="data",
            blueprint=_make_blueprint(),
            sort_order=42,
        )
        assert schema.sort_order == 42


class TestWorkflowTemplateUpdate:
    def test_all_fields_optional(self) -> None:
        schema = WorkflowTemplateUpdate()
        dump = schema.model_dump(exclude_unset=True)
        assert dump == {}

    def test_partial_update(self) -> None:
        schema = WorkflowTemplateUpdate(name="New Name", is_active=False)
        dump = schema.model_dump(exclude_unset=True)
        assert dump == {"name": "New Name", "is_active": False}

    def test_blueprint_update(self) -> None:
        bp = _make_blueprint("Updated prompt")
        schema = WorkflowTemplateUpdate(blueprint=bp)
        assert schema.blueprint is not None
        assert len(schema.blueprint["nodes"]) == 3


class TestWorkflowTemplateResponse:
    def test_response_construction(self) -> None:
        resp = WorkflowTemplateResponse(
            id="tpl-1",
            name="Test",
            description="Desc",
            icon="Bot",
            category="ai",
            blueprint=_make_blueprint(),
            created_at="2026-03-14T00:00:00",
        )
        assert resp.id == "tpl-1"
        assert resp.created_at == "2026-03-14T00:00:00"

    def test_response_without_created_at(self) -> None:
        resp = WorkflowTemplateResponse(
            id="tpl-2",
            name="Test",
            description="Desc",
            icon="Bot",
            category="ai",
            blueprint=_make_blueprint(),
        )
        assert resp.created_at is None


class TestWorkflowFromTemplateRequest:
    def test_valid_request(self) -> None:
        req = WorkflowFromTemplateRequest(template_id="tpl-1")
        assert req.template_id == "tpl-1"
        assert req.name is None

    def test_with_custom_name(self) -> None:
        req = WorkflowFromTemplateRequest(template_id="tpl-1", name="My Workflow")
        assert req.name == "My Workflow"

    def test_empty_template_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="String should have at least 1 character"):
            WorkflowFromTemplateRequest(template_id="")


# ---------------------------------------------------------------------------
# Admin CRUD endpoint tests
# ---------------------------------------------------------------------------


class TestAdminCreateTemplate:
    @pytest.mark.asyncio
    async def test_creates_template(self) -> None:
        """Admin can create a new DB-stored template."""
        from fim_one.web.api.workflow_templates import admin_create_template

        tpl_mock = _mock_template(template_id="new-tpl")
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = tpl_mock

        db = AsyncMock()
        db.commit = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        admin_user = _mock_admin_user()
        body = WorkflowTemplateCreate(
            name="New Template",
            description="A new template",
            category="ai",
            blueprint=_make_blueprint(),
        )

        with patch(
            "fim_one.web.api.workflow_templates.write_audit", new_callable=AsyncMock
        ):
            result = await admin_create_template(
                body=body, current_user=admin_user, db=db
            )

        assert result.success is True
        assert result.data["name"] == "New Template" or result.data["name"] == tpl_mock.name
        db.add.assert_called_once()
        db.commit.assert_awaited()


class TestAdminUpdateTemplate:
    @pytest.mark.asyncio
    async def test_updates_template(self) -> None:
        """Admin can update an existing DB-stored template."""
        from fim_one.web.api.workflow_templates import admin_update_template

        tpl_mock = _mock_template(template_id="tpl-1", name="Original")

        # First call returns the existing template, second returns updated
        updated_mock = _mock_template(template_id="tpl-1", name="Updated")
        mock_result_1 = MagicMock()
        mock_result_1.scalar_one_or_none.return_value = tpl_mock
        mock_result_2 = MagicMock()
        mock_result_2.scalar_one.return_value = updated_mock

        db = AsyncMock()
        db.commit = AsyncMock()
        db.execute = AsyncMock(side_effect=[mock_result_1, mock_result_2])

        admin_user = _mock_admin_user()
        body = WorkflowTemplateUpdate(name="Updated")

        with patch(
            "fim_one.web.api.workflow_templates.write_audit", new_callable=AsyncMock
        ):
            result = await admin_update_template(
                template_id="tpl-1", body=body, current_user=admin_user, db=db
            )

        assert result.success is True
        assert tpl_mock.name == "Updated"

    @pytest.mark.asyncio
    async def test_update_nonexistent_raises_404(self) -> None:
        """Updating a non-existent template raises 404."""
        from fim_one.web.api.workflow_templates import admin_update_template
        from fim_one.web.exceptions import AppError

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        admin_user = _mock_admin_user()
        body = WorkflowTemplateUpdate(name="Updated")

        with pytest.raises(AppError) as exc_info:
            await admin_update_template(
                template_id="nonexistent", body=body, current_user=admin_user, db=db
            )
        assert exc_info.value.status_code == 404


class TestAdminDeleteTemplate:
    @pytest.mark.asyncio
    async def test_deletes_template(self) -> None:
        """Admin can delete a DB-stored template."""
        from fim_one.web.api.workflow_templates import admin_delete_template

        tpl_mock = _mock_template(template_id="tpl-1")
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = tpl_mock

        db = AsyncMock()
        db.commit = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        db.delete = AsyncMock()

        admin_user = _mock_admin_user()

        with patch(
            "fim_one.web.api.workflow_templates.write_audit", new_callable=AsyncMock
        ):
            await admin_delete_template(
                template_id="tpl-1", current_user=admin_user, db=db
            )

        db.delete.assert_called_once_with(tpl_mock)
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises_404(self) -> None:
        """Deleting a non-existent template raises 404."""
        from fim_one.web.api.workflow_templates import admin_delete_template
        from fim_one.web.exceptions import AppError

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        admin_user = _mock_admin_user()

        with pytest.raises(AppError) as exc_info:
            await admin_delete_template(
                template_id="nonexistent", current_user=admin_user, db=db
            )
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Public endpoint tests
# ---------------------------------------------------------------------------


class TestListTemplates:
    @pytest.mark.asyncio
    async def test_returns_builtin_and_db_templates(self) -> None:
        """List combines built-in templates and DB templates."""
        from fim_one.web.api.workflow_templates import list_all_templates

        db_tpl = _mock_template(template_id="db-tpl-1", category="data")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [db_tpl]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        user = _mock_regular_user()

        result = await list_all_templates(current_user=user, db=db)

        assert result.success is True
        assert "templates" in result.data
        assert "by_category" in result.data
        # Should have at least built-in templates + 1 DB template
        assert len(result.data["templates"]) > 1

    @pytest.mark.asyncio
    async def test_groups_by_category(self) -> None:
        """Templates are grouped by category in the response."""
        from fim_one.web.api.workflow_templates import list_all_templates

        db_tpl_1 = _mock_template(template_id="db-1", category="custom_cat")
        db_tpl_2 = _mock_template(template_id="db-2", category="custom_cat")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [db_tpl_1, db_tpl_2]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        user = _mock_regular_user()

        result = await list_all_templates(current_user=user, db=db)

        by_category = result.data["by_category"]
        assert "custom_cat" in by_category
        assert len(by_category["custom_cat"]) == 2


class TestGetTemplateDetail:
    @pytest.mark.asyncio
    async def test_returns_db_template(self) -> None:
        """Get detail for a DB-stored template."""
        from fim_one.web.api.workflow_templates import get_template_detail

        tpl_mock = _mock_template(template_id="db-tpl-1")
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = tpl_mock

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        user = _mock_regular_user()

        result = await get_template_detail(
            template_id="db-tpl-1", current_user=user, db=db
        )

        assert result.success is True
        assert result.data["id"] == "db-tpl-1"

    @pytest.mark.asyncio
    async def test_falls_back_to_builtin(self) -> None:
        """When not in DB, falls back to built-in templates."""
        from fim_one.web.api.workflow_templates import get_template_detail

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        user = _mock_regular_user()

        # "simple-llm-chain" is a known built-in template ID
        result = await get_template_detail(
            template_id="simple-llm-chain", current_user=user, db=db
        )

        assert result.success is True
        assert result.data["id"] == "simple-llm-chain"

    @pytest.mark.asyncio
    async def test_nonexistent_raises_404(self) -> None:
        """Getting a non-existent template raises 404."""
        from fim_one.web.api.workflow_templates import get_template_detail
        from fim_one.web.exceptions import AppError

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        user = _mock_regular_user()

        with pytest.raises(AppError) as exc_info:
            await get_template_detail(
                template_id="nonexistent-id", current_user=user, db=db
            )
        assert exc_info.value.status_code == 404


class TestCreateWorkflowFromTemplate:
    @pytest.mark.asyncio
    async def test_creates_from_db_template(self) -> None:
        """Create a workflow from a DB-stored template."""
        from fim_one.web.api.workflow_templates import create_workflow_from_template

        tpl_mock = _mock_template(template_id="db-tpl-1", name="DB Template")

        # Mock for template lookup
        tpl_result = MagicMock()
        tpl_result.scalar_one_or_none.return_value = tpl_mock

        # Mock for re-fetching the created workflow
        wf_mock = MagicMock()
        wf_mock.id = str(uuid.uuid4())
        wf_mock.user_id = "user-1"
        wf_mock.name = "DB Template"
        wf_mock.icon = "MessageSquare"
        wf_mock.description = "A test template"
        wf_mock.blueprint = _make_blueprint()
        wf_mock.input_schema = None
        wf_mock.output_schema = None
        wf_mock.status = "draft"
        wf_mock.is_active = True
        wf_mock.visibility = "personal"
        wf_mock.org_id = None
        wf_mock.publish_status = None
        wf_mock.published_at = None
        wf_mock.reviewed_by = None
        wf_mock.reviewed_at = None
        wf_mock.review_note = None
        wf_mock.webhook_url = None
        wf_mock.schedule_cron = None
        wf_mock.schedule_enabled = False
        wf_mock.schedule_inputs = None
        wf_mock.schedule_timezone = "UTC"
        wf_mock.api_key = None
        wf_mock.change_summary = None
        wf_mock.created_at = datetime.now(timezone.utc)
        wf_mock.updated_at = None

        wf_result = MagicMock()
        wf_result.scalar_one.return_value = wf_mock

        db = AsyncMock()
        db.commit = AsyncMock()
        db.execute = AsyncMock(side_effect=[tpl_result, wf_result])

        user = _mock_regular_user()
        user.id = "user-1"

        body = WorkflowFromTemplateRequest(template_id="db-tpl-1")

        result = await create_workflow_from_template(
            template_id="db-tpl-1", body=body, current_user=user, db=db
        )

        assert result.success is True
        assert result.data["status"] == "draft"
        db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_with_custom_name(self) -> None:
        """Create a workflow from a template with a custom name."""
        from fim_one.web.api.workflow_templates import create_workflow_from_template

        tpl_mock = _mock_template(template_id="db-tpl-1", name="DB Template")

        tpl_result = MagicMock()
        tpl_result.scalar_one_or_none.return_value = tpl_mock

        wf_mock = MagicMock()
        wf_mock.id = str(uuid.uuid4())
        wf_mock.user_id = "user-1"
        wf_mock.name = "My Custom Name"
        wf_mock.icon = "MessageSquare"
        wf_mock.description = "A test template"
        wf_mock.blueprint = _make_blueprint()
        wf_mock.input_schema = None
        wf_mock.output_schema = None
        wf_mock.status = "draft"
        wf_mock.is_active = True
        wf_mock.visibility = "personal"
        wf_mock.org_id = None
        wf_mock.change_summary = None
        wf_mock.publish_status = None
        wf_mock.published_at = None
        wf_mock.reviewed_by = None
        wf_mock.reviewed_at = None
        wf_mock.review_note = None
        wf_mock.webhook_url = None
        wf_mock.schedule_cron = None
        wf_mock.schedule_enabled = False
        wf_mock.schedule_inputs = None
        wf_mock.schedule_timezone = "UTC"
        wf_mock.api_key = None
        wf_mock.created_at = datetime.now(timezone.utc)
        wf_mock.updated_at = None

        wf_result = MagicMock()
        wf_result.scalar_one.return_value = wf_mock

        db = AsyncMock()
        db.commit = AsyncMock()
        db.execute = AsyncMock(side_effect=[tpl_result, wf_result])

        user = _mock_regular_user()
        user.id = "user-1"

        body = WorkflowFromTemplateRequest(
            template_id="db-tpl-1", name="My Custom Name"
        )

        result = await create_workflow_from_template(
            template_id="db-tpl-1", body=body, current_user=user, db=db
        )

        assert result.success is True
        # Verify the Workflow was constructed with the custom name
        call_args = db.add.call_args[0][0]
        assert call_args.name == "My Custom Name"

    @pytest.mark.asyncio
    async def test_nonexistent_template_raises_404(self) -> None:
        """Trying to create from a non-existent template raises 404."""
        from fim_one.web.api.workflow_templates import create_workflow_from_template
        from fim_one.web.exceptions import AppError

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        user = _mock_regular_user()
        body = WorkflowFromTemplateRequest(template_id="nonexistent-id")

        with pytest.raises(AppError) as exc_info:
            await create_workflow_from_template(
                template_id="nonexistent-id", body=body, current_user=user, db=db
            )
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Access control tests
# ---------------------------------------------------------------------------


class TestAdminAccessControl:
    """Verify that admin auth dependency rejects non-admin users."""

    @pytest.mark.asyncio
    async def test_non_admin_raises_403(self) -> None:
        """get_current_admin raises 403 for a non-admin user."""
        from fastapi import HTTPException

        from fim_one.web.auth import get_current_admin

        non_admin = _mock_regular_user()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_admin(user=non_admin)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_passes(self) -> None:
        """get_current_admin returns the user for an admin."""
        from fim_one.web.auth import get_current_admin

        admin = _mock_admin_user()

        result = await get_current_admin(user=admin)
        assert result.is_admin is True


# ---------------------------------------------------------------------------
# Seed data tests
# ---------------------------------------------------------------------------


class TestTemplateSeedData:
    def test_seeds_have_required_fields(self) -> None:
        """Every seed template has all required fields."""
        from fim_one.core.workflow.template_seeds import TEMPLATE_SEEDS

        assert len(TEMPLATE_SEEDS) >= 5

        for seed in TEMPLATE_SEEDS:
            assert "name" in seed
            assert "description" in seed
            assert "icon" in seed
            assert "category" in seed
            assert "blueprint" in seed
            assert "sort_order" in seed

            bp = seed["blueprint"]
            assert "nodes" in bp
            assert "edges" in bp
            assert "viewport" in bp
            assert len(bp["nodes"]) >= 2  # At minimum start + end

    def test_seeds_have_unique_names(self) -> None:
        """All seed templates have unique names."""
        from fim_one.core.workflow.template_seeds import TEMPLATE_SEEDS

        names = [s["name"] for s in TEMPLATE_SEEDS]
        assert len(names) == len(set(names))

    def test_seed_categories_valid(self) -> None:
        """Seed categories are from known set."""
        from fim_one.core.workflow.template_seeds import TEMPLATE_SEEDS

        valid_categories = {"ai", "automation", "data", "integration", "advanced", "basic"}
        for seed in TEMPLATE_SEEDS:
            assert seed["category"] in valid_categories, (
                f"Seed '{seed['name']}' has unexpected category '{seed['category']}'"
            )

    def test_seed_blueprints_have_start_and_end(self) -> None:
        """Every seed blueprint has at least one START and one END node."""
        from fim_one.core.workflow.template_seeds import TEMPLATE_SEEDS

        for seed in TEMPLATE_SEEDS:
            nodes = seed["blueprint"]["nodes"]
            types = [
                (n.get("data", {}) or {}).get("type", "").upper() for n in nodes
            ]
            assert "START" in types, f"Seed '{seed['name']}' is missing START node"
            assert "END" in types, f"Seed '{seed['name']}' is missing END node"
