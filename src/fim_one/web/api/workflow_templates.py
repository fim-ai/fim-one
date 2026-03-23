"""Workflow template endpoints — public listing and admin CRUD.

Public endpoints serve both built-in (hardcoded) templates and DB-stored
templates in a unified list, grouped by category.  Admin endpoints manage
the DB-stored templates only.
"""

from __future__ import annotations

import copy
import logging
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_admin, get_current_user
from fim_one.web.exceptions import AppError
from fim_one.web.models import User, Workflow, WorkflowTemplate
from fim_one.web.schemas.common import ApiResponse
from fim_one.web.schemas.workflow import (
    WorkflowFromTemplateRequest,
    WorkflowResponse,
    WorkflowTemplateCreate,
    WorkflowTemplateResponse,
    WorkflowTemplateUpdate,
)

from fim_one.web.api.admin_utils import write_audit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public router — accessible by any authenticated user
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/workflow-templates", tags=["workflow-templates"])

# ---------------------------------------------------------------------------
# Admin router — requires admin privileges
# ---------------------------------------------------------------------------

admin_router = APIRouter(prefix="/api/admin/workflow-templates", tags=["admin"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _template_to_response(tpl: WorkflowTemplate) -> WorkflowTemplateResponse:
    """Convert a DB WorkflowTemplate ORM object to a response schema."""
    return WorkflowTemplateResponse(
        id=tpl.id,
        name=tpl.name,
        description=tpl.description,
        icon=tpl.icon,
        category=tpl.category,
        blueprint=tpl.blueprint or {"nodes": [], "edges": [], "viewport": {}},
        created_at=tpl.created_at.isoformat() if tpl.created_at else None,
    )


def _builtin_to_response(t: dict[str, Any]) -> WorkflowTemplateResponse:
    """Convert a built-in template dict to a response schema."""
    return WorkflowTemplateResponse(
        id=t["id"],
        name=t["name"],
        description=t["description"],
        icon=t.get("icon", "🔄"),
        category=t.get("category", "basic"),
        blueprint=t["blueprint"],
        created_at=None,
    )


def _extract_schemas_from_blueprint(
    blueprint: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Extract input/output schemas from Start and End nodes in the blueprint."""
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None

    nodes = blueprint.get("nodes", [])
    for node in nodes:
        node_type = (node.get("data", {}) or {}).get("type", "") or node.get("type", "")
        node_data = node.get("data", {}) or {}

        if node_type.upper() == "START":
            variables = node_data.get("variables") or (
                node_data.get("input_schema", {}) or {}
            ).get("variables")
            if variables:
                input_schema = {"variables": variables}
        elif node_type.upper() == "END":
            out = node_data.get("output_schema") or node_data.get("output_mapping")
            if out:
                output_schema = out if isinstance(out, dict) else {"variables": out}

    return input_schema, output_schema


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse)
async def list_all_templates(
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """List all active templates (built-in + DB-stored), grouped by category."""
    from fim_one.core.workflow.templates import list_templates as list_builtin

    # 1. Collect built-in templates
    builtin = list_builtin()
    items: list[dict[str, Any]] = [
        _builtin_to_response(t).model_dump() for t in builtin
    ]

    # 2. Collect DB-stored active templates
    result = await db.execute(
        select(WorkflowTemplate)
        .where(WorkflowTemplate.is_active == True)  # noqa: E712
        .order_by(WorkflowTemplate.sort_order.asc(), WorkflowTemplate.created_at.asc())
    )
    db_templates = result.scalars().all()
    for tpl in db_templates:
        items.append(_template_to_response(tpl).model_dump())

    # 3. Group by category
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        grouped[item["category"]].append(item)

    return ApiResponse(data={"templates": items, "by_category": dict(grouped)})


@router.get("/{template_id}", response_model=ApiResponse)
async def get_template_detail(
    template_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Get a single template by ID (checks DB first, then built-in)."""
    # Check DB first
    result = await db.execute(
        select(WorkflowTemplate).where(WorkflowTemplate.id == template_id)
    )
    db_tpl = result.scalar_one_or_none()
    if db_tpl is not None:
        return ApiResponse(data=_template_to_response(db_tpl).model_dump())

    # Fallback to built-in
    from fim_one.core.workflow.templates import get_template as get_builtin

    builtin = get_builtin(template_id)
    if builtin is not None:
        return ApiResponse(data=_builtin_to_response(builtin).model_dump())

    raise AppError("template_not_found", status_code=404)


@router.post("/{template_id}/create-workflow", response_model=ApiResponse)
async def create_workflow_from_template(
    template_id: str,
    body: WorkflowFromTemplateRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Create a new workflow by cloning a template's blueprint."""
    blueprint: dict[str, Any] | None = None
    template_name: str = ""
    template_icon: str | None = None
    template_desc: str | None = None

    # Check DB first
    result = await db.execute(
        select(WorkflowTemplate).where(WorkflowTemplate.id == template_id)
    )
    db_tpl = result.scalar_one_or_none()
    if db_tpl is not None:
        blueprint = copy.deepcopy(db_tpl.blueprint)
        template_name = db_tpl.name
        template_icon = db_tpl.icon
        template_desc = db_tpl.description
    else:
        # Fallback to built-in
        from fim_one.core.workflow.templates import get_template as get_builtin

        builtin = get_builtin(template_id)
        if builtin is not None:
            blueprint = builtin["blueprint"]
            template_name = builtin["name"]
            template_icon = builtin.get("icon")
            template_desc = builtin.get("description")

    if blueprint is None:
        raise AppError("template_not_found", status_code=404)

    input_schema, output_schema = _extract_schemas_from_blueprint(blueprint)

    wf = Workflow(
        user_id=current_user.id,
        name=body.name or template_name,
        icon=None,  # template icon is a Lucide name for gallery, not an emoji
        description=template_desc,
        blueprint=blueprint,
        input_schema=input_schema,
        output_schema=output_schema,
        status="draft",
        is_active=True,
    )
    db.add(wf)
    await db.commit()

    # Re-fetch to get server-generated fields
    wf_result = await db.execute(select(Workflow).where(Workflow.id == wf.id))
    wf_reloaded = wf_result.scalar_one()

    # Use the response builder from workflows module
    from fim_one.web.api.workflows import _workflow_to_response

    return ApiResponse(data=_workflow_to_response(wf_reloaded).model_dump())


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


@admin_router.post("", response_model=ApiResponse, status_code=201)
async def admin_create_template(
    body: WorkflowTemplateCreate,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Create a new DB-stored workflow template (admin only)."""
    tpl = WorkflowTemplate(
        name=body.name,
        description=body.description,
        icon=body.icon,
        category=body.category,
        blueprint=body.blueprint,
        is_active=body.is_active,
        sort_order=body.sort_order,
    )
    db.add(tpl)
    await db.commit()

    result = await db.execute(
        select(WorkflowTemplate).where(WorkflowTemplate.id == tpl.id)
    )
    tpl = result.scalar_one()

    await write_audit(
        db,
        current_user,
        "workflow_template.create",
        target_type="workflow_template",
        target_id=tpl.id,
        target_label=tpl.name,
    )

    return ApiResponse(data=_template_to_response(tpl).model_dump())


@admin_router.put("/{template_id}", response_model=ApiResponse)
async def admin_update_template(
    template_id: str,
    body: WorkflowTemplateUpdate,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Update a DB-stored workflow template (admin only)."""
    result = await db.execute(
        select(WorkflowTemplate).where(WorkflowTemplate.id == template_id)
    )
    tpl = result.scalar_one_or_none()
    if tpl is None:
        raise AppError("template_not_found", status_code=404)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(tpl, field, value)

    await db.commit()

    result = await db.execute(
        select(WorkflowTemplate).where(WorkflowTemplate.id == template_id)
    )
    tpl = result.scalar_one()

    await write_audit(
        db,
        current_user,
        "workflow_template.update",
        target_type="workflow_template",
        target_id=tpl.id,
        target_label=tpl.name,
        detail=f"Updated fields: {', '.join(update_data.keys())}",
    )

    return ApiResponse(data=_template_to_response(tpl).model_dump())


@admin_router.delete("/{template_id}", status_code=204)
async def admin_delete_template(
    template_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Delete a DB-stored workflow template (admin only)."""
    result = await db.execute(
        select(WorkflowTemplate).where(WorkflowTemplate.id == template_id)
    )
    tpl = result.scalar_one_or_none()
    if tpl is None:
        raise AppError("template_not_found", status_code=404)

    tpl_name = tpl.name
    await db.delete(tpl)
    await db.commit()

    await write_audit(
        db,
        current_user,
        "workflow_template.delete",
        target_type="workflow_template",
        target_id=template_id,
        target_label=tpl_name,
    )
