"""Tests for admin workflow batch operations.

Covers:
- BatchWorkflowDeleteRequest / BatchWorkflowToggleRequest / BatchWorkflowPublishRequest validation
- BatchOperationResponse construction
- Endpoint logic for batch-delete, batch-toggle, batch-publish
- Access control (non-admin gets 403)
- Edge cases (empty list rejected, mix of valid/invalid IDs)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from fim_one.web.api.admin_workflows import (
    BatchOperationResponse,
    BatchWorkflowDeleteRequest,
    BatchWorkflowPublishRequest,
    BatchWorkflowToggleRequest,
    batch_delete_workflows,
    batch_publish_workflows,
    batch_toggle_workflows,
)


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestBatchWorkflowDeleteRequest:
    def test_valid_request(self) -> None:
        req = BatchWorkflowDeleteRequest(workflow_ids=["id1", "id2", "id3"])
        assert len(req.workflow_ids) == 3

    def test_single_id(self) -> None:
        req = BatchWorkflowDeleteRequest(workflow_ids=["id1"])
        assert len(req.workflow_ids) == 1

    def test_empty_list_rejected(self) -> None:
        with pytest.raises(ValidationError, match="too_short"):
            BatchWorkflowDeleteRequest(workflow_ids=[])

    def test_over_100_rejected(self) -> None:
        ids = [f"id-{i}" for i in range(101)]
        with pytest.raises(ValidationError, match="too_long"):
            BatchWorkflowDeleteRequest(workflow_ids=ids)

    def test_exactly_100_accepted(self) -> None:
        ids = [f"id-{i}" for i in range(100)]
        req = BatchWorkflowDeleteRequest(workflow_ids=ids)
        assert len(req.workflow_ids) == 100


class TestBatchWorkflowToggleRequest:
    def test_valid_enable(self) -> None:
        req = BatchWorkflowToggleRequest(
            workflow_ids=["id1", "id2"], is_active=True
        )
        assert req.is_active is True

    def test_valid_disable(self) -> None:
        req = BatchWorkflowToggleRequest(
            workflow_ids=["id1"], is_active=False
        )
        assert req.is_active is False

    def test_empty_list_rejected(self) -> None:
        with pytest.raises(ValidationError, match="too_short"):
            BatchWorkflowToggleRequest(workflow_ids=[], is_active=True)

    def test_missing_is_active_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BatchWorkflowToggleRequest(workflow_ids=["id1"])  # type: ignore[call-arg]


class TestBatchWorkflowPublishRequest:
    def test_valid_active(self) -> None:
        req = BatchWorkflowPublishRequest(
            workflow_ids=["id1", "id2"], status="active"
        )
        assert req.status == "active"

    def test_valid_draft(self) -> None:
        req = BatchWorkflowPublishRequest(
            workflow_ids=["id1"], status="draft"
        )
        assert req.status == "draft"

    def test_any_status_accepted(self) -> None:
        """Status field is a free-form string (active/draft); no enum constraint."""
        req = BatchWorkflowPublishRequest(
            workflow_ids=["id1"], status="custom_status"
        )
        assert req.status == "custom_status"

    def test_empty_list_rejected(self) -> None:
        with pytest.raises(ValidationError, match="too_short"):
            BatchWorkflowPublishRequest(workflow_ids=[], status="active")


class TestBatchOperationResponse:
    def test_construction(self) -> None:
        resp = BatchOperationResponse(count=5, message="Deleted 5 workflow(s)")
        assert resp.count == 5
        assert "5" in resp.message

    def test_zero_count(self) -> None:
        resp = BatchOperationResponse(count=0, message="Deleted 0 workflow(s)")
        assert resp.count == 0


# ---------------------------------------------------------------------------
# Helper: mock workflow objects
# ---------------------------------------------------------------------------


def _mock_workflow(
    workflow_id: str | None = None,
    name: str = "Test Workflow",
    is_active: bool = True,
    status: str = "draft",
) -> MagicMock:
    """Create a mock Workflow ORM object."""
    wf = MagicMock()
    wf.id = workflow_id or str(uuid.uuid4())
    wf.name = name
    wf.is_active = is_active
    wf.status = status
    wf.created_at = datetime.now(timezone.utc)
    wf.updated_at = datetime.now(timezone.utc)
    return wf


def _mock_admin_user() -> MagicMock:
    """Create a mock admin user."""
    user = MagicMock()
    user.id = str(uuid.uuid4())
    user.username = "admin"
    user.email = "admin@test.com"
    user.is_admin = True
    user.is_active = True
    return user


# ---------------------------------------------------------------------------
# Endpoint logic tests
# ---------------------------------------------------------------------------


class TestBatchDeleteEndpoint:
    @pytest.mark.asyncio
    async def test_deletes_existing_workflows(self) -> None:
        """Batch delete with valid IDs deletes all found workflows."""
        wf1 = _mock_workflow(workflow_id="wf-1", name="Workflow 1")
        wf2 = _mock_workflow(workflow_id="wf-2", name="Workflow 2")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [wf1, wf2]

        db = AsyncMock()
        db.execute.return_value = mock_result
        db.commit = AsyncMock()

        admin_user = _mock_admin_user()
        body = BatchWorkflowDeleteRequest(workflow_ids=["wf-1", "wf-2"])

        with patch(
            "fim_one.web.api.admin_workflows.write_audit", new_callable=AsyncMock
        ):
            result = await batch_delete_workflows(
                body=body, current_user=admin_user, db=db
            )

        assert result.count == 2
        assert "2" in result.message
        assert db.delete.call_count == 2
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mixed_valid_invalid_ids(self) -> None:
        """Batch delete with mix of valid/invalid IDs deletes only valid ones."""
        wf1 = _mock_workflow(workflow_id="wf-1", name="Workflow 1")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [wf1]

        db = AsyncMock()
        db.execute.return_value = mock_result
        db.commit = AsyncMock()

        admin_user = _mock_admin_user()
        body = BatchWorkflowDeleteRequest(
            workflow_ids=["wf-1", "nonexistent-id"]
        )

        with patch(
            "fim_one.web.api.admin_workflows.write_audit", new_callable=AsyncMock
        ):
            result = await batch_delete_workflows(
                body=body, current_user=admin_user, db=db
            )

        assert result.count == 1
        assert db.delete.call_count == 1

    @pytest.mark.asyncio
    async def test_no_matching_ids(self) -> None:
        """Batch delete with no matching IDs returns count=0."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute.return_value = mock_result
        db.commit = AsyncMock()

        admin_user = _mock_admin_user()
        body = BatchWorkflowDeleteRequest(workflow_ids=["nonexistent"])

        with patch(
            "fim_one.web.api.admin_workflows.write_audit", new_callable=AsyncMock
        ):
            result = await batch_delete_workflows(
                body=body, current_user=admin_user, db=db
            )

        assert result.count == 0
        assert db.delete.call_count == 0


class TestBatchToggleEndpoint:
    @pytest.mark.asyncio
    async def test_enable_workflows(self) -> None:
        """Batch toggle sets is_active=True on all found workflows."""
        wf1 = _mock_workflow(workflow_id="wf-1", is_active=False)
        wf2 = _mock_workflow(workflow_id="wf-2", is_active=False)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [wf1, wf2]

        db = AsyncMock()
        db.execute.return_value = mock_result
        db.commit = AsyncMock()

        admin_user = _mock_admin_user()
        body = BatchWorkflowToggleRequest(
            workflow_ids=["wf-1", "wf-2"], is_active=True
        )

        with patch(
            "fim_one.web.api.admin_workflows.write_audit", new_callable=AsyncMock
        ):
            result = await batch_toggle_workflows(
                body=body, current_user=admin_user, db=db
            )

        assert result.count == 2
        assert wf1.is_active is True
        assert wf2.is_active is True

    @pytest.mark.asyncio
    async def test_disable_workflows(self) -> None:
        """Batch toggle sets is_active=False on all found workflows."""
        wf1 = _mock_workflow(workflow_id="wf-1", is_active=True)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [wf1]

        db = AsyncMock()
        db.execute.return_value = mock_result
        db.commit = AsyncMock()

        admin_user = _mock_admin_user()
        body = BatchWorkflowToggleRequest(
            workflow_ids=["wf-1"], is_active=False
        )

        with patch(
            "fim_one.web.api.admin_workflows.write_audit", new_callable=AsyncMock
        ):
            result = await batch_toggle_workflows(
                body=body, current_user=admin_user, db=db
            )

        assert result.count == 1
        assert wf1.is_active is False

    @pytest.mark.asyncio
    async def test_no_matching_ids(self) -> None:
        """Batch toggle with no matching IDs returns count=0."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute.return_value = mock_result
        db.commit = AsyncMock()

        admin_user = _mock_admin_user()
        body = BatchWorkflowToggleRequest(
            workflow_ids=["nonexistent"], is_active=True
        )

        with patch(
            "fim_one.web.api.admin_workflows.write_audit", new_callable=AsyncMock
        ):
            result = await batch_toggle_workflows(
                body=body, current_user=admin_user, db=db
            )

        assert result.count == 0


class TestBatchPublishEndpoint:
    @pytest.mark.asyncio
    async def test_publish_workflows(self) -> None:
        """Batch publish sets status=active on all found workflows."""
        wf1 = _mock_workflow(workflow_id="wf-1", status="draft")
        wf2 = _mock_workflow(workflow_id="wf-2", status="draft")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [wf1, wf2]

        db = AsyncMock()
        db.execute.return_value = mock_result
        db.commit = AsyncMock()

        admin_user = _mock_admin_user()
        body = BatchWorkflowPublishRequest(
            workflow_ids=["wf-1", "wf-2"], status="active"
        )

        with patch(
            "fim_one.web.api.admin_workflows.write_audit", new_callable=AsyncMock
        ):
            result = await batch_publish_workflows(
                body=body, current_user=admin_user, db=db
            )

        assert result.count == 2
        assert wf1.status == "active"
        assert wf2.status == "active"

    @pytest.mark.asyncio
    async def test_unpublish_workflows(self) -> None:
        """Batch publish sets status=draft on all found workflows."""
        wf1 = _mock_workflow(workflow_id="wf-1", status="active")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [wf1]

        db = AsyncMock()
        db.execute.return_value = mock_result
        db.commit = AsyncMock()

        admin_user = _mock_admin_user()
        body = BatchWorkflowPublishRequest(
            workflow_ids=["wf-1"], status="draft"
        )

        with patch(
            "fim_one.web.api.admin_workflows.write_audit", new_callable=AsyncMock
        ):
            result = await batch_publish_workflows(
                body=body, current_user=admin_user, db=db
            )

        assert result.count == 1
        assert wf1.status == "draft"

    @pytest.mark.asyncio
    async def test_no_matching_ids(self) -> None:
        """Batch publish with no matching IDs returns count=0."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute.return_value = mock_result
        db.commit = AsyncMock()

        admin_user = _mock_admin_user()
        body = BatchWorkflowPublishRequest(
            workflow_ids=["nonexistent"], status="active"
        )

        with patch(
            "fim_one.web.api.admin_workflows.write_audit", new_callable=AsyncMock
        ):
            result = await batch_publish_workflows(
                body=body, current_user=admin_user, db=db
            )

        assert result.count == 0


# ---------------------------------------------------------------------------
# Access control tests
# ---------------------------------------------------------------------------


class TestAdminAccessControl:
    """Test that admin auth dependency rejects non-admin users.

    Since ``get_current_admin`` is a FastAPI dependency that raises 403 for
    non-admin users, we verify the dependency itself works correctly.
    """

    @pytest.mark.asyncio
    async def test_non_admin_raises_403(self) -> None:
        """get_current_admin raises 403 for a non-admin user."""
        from fastapi import HTTPException

        from fim_one.web.auth import get_current_admin

        non_admin = MagicMock()
        non_admin.is_admin = False
        non_admin.is_active = True

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
