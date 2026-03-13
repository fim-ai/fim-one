"""Workflow CRUD endpoints with SSE execution streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session, create_session
from fim_one.web.exceptions import AppError
from fim_one.web.auth import get_current_user, get_user_org_ids
from fim_one.web.models import User, Workflow, WorkflowRun, WorkflowVersion
from fim_one.web.schemas.common import ApiResponse, PaginatedResponse, PublishRequest
from fim_one.web.schemas.workflow import (
    BlueprintWarningItem,
    DryRunNodePlan,
    WorkflowCreate,
    WorkflowDryRunResponse,
    WorkflowEnvVarsUpdate,
    WorkflowExportData,
    WorkflowExportFile,
    WorkflowFromTemplateRequest,
    WorkflowImportFileRequest,
    WorkflowResponse,
    WorkflowRunRequest,
    WorkflowRunResponse,
    WorkflowTemplateResponse,
    WorkflowUpdate,
    WorkflowValidateResponse,
    WorkflowVersionResponse,
)
from fim_one.web.visibility import build_visibility_filter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sse(event: str, data: Any) -> str:
    """Format a Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _workflow_to_response(wf: Workflow) -> WorkflowResponse:
    return WorkflowResponse(
        id=wf.id,
        user_id=wf.user_id,
        name=wf.name,
        icon=wf.icon,
        description=wf.description,
        blueprint=wf.blueprint or {"nodes": [], "edges": [], "viewport": {}},
        input_schema=wf.input_schema,
        output_schema=wf.output_schema,
        status=wf.status,
        is_active=wf.is_active,
        visibility=getattr(wf, "visibility", "personal"),
        org_id=getattr(wf, "org_id", None),
        publish_status=getattr(wf, "publish_status", None),
        published_at=(
            wf.published_at.isoformat() if getattr(wf, "published_at", None) else None
        ),
        reviewed_by=getattr(wf, "reviewed_by", None),
        reviewed_at=(
            wf.reviewed_at.isoformat()
            if getattr(wf, "reviewed_at", None)
            else None
        ),
        review_note=getattr(wf, "review_note", None),
        created_at=wf.created_at.isoformat() if wf.created_at else "",
        updated_at=wf.updated_at.isoformat() if wf.updated_at else None,
    )


def _run_to_response(run: WorkflowRun) -> WorkflowRunResponse:
    return WorkflowRunResponse(
        id=run.id,
        workflow_id=run.workflow_id,
        user_id=run.user_id,
        status=run.status,
        inputs=run.inputs,
        outputs=run.outputs,
        node_results=run.node_results,
        started_at=run.started_at.isoformat() if run.started_at else None,
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        duration_ms=run.duration_ms,
        error=run.error,
        created_at=run.created_at.isoformat() if run.created_at else "",
        updated_at=run.updated_at.isoformat() if run.updated_at else None,
    )


def _version_to_response(v: WorkflowVersion) -> WorkflowVersionResponse:
    return WorkflowVersionResponse(
        id=v.id,
        workflow_id=v.workflow_id,
        version_number=v.version_number,
        blueprint=v.blueprint or {},
        input_schema=v.input_schema,
        output_schema=v.output_schema,
        change_summary=v.change_summary,
        created_by=v.created_by,
        created_at=v.created_at.isoformat() if v.created_at else "",
    )


async def _get_owned_workflow(
    workflow_id: str,
    user_id: str,
    db: AsyncSession,
) -> Workflow:
    """Fetch a workflow that the user owns."""
    result = await db.execute(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.user_id == user_id)
    )
    wf = result.scalar_one_or_none()
    if wf is None:
        raise AppError("workflow_not_found", status_code=404)
    return wf


async def _get_accessible_workflow(
    workflow_id: str,
    user_id: str,
    db: AsyncSession,
) -> Workflow:
    """Fetch a workflow the user owns OR a published org workflow (read-only)."""
    user_org_ids = await get_user_org_ids(user_id, db)
    result = await db.execute(
        select(Workflow).where(
            Workflow.id == workflow_id,
            build_visibility_filter(Workflow, user_id, user_org_ids),
        )
    )
    wf = result.scalar_one_or_none()
    if wf is None:
        raise AppError("workflow_not_found", status_code=404)
    return wf


def _extract_schemas_from_blueprint(
    blueprint: dict,
) -> tuple[dict | None, dict | None]:
    """Extract input/output schemas from Start and End nodes in the blueprint.

    The frontend stores Start node inputs as ``data.variables``:
    ``[{name, type, default_value, required}]``.  We convert this to a
    JSON Schema dict for the ``input_schema`` column.

    Returns (input_schema, output_schema).
    """
    input_schema: dict | None = None
    output_schema: dict | None = None

    nodes = blueprint.get("nodes", [])
    for node in nodes:
        node_type = (node.get("data", {}) or {}).get("type", "") or node.get("type", "")
        node_data = node.get("data", {}) or {}

        if node_type.upper() == "START":
            # First check for explicit input_schema (legacy)
            input_schema = node_data.get("input_schema") or node_data.get("schema")
            # Convert variables array to JSON Schema if no explicit schema
            if not input_schema:
                variables = node_data.get("variables", [])
                if variables:
                    properties: dict[str, dict] = {}
                    required: list[str] = []
                    for var in variables:
                        name = var.get("name", "")
                        if not name:
                            continue
                        properties[name] = {
                            "type": var.get("type", "string"),
                        }
                        if var.get("default_value"):
                            properties[name]["default"] = var["default_value"]
                        if var.get("required"):
                            required.append(name)
                    input_schema = {
                        "type": "object",
                        "properties": properties,
                    }
                    if required:
                        input_schema["required"] = required

        elif node_type.upper() == "END":
            output_schema = node_data.get("output_schema") or node_data.get("schema")
            # Convert output_mapping to a schema
            if not output_schema:
                output_mapping = node_data.get("output_mapping", {})
                if output_mapping:
                    output_schema = {
                        "type": "object",
                        "properties": {
                            key: {"type": "string"} for key in output_mapping
                        },
                    }

    return input_schema, output_schema


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=ApiResponse)
async def create_workflow(
    body: WorkflowCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    input_schema, output_schema = _extract_schemas_from_blueprint(body.blueprint)
    wf = Workflow(
        user_id=current_user.id,
        name=body.name,
        icon=body.icon,
        description=body.description,
        blueprint=body.blueprint,
        input_schema=input_schema,
        output_schema=output_schema,
        status=body.status,
        is_active=body.is_active,
    )
    db.add(wf)
    await db.commit()
    result = await db.execute(select(Workflow).where(Workflow.id == wf.id))
    wf = result.scalar_one()
    return ApiResponse(data=_workflow_to_response(wf).model_dump())


@router.get("", response_model=PaginatedResponse)
async def list_workflows(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    workflow_status: str | None = Query(None, alias="status"),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    user_org_ids = await get_user_org_ids(current_user.id, db)

    base = select(Workflow).where(
        build_visibility_filter(Workflow, current_user.id, user_org_ids),
    )
    if workflow_status is not None:
        base = base.where(Workflow.status == workflow_status)

    count_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(
        base.order_by(Workflow.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    workflows = result.scalars().all()

    return PaginatedResponse(
        items=[_workflow_to_response(w).model_dump() for w in workflows],
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total else 0,
    )


# ---------------------------------------------------------------------------
# Templates (must be registered BEFORE /{workflow_id} parameterised routes)
# ---------------------------------------------------------------------------


@router.get("/templates", response_model=ApiResponse)
async def list_workflow_templates(
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> ApiResponse:
    """Return all built-in workflow templates (not stored in DB)."""
    from fim_one.core.workflow.templates import list_templates

    templates = list_templates()
    return ApiResponse(
        data=[WorkflowTemplateResponse(**t).model_dump() for t in templates]
    )


@router.post("/from-template", response_model=ApiResponse)
async def create_workflow_from_template(
    body: WorkflowFromTemplateRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Create a new workflow from a built-in template."""
    from fim_one.core.workflow.templates import get_template

    template = get_template(body.template_id)
    if template is None:
        raise AppError("template_not_found", status_code=404)

    blueprint = template["blueprint"]
    input_schema, output_schema = _extract_schemas_from_blueprint(blueprint)

    wf = Workflow(
        user_id=current_user.id,
        name=body.name or template["name"],
        icon=template.get("icon"),
        description=template.get("description"),
        blueprint=blueprint,
        input_schema=input_schema,
        output_schema=output_schema,
        status="draft",
        is_active=True,
    )
    db.add(wf)
    await db.commit()
    result = await db.execute(select(Workflow).where(Workflow.id == wf.id))
    wf = result.scalar_one()
    return ApiResponse(data=_workflow_to_response(wf).model_dump())


# ---------------------------------------------------------------------------
# Single-workflow CRUD
# ---------------------------------------------------------------------------


@router.get("/{workflow_id}", response_model=ApiResponse)
async def get_workflow(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    wf = await _get_accessible_workflow(workflow_id, current_user.id, db)
    return ApiResponse(data=_workflow_to_response(wf).model_dump())


@router.put("/{workflow_id}", response_model=ApiResponse)
async def update_workflow(
    workflow_id: str,
    body: WorkflowUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    wf = await _get_owned_workflow(workflow_id, current_user.id, db)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(wf, field, value)

    # Auto-extract schemas when blueprint is updated
    if "blueprint" in update_data and update_data["blueprint"] is not None:
        input_schema, output_schema = _extract_schemas_from_blueprint(
            update_data["blueprint"]
        )
        wf.input_schema = input_schema
        wf.output_schema = output_schema

        # Auto-version: snapshot blueprint when it actually changes
        new_bp = update_data["blueprint"]
        latest_result = await db.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .order_by(WorkflowVersion.version_number.desc())
            .limit(1)
        )
        latest_ver = latest_result.scalar_one_or_none()

        # Compare blueprints — only version if different (or no version yet)
        should_version = latest_ver is None or json.dumps(
            latest_ver.blueprint, sort_keys=True
        ) != json.dumps(new_bp, sort_keys=True)

        if should_version:
            next_num = (latest_ver.version_number + 1) if latest_ver else 1
            ver = WorkflowVersion(
                workflow_id=workflow_id,
                version_number=next_num,
                blueprint=new_bp,
                input_schema=input_schema,
                output_schema=output_schema,
                change_summary=None,
                created_by=current_user.id,
            )
            db.add(ver)

    content_changed = bool(update_data.keys() - {"is_active"})
    if content_changed:
        from fim_one.web.publish_review import check_edit_revert

        reverted = await check_edit_revert(wf, db)
    else:
        reverted = False

    await db.commit()
    result = await db.execute(select(Workflow).where(Workflow.id == wf.id))
    wf = result.scalar_one()
    data = _workflow_to_response(wf).model_dump()
    if reverted:
        data["publish_status_reverted"] = True
    return ApiResponse(data=data)


@router.post("/{workflow_id}/duplicate", response_model=ApiResponse)
async def duplicate_workflow(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Create a copy of an existing workflow."""
    wf = await _get_accessible_workflow(workflow_id, current_user.id, db)
    copy = Workflow(
        user_id=current_user.id,
        name=f"{wf.name} (Copy)",
        icon=wf.icon,
        description=wf.description,
        blueprint=wf.blueprint,
        input_schema=wf.input_schema,
        output_schema=wf.output_schema,
        status="draft",
    )
    db.add(copy)
    await db.commit()
    result = await db.execute(select(Workflow).where(Workflow.id == copy.id))
    copy = result.scalar_one()
    return ApiResponse(data=_workflow_to_response(copy).model_dump())


@router.delete("/{workflow_id}", response_model=ApiResponse)
async def delete_workflow(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    wf = await _get_owned_workflow(workflow_id, current_user.id, db)
    await db.delete(wf)
    await db.commit()
    return ApiResponse(data={"deleted": workflow_id})


# ---------------------------------------------------------------------------
# Publish / Unpublish / Resubmit
# ---------------------------------------------------------------------------


@router.post("/{workflow_id}/publish", response_model=ApiResponse)
async def publish_workflow(
    workflow_id: str,
    body: PublishRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Publish workflow to org or global scope."""
    wf = await _get_owned_workflow(workflow_id, current_user.id, db)

    if body.scope == "org":
        if not body.org_id:
            raise AppError("org_id_required", status_code=400)
        from fim_one.web.auth import require_org_member
        await require_org_member(body.org_id, current_user, db)
        wf.visibility = "org"
        wf.org_id = body.org_id
        from fim_one.web.publish_review import apply_publish_status
        await apply_publish_status(wf, body.org_id, db, resource_type="workflow")
    elif body.scope == "global":
        if not current_user.is_admin:
            raise AppError("admin_required_for_global", status_code=403)
        wf.visibility = "global"
        wf.org_id = None
    else:
        raise AppError("invalid_scope", status_code=400)

    wf.published_at = datetime.now(UTC)

    # Audit log: submitted (org scope only)
    if body.scope == "org" and body.org_id:
        from fim_one.web.api.reviews import log_review_event
        await log_review_event(
            db=db,
            org_id=body.org_id,
            resource_type="workflow",
            resource_id=wf.id,
            resource_name=wf.name,
            action="submitted",
            actor=current_user,
        )

    await db.commit()
    await db.refresh(wf)

    return ApiResponse(data=_workflow_to_response(wf).model_dump())


@router.post("/{workflow_id}/resubmit", response_model=ApiResponse)
async def resubmit_workflow(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Resubmit a rejected workflow for review."""
    wf = await _get_owned_workflow(workflow_id, current_user.id, db)
    if wf.publish_status != "rejected":
        raise AppError("not_rejected", status_code=400)
    wf.publish_status = "pending_review"
    wf.reviewed_by = None
    wf.reviewed_at = None
    wf.review_note = None

    if wf.org_id:
        from fim_one.web.api.reviews import log_review_event
        await log_review_event(
            db=db,
            org_id=wf.org_id,
            resource_type="workflow",
            resource_id=wf.id,
            resource_name=wf.name,
            action="resubmitted",
            actor=current_user,
        )

    await db.commit()
    await db.refresh(wf)
    return ApiResponse(data=_workflow_to_response(wf).model_dump())


@router.post("/{workflow_id}/unpublish", response_model=ApiResponse)
async def unpublish_workflow(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Revert workflow to personal visibility."""
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    wf = result.scalar_one_or_none()
    if wf is None:
        raise AppError("workflow_not_found", status_code=404)

    is_owner = wf.user_id == current_user.id
    is_admin = current_user.is_admin
    is_org_admin = False

    if wf.visibility == "org" and wf.org_id and not is_owner:
        try:
            from fim_one.web.auth import require_org_admin
            await require_org_admin(wf.org_id, current_user, db)
            is_org_admin = True
        except Exception:
            pass

    if not (is_owner or is_admin or is_org_admin):
        raise AppError("unpublish_denied", status_code=403)

    # Log BEFORE clearing org_id
    if wf.org_id:
        from fim_one.web.api.reviews import log_review_event
        await log_review_event(
            db=db,
            org_id=wf.org_id,
            resource_type="workflow",
            resource_id=wf.id,
            resource_name=wf.name,
            action="unpublished",
            actor=current_user,
        )

    wf.visibility = "personal"
    wf.org_id = None
    wf.published_at = None
    wf.publish_status = None

    await db.commit()
    await db.refresh(wf)
    return ApiResponse(data=_workflow_to_response(wf).model_dump())


# ---------------------------------------------------------------------------
# Validate blueprint
# ---------------------------------------------------------------------------


@router.post("/validate", response_model=ApiResponse)
async def validate_blueprint_endpoint(
    body: dict,
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> ApiResponse:
    """Validate a workflow blueprint without saving it.

    Returns hard errors (blueprint can't parse) or soft warnings
    (blueprint is valid but has potential issues like disconnected nodes).
    """
    from fim_one.core.workflow.parser import (
        BlueprintValidationError,
        parse_blueprint,
        validate_blueprint as _validate,
    )

    blueprint = body.get("blueprint", body)
    try:
        parsed = parse_blueprint(blueprint)
        warnings = _validate(parsed)
        return ApiResponse(data={
            "valid": True,
            "node_count": len(parsed.nodes),
            "edge_count": len(parsed.edges),
            "warnings": [
                {
                    "node_id": w.node_id,
                    "code": w.code,
                    "message": w.message,
                }
                for w in warnings
            ],
        })
    except BlueprintValidationError as exc:
        return ApiResponse(data={
            "valid": False,
            "error": str(exc),
            "warnings": [],
        })


@router.post("/{workflow_id}/validate", response_model=ApiResponse)
async def validate_workflow(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Validate a saved workflow's blueprint and return structural analysis.

    Parses the blueprint, runs ``validate_blueprint()`` for warnings, and
    returns a structured ``WorkflowValidateResponse`` with topology order.
    Does **not** execute the workflow.
    """
    from fim_one.core.workflow.parser import (
        BlueprintValidationError,
        parse_blueprint,
        topological_sort,
        validate_blueprint as _validate,
    )

    wf = await _get_accessible_workflow(workflow_id, current_user.id, db)

    blueprint = wf.blueprint
    if not blueprint or not blueprint.get("nodes"):
        return ApiResponse(data=WorkflowValidateResponse(
            valid=False,
            errors=["Blueprint is empty or has no nodes"],
        ).model_dump())

    try:
        parsed = parse_blueprint(blueprint)
    except BlueprintValidationError as exc:
        return ApiResponse(data=WorkflowValidateResponse(
            valid=False,
            errors=[str(exc)],
        ).model_dump())

    warnings = _validate(parsed)
    topo_order = topological_sort(parsed)

    return ApiResponse(data=WorkflowValidateResponse(
        valid=True,
        errors=[],
        warnings=[
            BlueprintWarningItem(
                node_id=w.node_id,
                code=w.code,
                message=w.message,
            )
            for w in warnings
        ],
        node_count=len(parsed.nodes),
        edge_count=len(parsed.edges),
        topology_order=topo_order,
    ).model_dump())


# ---------------------------------------------------------------------------
# Execution endpoint (SSE streaming)
# ---------------------------------------------------------------------------


# Track running workflow tasks for cancellation
_running_tasks: dict[str, asyncio.Event] = {}


@router.post("/{workflow_id}/run")
async def run_workflow(
    workflow_id: str,
    body: WorkflowRunRequest,
    request: Request,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> Response:
    """Execute a workflow and stream progress via SSE.

    When ``body.dry_run`` is ``True``, parse and validate the blueprint,
    compute the topological execution order, and return a JSON response
    with the planned execution plan — no nodes are actually executed.
    """
    wf = await _get_accessible_workflow(workflow_id, current_user.id, db)

    # --- Dry-run mode: validate + return execution plan, no execution ---
    if body.dry_run:
        from fim_one.core.workflow.parser import (
            BlueprintValidationError,
            parse_blueprint,
            topological_sort,
            validate_blueprint as _validate,
        )

        blueprint = wf.blueprint
        if not blueprint or not blueprint.get("nodes"):
            dry_result = WorkflowDryRunResponse(
                valid=False,
                errors=["Blueprint is empty or has no nodes"],
            )
            return ApiResponse(data=dry_result.model_dump())

        try:
            parsed = parse_blueprint(blueprint)
        except BlueprintValidationError as exc:
            dry_result = WorkflowDryRunResponse(
                valid=False,
                errors=[str(exc)],
            )
            return ApiResponse(data=dry_result.model_dump())

        bp_warnings = _validate(parsed)
        topo_order = topological_sort(parsed)
        node_index = {n.id: n for n in parsed.nodes}

        # Build per-node warning lookup
        node_warning_ids: set[str] = set()
        for w in bp_warnings:
            if w.node_id:
                node_warning_ids.add(w.node_id)

        execution_plan = [
            DryRunNodePlan(
                node_id=nid,
                node_type=node_index[nid].type.value,
                position=idx,
                has_warnings=nid in node_warning_ids,
            )
            for idx, nid in enumerate(topo_order)
        ]

        dry_result = WorkflowDryRunResponse(
            valid=True,
            errors=[],
            warnings=[
                BlueprintWarningItem(
                    node_id=w.node_id,
                    code=w.code,
                    message=w.message,
                )
                for w in bp_warnings
            ],
            node_count=len(parsed.nodes),
            edge_count=len(parsed.edges),
            topology_order=topo_order,
            execution_plan=execution_plan,
        )
        return ApiResponse(data=dry_result.model_dump())

    # --- Normal execution mode (SSE streaming) ---

    # Create run record
    run_id = str(uuid.uuid4())
    run = WorkflowRun(
        id=run_id,
        workflow_id=wf.id,
        user_id=current_user.id,
        blueprint_snapshot=wf.blueprint,
        inputs=body.inputs,
        status="pending",
    )
    db.add(run)
    await db.commit()

    # Decrypt env vars if present
    env_vars: dict[str, str] = {}
    if wf.env_vars_blob:
        try:
            from fim_one.core.security.encryption import decrypt_credential

            env_vars = decrypt_credential(wf.env_vars_blob)
        except Exception:
            logger.warning("Failed to decrypt workflow env vars for %s", wf.id)

    cancel_event = asyncio.Event()
    _running_tasks[run_id] = cancel_event

    async def generate() -> AsyncGenerator[str, None]:
        start_time = time.time()
        node_results: dict[str, Any] = {}
        outputs: dict[str, Any] = {}
        final_status = "completed"
        error_msg: str | None = None

        try:
            yield _sse("run_started", {"run_id": run_id, "status": "running"})

            from fim_one.core.workflow.engine import WorkflowEngine
            from fim_one.core.workflow.parser import parse_blueprint

            blueprint = wf.blueprint
            parsed = parse_blueprint(blueprint)

            engine = WorkflowEngine(
                max_concurrency=5,
                cancel_event=cancel_event,
                env_vars=env_vars,
                run_id=run_id,
                user_id=current_user.id,
                workflow_id=wf.id,
            )

            ait = engine.execute_streaming(parsed, body.inputs).__aiter__()
            while True:
                try:
                    sse_event, sse_data = await asyncio.wait_for(
                        ait.__anext__(), timeout=15.0
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    # Keepalive comment to prevent proxy/browser timeout
                    if await request.is_disconnected():
                        cancel_event.set()
                        break
                    yield ": keepalive\n\n"
                    continue

                # Check for client disconnect
                if await request.is_disconnected():
                    cancel_event.set()
                    break

                # Track node results for persistence
                if sse_event in (
                    "node_started",
                    "node_completed",
                    "node_failed",
                    "node_skipped",
                ):
                    nid = sse_data.get("node_id", "")
                    node_results[nid] = {
                        **(node_results.get(nid) or {}),
                        **sse_data,
                    }

                if sse_event == "run_completed":
                    outputs = sse_data.get("outputs", {})
                    final_status = sse_data.get("status", "completed")
                elif sse_event == "run_failed":
                    final_status = "failed"
                    error_msg = sse_data.get("error")

                yield _sse(sse_event, sse_data)

        except asyncio.CancelledError:
            final_status = "cancelled"
            error_msg = "Execution cancelled"
            yield _sse("run_completed", {
                "run_id": run_id,
                "status": "cancelled",
                "error": error_msg,
            })
        except Exception as exc:
            final_status = "failed"
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.exception("Workflow execution failed for run %s", run_id)
            yield _sse("run_failed", {
                "run_id": run_id,
                "status": "failed",
                "error": error_msg,
            })
        finally:
            _running_tasks.pop(run_id, None)
            elapsed_ms = int((time.time() - start_time) * 1000)

            # Persist run results
            try:
                async with create_session() as persist_db:
                    result = await persist_db.execute(
                        select(WorkflowRun).where(WorkflowRun.id == run_id)
                    )
                    db_run = result.scalar_one_or_none()
                    if db_run:
                        db_run.status = final_status
                        db_run.outputs = outputs or None
                        db_run.node_results = node_results or None
                        db_run.started_at = datetime.fromtimestamp(
                            start_time, tz=UTC
                        )
                        db_run.completed_at = datetime.now(UTC)
                        db_run.duration_ms = elapsed_ms
                        db_run.error = error_msg
                        await persist_db.commit()
            except Exception:
                logger.exception("Failed to persist workflow run %s", run_id)

            yield _sse("end", {})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-store",
        },
    )


# ---------------------------------------------------------------------------
# Variable introspection (for frontend config panels)
# ---------------------------------------------------------------------------


@router.get("/{workflow_id}/variables", response_model=ApiResponse)
async def get_workflow_variables(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Analyze the workflow blueprint and return available variables per node.

    Used by the frontend variable-picker dropdowns in node config panels.
    For each node the response includes the node_type, title, and a list of
    declared output variables with name and type.
    """
    wf = await _get_accessible_workflow(workflow_id, current_user.id, db)

    from fim_one.core.workflow.nodes import EXECUTOR_REGISTRY
    from fim_one.core.workflow.parser import parse_blueprint

    blueprint = wf.blueprint
    if not blueprint or not blueprint.get("nodes"):
        return ApiResponse(data={})

    try:
        parsed = parse_blueprint(blueprint)
    except Exception as exc:
        raise AppError(f"invalid_blueprint: {exc}", status_code=400)

    variables_map: dict[str, Any] = {}
    for node_def in parsed.nodes:
        node_data = node_def.data or {}
        title = node_data.get("title") or node_data.get("label") or node_def.id

        executor_cls = EXECUTOR_REGISTRY.get(node_def.type)
        declared_outputs: list[dict[str, str]] = []
        if executor_cls is not None:
            # Call the static output_schema() if the executor defines one
            schema_fn = getattr(executor_cls, "output_schema", None)
            if schema_fn is not None:
                declared_outputs = schema_fn()

        # For START nodes, also include the individual input variables from
        # the variables array (frontend format) or input_schema (legacy).
        if node_def.type.value == "START":
            # Try variables array first (frontend format)
            variables = node_data.get("variables", [])
            if variables:
                for var in variables:
                    name = var.get("name", "")
                    if name:
                        declared_outputs.append({
                            "name": name,
                            "type": var.get("type", "string"),
                            "description": f"Input variable: {name}",
                        })
            else:
                # Fallback: legacy input_schema
                input_schema = node_data.get("input_schema") or node_data.get("schema")
                if isinstance(input_schema, dict):
                    props = input_schema.get("properties", {})
                    for prop_name, prop_def in props.items():
                        declared_outputs.append({
                            "name": prop_name,
                            "type": prop_def.get("type", "string"),
                            "description": prop_def.get("description", ""),
                        })

        variables_map[node_def.id] = {
            "node_type": node_def.type.value,
            "title": title,
            "outputs": declared_outputs,
        }

    return ApiResponse(data=variables_map)


# ---------------------------------------------------------------------------
# Version history endpoints
# ---------------------------------------------------------------------------


@router.get("/{workflow_id}/versions", response_model=PaginatedResponse)
async def list_workflow_versions(
    workflow_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    """List all versions for a workflow, newest first."""
    await _get_accessible_workflow(workflow_id, current_user.id, db)

    base = select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow_id)

    count_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(
        base.order_by(WorkflowVersion.version_number.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    versions = result.scalars().all()

    return PaginatedResponse(
        items=[_version_to_response(v).model_dump() for v in versions],
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total else 0,
    )


@router.get("/{workflow_id}/versions/{version_id}", response_model=ApiResponse)
async def get_workflow_version(
    workflow_id: str,
    version_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Get a specific workflow version by ID."""
    await _get_accessible_workflow(workflow_id, current_user.id, db)

    result = await db.execute(
        select(WorkflowVersion).where(
            WorkflowVersion.id == version_id,
            WorkflowVersion.workflow_id == workflow_id,
        )
    )
    ver = result.scalar_one_or_none()
    if ver is None:
        raise AppError("workflow_version_not_found", status_code=404)

    return ApiResponse(data=_version_to_response(ver).model_dump())


@router.post("/{workflow_id}/versions/{version_id}/restore", response_model=ApiResponse)
async def restore_workflow_version(
    workflow_id: str,
    version_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Restore a workflow to a specific version's blueprint.

    Creates a new version entry to record the restore action, then updates
    the workflow's live blueprint to match the restored version.
    """
    wf = await _get_owned_workflow(workflow_id, current_user.id, db)

    # Fetch the version to restore
    result = await db.execute(
        select(WorkflowVersion).where(
            WorkflowVersion.id == version_id,
            WorkflowVersion.workflow_id == workflow_id,
        )
    )
    ver = result.scalar_one_or_none()
    if ver is None:
        raise AppError("workflow_version_not_found", status_code=404)

    # Apply the restored blueprint
    wf.blueprint = ver.blueprint
    input_schema, output_schema = _extract_schemas_from_blueprint(ver.blueprint)
    wf.input_schema = input_schema
    wf.output_schema = output_schema

    # Create a new version entry to record the restore
    latest_result = await db.execute(
        select(func.max(WorkflowVersion.version_number)).where(
            WorkflowVersion.workflow_id == workflow_id
        )
    )
    max_num = latest_result.scalar_one() or 0

    restore_ver = WorkflowVersion(
        workflow_id=workflow_id,
        version_number=max_num + 1,
        blueprint=ver.blueprint,
        input_schema=input_schema,
        output_schema=output_schema,
        change_summary=f"Restored from version {ver.version_number}",
        created_by=current_user.id,
    )
    db.add(restore_ver)

    await db.commit()
    result = await db.execute(select(Workflow).where(Workflow.id == wf.id))
    wf = result.scalar_one()
    return ApiResponse(data=_workflow_to_response(wf).model_dump())


# ---------------------------------------------------------------------------
# Run history endpoints
# ---------------------------------------------------------------------------


@router.get("/{workflow_id}/runs", response_model=PaginatedResponse)
async def list_workflow_runs(
    workflow_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, pattern="^(pending|running|completed|failed|cancelled)$"),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    # Verify access to the workflow
    await _get_accessible_workflow(workflow_id, current_user.id, db)

    base = select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id)
    if status:
        base = base.where(WorkflowRun.status == status)

    count_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(
        base.order_by(WorkflowRun.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    runs = result.scalars().all()

    return PaginatedResponse(
        items=[_run_to_response(r).model_dump() for r in runs],
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total else 0,
    )


@router.get("/{workflow_id}/stats", response_model=ApiResponse)
async def get_workflow_stats(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Return execution statistics for a workflow.

    Includes total runs, success/failure rates, average duration, and the
    last run timestamp.  Useful for dashboard cards and editor status bars.
    """
    await _get_accessible_workflow(workflow_id, current_user.id, db)

    base = select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id)

    # Total runs
    total_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total_runs = total_result.scalar_one()

    if total_runs == 0:
        return ApiResponse(data={
            "total_runs": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "success_rate": None,
            "avg_duration_ms": None,
            "last_run_at": None,
        })

    # Status breakdown
    status_counts: dict[str, int] = {}
    for status_val in ("completed", "failed", "cancelled", "running", "pending"):
        count_result = await db.execute(
            select(func.count()).where(
                WorkflowRun.workflow_id == workflow_id,
                WorkflowRun.status == status_val,
            )
        )
        status_counts[status_val] = count_result.scalar_one()

    # Average duration of completed runs
    avg_result = await db.execute(
        select(func.avg(WorkflowRun.duration_ms)).where(
            WorkflowRun.workflow_id == workflow_id,
            WorkflowRun.status == "completed",
            WorkflowRun.duration_ms.isnot(None),
        )
    )
    avg_duration = avg_result.scalar_one()

    # Last run timestamp
    last_result = await db.execute(
        select(WorkflowRun.created_at)
        .where(WorkflowRun.workflow_id == workflow_id)
        .order_by(WorkflowRun.created_at.desc())
        .limit(1)
    )
    last_run_row = last_result.scalar_one_or_none()

    completed = status_counts.get("completed", 0)
    failed = status_counts.get("failed", 0)
    finished = completed + failed
    success_rate = round(completed / finished * 100, 1) if finished > 0 else None

    return ApiResponse(data={
        "total_runs": total_runs,
        "completed": completed,
        "failed": failed,
        "cancelled": status_counts.get("cancelled", 0),
        "success_rate": success_rate,
        "avg_duration_ms": int(avg_duration) if avg_duration else None,
        "last_run_at": last_run_row.isoformat() if last_run_row else None,
    })


@router.get("/{workflow_id}/node-stats", response_model=ApiResponse)
async def get_workflow_node_stats(
    workflow_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Return per-node execution statistics aggregated from recent runs.

    Analyzes the ``node_results`` JSON from the most recent runs to compute
    per-node success rate, average duration, and failure count.  Useful for
    identifying bottleneck or flaky nodes.
    """
    await _get_accessible_workflow(workflow_id, current_user.id, db)

    # Fetch recent runs that have node_results
    result = await db.execute(
        select(WorkflowRun.node_results)
        .where(
            WorkflowRun.workflow_id == workflow_id,
            WorkflowRun.node_results.isnot(None),
        )
        .order_by(WorkflowRun.created_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()

    # Aggregate per-node stats
    node_stats: dict[str, dict] = {}
    for node_results_json in rows:
        if not isinstance(node_results_json, dict):
            continue
        for node_id, nr in node_results_json.items():
            if not isinstance(nr, dict):
                continue
            if node_id not in node_stats:
                node_stats[node_id] = {
                    "node_id": node_id,
                    "total": 0,
                    "completed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "total_duration_ms": 0,
                    "min_duration_ms": None,
                    "max_duration_ms": None,
                }
            stats = node_stats[node_id]
            stats["total"] += 1
            status = nr.get("status", "")
            if status == "completed":
                stats["completed"] += 1
            elif status == "failed":
                stats["failed"] += 1
            elif status == "skipped":
                stats["skipped"] += 1

            dur = nr.get("duration_ms")
            if isinstance(dur, (int, float)) and dur > 0:
                stats["total_duration_ms"] += dur
                if stats["min_duration_ms"] is None or dur < stats["min_duration_ms"]:
                    stats["min_duration_ms"] = dur
                if stats["max_duration_ms"] is None or dur > stats["max_duration_ms"]:
                    stats["max_duration_ms"] = dur

    # Compute averages and success rates
    result_list = []
    for stats in node_stats.values():
        finished = stats["completed"] + stats["failed"]
        avg_ms = (
            int(stats["total_duration_ms"] / finished)
            if finished > 0
            else None
        )
        success_rate = (
            round(stats["completed"] / finished * 100, 1)
            if finished > 0
            else None
        )
        result_list.append({
            "node_id": stats["node_id"],
            "total_runs": stats["total"],
            "completed": stats["completed"],
            "failed": stats["failed"],
            "skipped": stats["skipped"],
            "avg_duration_ms": avg_ms,
            "min_duration_ms": stats["min_duration_ms"],
            "max_duration_ms": stats["max_duration_ms"],
            "success_rate": success_rate,
        })

    # Sort by total runs descending
    result_list.sort(key=lambda x: x["total_runs"], reverse=True)

    return ApiResponse(data={
        "runs_analyzed": len(rows),
        "nodes": result_list,
    })


@router.get("/{workflow_id}/runs/{run_id}", response_model=ApiResponse)
async def get_workflow_run(
    workflow_id: str,
    run_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    # Verify access to the workflow
    await _get_accessible_workflow(workflow_id, current_user.id, db)

    result = await db.execute(
        select(WorkflowRun).where(
            WorkflowRun.id == run_id,
            WorkflowRun.workflow_id == workflow_id,
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise AppError("workflow_run_not_found", status_code=404)

    return ApiResponse(data=_run_to_response(run).model_dump())


@router.post("/{workflow_id}/runs/{run_id}/cancel", response_model=ApiResponse)
async def cancel_workflow_run(
    workflow_id: str,
    run_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    # Verify ownership
    await _get_owned_workflow(workflow_id, current_user.id, db)

    cancel_event = _running_tasks.get(run_id)
    if cancel_event:
        cancel_event.set()
        return ApiResponse(data={"cancelled": True, "run_id": run_id})

    # If not running, check if it exists and update status
    result = await db.execute(
        select(WorkflowRun).where(
            WorkflowRun.id == run_id,
            WorkflowRun.workflow_id == workflow_id,
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise AppError("workflow_run_not_found", status_code=404)

    if run.status in ("pending", "running"):
        run.status = "cancelled"
        await db.commit()

    return ApiResponse(data={"cancelled": True, "run_id": run_id})


# ---------------------------------------------------------------------------
# Export / Import
# ---------------------------------------------------------------------------


@router.get("/{workflow_id}/export")
async def export_workflow(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> Response:
    """Export a workflow as a downloadable JSON file.

    Returns a ``fim_workflow_v1`` envelope stripped of user/org metadata.
    """
    wf = await _get_accessible_workflow(workflow_id, current_user.id, db)
    export_data = WorkflowExportData(
        name=wf.name,
        icon=wf.icon,
        description=wf.description,
        blueprint=wf.blueprint or {"nodes": [], "edges": [], "viewport": {}},
        input_schema=wf.input_schema,
        output_schema=wf.output_schema,
    )
    envelope = WorkflowExportFile(
        format="fim_workflow_v1",
        exported_at=datetime.now(UTC).isoformat(),
        workflow=export_data,
    )
    content = json.dumps(envelope.model_dump(), ensure_ascii=False, indent=2)
    # Sanitise filename: replace whitespace/special chars
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in wf.name)
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="workflow-{safe_name}.json"',
        },
    )


@router.post("/import", response_model=ApiResponse)
async def import_workflow(
    body: WorkflowImportFileRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Import a workflow from an exported JSON payload.

    Accepts the ``fim_workflow_v1`` envelope format:
    ``{ "format": "fim_workflow_v1", "exported_at": ..., "workflow": {...} }``

    Also accepts the legacy shape ``{ "data": {...} }`` for backwards
    compatibility.
    """
    # Resolve the workflow data from either envelope or legacy shape
    data = body.workflow or body.data
    if data is None:
        raise AppError("import_invalid_format", status_code=400)

    # Validate format field when present
    if body.format is not None and body.format != "fim_workflow_v1":
        raise AppError("import_invalid_format", status_code=400)

    # Validate blueprint structure: must have nodes with at least a start node
    blueprint = data.blueprint
    nodes = blueprint.get("nodes", [])
    if not nodes:
        raise AppError("import_invalid_blueprint", status_code=400)

    has_start = any(
        (n.get("data", {}) or {}).get("type", "").upper() == "START"
        or n.get("type", "").upper() == "START"
        for n in nodes
    )
    if not has_start:
        raise AppError("import_invalid_blueprint", status_code=400)

    # Deduplicate name: append " (imported)" if a workflow with the same
    # name already exists for this user.
    name = data.name
    existing = await db.execute(
        select(func.count()).where(
            Workflow.user_id == current_user.id,
            Workflow.name == name,
        )
    )
    if existing.scalar_one() > 0:
        name = f"{name} (imported)"

    wf = Workflow(
        user_id=current_user.id,
        name=name,
        icon=data.icon,
        description=data.description,
        blueprint=data.blueprint,
        input_schema=data.input_schema,
        output_schema=data.output_schema,
        status="draft",
    )
    db.add(wf)
    await db.commit()
    result = await db.execute(select(Workflow).where(Workflow.id == wf.id))
    wf = result.scalar_one()
    return ApiResponse(data=_workflow_to_response(wf).model_dump())


# ---------------------------------------------------------------------------
# Env vars management
# ---------------------------------------------------------------------------


@router.put("/{workflow_id}/env", response_model=ApiResponse)
async def update_workflow_env(
    workflow_id: str,
    body: WorkflowEnvVarsUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Store encrypted env vars for the workflow."""
    wf = await _get_owned_workflow(workflow_id, current_user.id, db)

    from fim_one.core.security.encryption import encrypt_credential

    if body.env_vars:
        wf.env_vars_blob = encrypt_credential(body.env_vars)
    else:
        wf.env_vars_blob = None

    await db.commit()

    # Return keys only (not values) for security
    return ApiResponse(
        data={"keys": list(body.env_vars.keys()) if body.env_vars else []}
    )
