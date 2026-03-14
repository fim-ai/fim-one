"""Workflow CRUD endpoints with SSE execution streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import secrets
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session, create_session
from fim_one.web.exceptions import AppError
from fim_one.web.auth import get_current_user, get_user_org_ids
from fim_one.web.models import User, Workflow, WorkflowRun, WorkflowVersion
from fim_one.web.schemas.common import ApiResponse, PaginatedResponse, PublishRequest
from fim_one.web.schemas.workflow import (
    BatchRunResultItem,
    BlueprintWarningItem,
    DryRunNodePlan,
    MostFailedNode,
    RunsPerDay,
    WorkflowAnalyticsResponse,
    WorkflowApiKeyResponse,
    WorkflowBatchRunRequest,
    WorkflowBatchRunResponse,
    WorkflowCreate,
    WorkflowDryRunResponse,
    WorkflowEnvVarsUpdate,
    WorkflowExportData,
    WorkflowExportFile,
    WorkflowFromTemplateRequest,
    WorkflowImportFileRequest,
    WorkflowImportResponse,
    WorkflowResponse,
    UnresolvedReferenceItem,
    WorkflowRunRequest,
    WorkflowRunResponse,
    WorkflowScheduleResponse,
    WorkflowScheduleUpdate,
    WorkflowTemplateResponse,
    WorkflowTriggerRequest,
    WorkflowTriggerResponse,
    WorkflowUpdate,
    WorkflowValidateResponse,
    WorkflowVersionResponse,
    _compute_next_run,
)
from fim_one.web.visibility import build_visibility_filter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _percentile(sorted_values: list[int], pct: int) -> int:
    """Return the *pct*-th percentile from a pre-sorted list of ints.

    Uses the nearest-rank method.  ``sorted_values`` **must** already be
    sorted in ascending order and contain at least one element.
    """
    n = len(sorted_values)
    idx = max(0, min(math.ceil(pct / 100 * n) - 1, n - 1))
    return sorted_values[idx]


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
        webhook_url=getattr(wf, "webhook_url", None),
        schedule_cron=getattr(wf, "schedule_cron", None),
        schedule_enabled=getattr(wf, "schedule_enabled", False),
        schedule_inputs=getattr(wf, "schedule_inputs", None),
        schedule_timezone=getattr(wf, "schedule_timezone", "UTC") or "UTC",
        has_api_key=bool(getattr(wf, "api_key", None)),
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

    # Batch-fetch run stats for all workflows on this page (single query)
    wf_ids = [w.id for w in workflows]
    run_stats: dict[str, dict] = {}
    if wf_ids:
        stats_q = await db.execute(
            select(
                WorkflowRun.workflow_id,
                func.count().label("total"),
                func.count(
                    func.nullif(WorkflowRun.status != "completed", True)
                ).label("completed"),
                func.max(WorkflowRun.created_at).label("last_run"),
            )
            .where(WorkflowRun.workflow_id.in_(wf_ids))
            .group_by(WorkflowRun.workflow_id)
        )
        for row in stats_q:
            rate = (row.completed / row.total * 100) if row.total else None
            run_stats[row.workflow_id] = {
                "total_runs": row.total,
                "last_run_at": row.last_run.isoformat() if row.last_run else None,
                "success_rate": round(rate, 1) if rate is not None else None,
            }

    items = []
    for w in workflows:
        resp = _workflow_to_response(w)
        stats = run_stats.get(w.id, {})
        resp.total_runs = stats.get("total_runs", 0)
        resp.last_run_at = stats.get("last_run_at")
        resp.success_rate = stats.get("success_rate")
        items.append(resp.model_dump())

    return PaginatedResponse(
        items=items,
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
# Public trigger (API key auth, no user session required)
# Must be registered BEFORE /{workflow_id} parameterised routes.
# ---------------------------------------------------------------------------


@router.post("/trigger/{api_key}", response_model=ApiResponse)
async def trigger_workflow(
    api_key: str,
    body: WorkflowTriggerRequest,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Trigger a workflow execution via its API key (no user auth required).

    This is the public webhook-style endpoint for external systems to invoke
    a workflow.  The workflow is looked up by its unique ``api_key`` field.
    Runs synchronously (non-SSE) and returns the final result.
    """
    # Look up workflow by API key
    result = await db.execute(
        select(Workflow).where(Workflow.api_key == api_key)
    )
    wf = result.scalar_one_or_none()
    if wf is None:
        raise AppError("invalid_api_key", status_code=401, detail="Invalid API key")

    if not wf.is_active:
        raise AppError("workflow_inactive", status_code=403, detail="Workflow is inactive")

    if not wf.blueprint or not wf.blueprint.get("nodes"):
        raise AppError("blueprint_empty", status_code=400)

    # Rate limit: reject if there's already a running run for this workflow
    running_result = await db.execute(
        select(func.count()).where(
            WorkflowRun.workflow_id == wf.id,
            WorkflowRun.status.in_(["pending", "running"]),
        )
    )
    if running_result.scalar_one() > 0:
        raise AppError(
            "workflow_already_running",
            status_code=429,
            detail="Workflow already has a running execution. Please wait for it to complete.",
        )

    from fim_one.core.workflow.engine import WorkflowEngine
    from fim_one.core.workflow.parser import BlueprintValidationError, parse_blueprint

    try:
        parsed = parse_blueprint(wf.blueprint)
    except BlueprintValidationError as exc:
        raise AppError(f"invalid_blueprint: {exc}", status_code=400)

    # Decrypt env vars if present
    env_vars: dict[str, str] = {}
    if wf.env_vars_blob:
        try:
            from fim_one.core.security.encryption import decrypt_credential

            env_vars = decrypt_credential(wf.env_vars_blob)
        except Exception:
            logger.warning("Failed to decrypt workflow env vars for %s", wf.id)

    # Create run record
    run_id = str(uuid.uuid4())
    run = WorkflowRun(
        id=run_id,
        workflow_id=wf.id,
        user_id=wf.user_id,  # attribute run to the workflow owner
        blueprint_snapshot=wf.blueprint,
        inputs=body.inputs,
        status="running",
    )
    db.add(run)
    await db.commit()

    # Execute synchronously (non-SSE)
    start_time = time.time()
    final_status = "completed"
    outputs: dict[str, Any] = {}
    node_results: dict[str, Any] = {}
    error_msg: str | None = None

    try:
        engine = WorkflowEngine(
            max_concurrency=5,
            env_vars=env_vars,
            run_id=run_id,
            user_id=wf.user_id,
            workflow_id=wf.id,
        )

        async for event_name, event_data in engine.execute_streaming(
            parsed, body.inputs
        ):
            if event_name in (
                "node_started",
                "node_completed",
                "node_failed",
                "node_skipped",
            ):
                nid = event_data.get("node_id", "")
                node_results[nid] = {
                    **(node_results.get(nid) or {}),
                    **event_data,
                }
            elif event_name == "run_completed":
                outputs = event_data.get("outputs", {})
                final_status = event_data.get("status", "completed")
            elif event_name == "run_failed":
                final_status = "failed"
                error_msg = event_data.get("error")

    except Exception as exc:
        final_status = "failed"
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.exception("Trigger execution failed for run %s", run_id)

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
                db_run.started_at = datetime.fromtimestamp(start_time, tz=UTC)
                db_run.completed_at = datetime.now(UTC)
                db_run.duration_ms = elapsed_ms
                db_run.error = error_msg
                await persist_db.commit()
    except Exception:
        logger.exception("Failed to persist trigger run %s", run_id)

    # Fire webhook if configured (fire-and-forget)
    wf_webhook_url = getattr(wf, "webhook_url", None)
    if wf_webhook_url and final_status in ("completed", "failed"):
        webhook_payload = {
            "event": "run_completed" if final_status == "completed" else "run_failed",
            "workflow_id": wf.id,
            "run_id": run_id,
            "status": final_status,
            "outputs": outputs or None,
            "error": error_msg,
            "duration_ms": elapsed_ms,
            "completed_at": datetime.now(UTC).isoformat(),
        }
        asyncio.create_task(_deliver_webhook(wf_webhook_url, webhook_payload))

    trigger_response = WorkflowTriggerResponse(
        run_id=run_id,
        status=final_status,
        outputs=outputs or None,
        error=error_msg,
        duration_ms=elapsed_ms,
    )
    return ApiResponse(data=trigger_response.model_dump())


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


async def _deliver_webhook(
    webhook_url: str,
    payload: dict[str, Any],
) -> None:
    """Fire-and-forget POST to a workflow webhook URL.

    Logs errors but never raises — callers should schedule this as a
    background task so it doesn't block the SSE stream.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                webhook_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Source": "fim-one",
                },
            )
            logger.info(
                "Webhook delivered to %s — status %d", webhook_url, resp.status_code
            )
    except Exception:
        logger.exception("Webhook delivery failed for %s", webhook_url)


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

            # Fire webhook if configured (fire-and-forget)
            wf_webhook_url = getattr(wf, "webhook_url", None)
            if wf_webhook_url and final_status in ("completed", "failed"):
                webhook_payload = {
                    "event": (
                        "run_completed"
                        if final_status == "completed"
                        else "run_failed"
                    ),
                    "workflow_id": wf.id,
                    "run_id": run_id,
                    "status": final_status,
                    "outputs": outputs or None,
                    "error": error_msg,
                    "duration_ms": elapsed_ms,
                    "completed_at": datetime.now(UTC).isoformat(),
                }
                asyncio.create_task(_deliver_webhook(wf_webhook_url, webhook_payload))

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
# Batch run
# ---------------------------------------------------------------------------


@router.post("/{workflow_id}/batch-run", response_model=ApiResponse)
async def batch_run_workflow(
    workflow_id: str,
    body: WorkflowBatchRunRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Execute a workflow multiple times with different input sets.

    Runs each input set as an independent workflow execution, bounded by
    ``max_parallel`` concurrency.  If one run fails, the rest continue.
    Returns a non-streaming JSON response with results for every input set.
    """
    wf = await _get_accessible_workflow(workflow_id, current_user.id, db)

    if not wf.blueprint or not wf.blueprint.get("nodes"):
        raise AppError("blueprint_empty", status_code=400)

    from fim_one.core.workflow.engine import WorkflowEngine
    from fim_one.core.workflow.parser import BlueprintValidationError, parse_blueprint

    try:
        parsed = parse_blueprint(wf.blueprint)
    except BlueprintValidationError as exc:
        raise AppError(f"invalid_blueprint: {exc}", status_code=400)

    # Decrypt env vars if present
    env_vars: dict[str, str] = {}
    if wf.env_vars_blob:
        try:
            from fim_one.core.security.encryption import decrypt_credential

            env_vars = decrypt_credential(wf.env_vars_blob)
        except Exception:
            logger.warning("Failed to decrypt workflow env vars for %s", wf.id)

    batch_id = str(uuid.uuid4())
    semaphore = asyncio.Semaphore(body.max_parallel)

    async def _run_single(index: int, inputs: dict[str, Any]) -> BatchRunResultItem:
        """Execute one workflow run within the batch."""
        run_id = str(uuid.uuid4())
        start_time = time.time()
        final_status = "completed"
        outputs: dict[str, Any] = {}
        node_results: dict[str, Any] = {}
        error_msg: str | None = None

        # Create run record
        try:
            async with create_session() as run_db:
                run = WorkflowRun(
                    id=run_id,
                    workflow_id=wf.id,
                    user_id=current_user.id,
                    blueprint_snapshot=wf.blueprint,
                    inputs=inputs,
                    status="pending",
                )
                run_db.add(run)
                await run_db.commit()
        except Exception:
            logger.exception("Failed to create run record for batch item %d", index)
            return BatchRunResultItem(
                run_id=run_id,
                inputs=inputs,
                status="failed",
                error="Failed to create run record",
                duration_ms=0,
            )

        async with semaphore:
            try:
                engine = WorkflowEngine(
                    max_concurrency=5,
                    env_vars=env_vars,
                    run_id=run_id,
                    user_id=current_user.id,
                    workflow_id=wf.id,
                )

                async for event_name, event_data in engine.execute_streaming(
                    parsed, inputs
                ):
                    if event_name in (
                        "node_started",
                        "node_completed",
                        "node_failed",
                        "node_skipped",
                    ):
                        nid = event_data.get("node_id", "")
                        node_results[nid] = {
                            **(node_results.get(nid) or {}),
                            **event_data,
                        }
                    elif event_name == "run_completed":
                        outputs = event_data.get("outputs", {})
                        final_status = event_data.get("status", "completed")
                    elif event_name == "run_failed":
                        final_status = "failed"
                        error_msg = event_data.get("error")

            except Exception as exc:
                final_status = "failed"
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception(
                    "Batch run %s item %d failed", batch_id, index
                )

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
                    db_run.started_at = datetime.fromtimestamp(start_time, tz=UTC)
                    db_run.completed_at = datetime.now(UTC)
                    db_run.duration_ms = elapsed_ms
                    db_run.error = error_msg
                    await persist_db.commit()
        except Exception:
            logger.exception("Failed to persist batch run %s item %d", batch_id, index)

        return BatchRunResultItem(
            run_id=run_id,
            inputs=inputs,
            status=final_status,
            outputs=outputs or None,
            error=error_msg,
            duration_ms=elapsed_ms,
        )

    # Launch all runs concurrently (bounded by semaphore)
    tasks = [
        asyncio.create_task(_run_single(i, input_set))
        for i, input_set in enumerate(body.inputs)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Convert any unexpected exceptions into error results
    batch_results: list[BatchRunResultItem] = []
    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            logger.exception(
                "Unexpected error in batch %s item %d", batch_id, i, exc_info=result
            )
            batch_results.append(
                BatchRunResultItem(
                    run_id="",
                    inputs=body.inputs[i],
                    status="failed",
                    error=str(result),
                    duration_ms=0,
                )
            )
        else:
            batch_results.append(result)

    response = WorkflowBatchRunResponse(
        batch_id=batch_id,
        total=len(body.inputs),
        results=batch_results,
    )
    return ApiResponse(data=response.model_dump())


# ---------------------------------------------------------------------------
# Webhook test
# ---------------------------------------------------------------------------


@router.post("/{workflow_id}/test-webhook", response_model=ApiResponse)
async def test_webhook(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Send a test payload to the workflow's configured webhook URL."""
    import httpx

    wf = await _get_owned_workflow(workflow_id, current_user.id, db)

    webhook_url = getattr(wf, "webhook_url", None)
    if not webhook_url:
        raise AppError("webhook_url_not_configured", status_code=400)

    test_payload = {
        "event": "test",
        "workflow_id": wf.id,
        "run_id": "test-00000000-0000-0000-0000-000000000000",
        "status": "completed",
        "outputs": {"message": "This is a test webhook delivery from FIM One."},
        "error": None,
        "duration_ms": 0,
        "completed_at": datetime.now(UTC).isoformat(),
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                webhook_url,
                json=test_payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Source": "fim-one",
                },
            )
        return ApiResponse(
            data={
                "success": 200 <= resp.status_code < 300,
                "status_code": resp.status_code,
                "webhook_url": webhook_url,
            }
        )
    except httpx.TimeoutException:
        return ApiResponse(
            data={
                "success": False,
                "status_code": None,
                "webhook_url": webhook_url,
                "error": "Request timed out after 10 seconds",
            }
        )
    except Exception as exc:
        return ApiResponse(
            data={
                "success": False,
                "status_code": None,
                "webhook_url": webhook_url,
                "error": str(exc),
            }
        )


# ---------------------------------------------------------------------------
# Schedule (cron-based trigger) endpoints
# ---------------------------------------------------------------------------


@router.get("/{workflow_id}/schedule", response_model=ApiResponse)
async def get_workflow_schedule(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Get the current schedule configuration for a workflow."""
    wf = await _get_accessible_workflow(workflow_id, current_user.id, db)

    cron = getattr(wf, "schedule_cron", None)
    enabled = getattr(wf, "schedule_enabled", False)
    tz = getattr(wf, "schedule_timezone", "UTC") or "UTC"

    next_run: str | None = None
    if cron and enabled:
        next_run = _compute_next_run(cron, tz)

    return ApiResponse(
        data=WorkflowScheduleResponse(
            schedule_cron=cron,
            schedule_enabled=enabled,
            schedule_inputs=getattr(wf, "schedule_inputs", None),
            schedule_timezone=tz,
            next_run_at=next_run,
        ).model_dump()
    )


@router.put("/{workflow_id}/schedule", response_model=ApiResponse)
async def update_workflow_schedule(
    workflow_id: str,
    body: WorkflowScheduleUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Set or update the schedule configuration for a workflow."""
    wf = await _get_owned_workflow(workflow_id, current_user.id, db)

    # If enabling without a cron expression, reject
    if body.enabled and not body.cron:
        raise AppError(
            "schedule_cron_required",
            status_code=400,
            detail="A cron expression is required when enabling a schedule.",
        )

    wf.schedule_cron = body.cron
    wf.schedule_enabled = body.enabled
    wf.schedule_inputs = body.inputs
    wf.schedule_timezone = body.timezone

    await db.commit()
    await db.refresh(wf)

    tz = wf.schedule_timezone or "UTC"
    next_run: str | None = None
    if wf.schedule_cron and wf.schedule_enabled:
        next_run = _compute_next_run(wf.schedule_cron, tz)

    return ApiResponse(
        data=WorkflowScheduleResponse(
            schedule_cron=wf.schedule_cron,
            schedule_enabled=wf.schedule_enabled,
            schedule_inputs=wf.schedule_inputs,
            schedule_timezone=tz,
            next_run_at=next_run,
        ).model_dump()
    )


@router.delete("/{workflow_id}/schedule", response_model=ApiResponse)
async def delete_workflow_schedule(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Clear the schedule configuration for a workflow."""
    wf = await _get_owned_workflow(workflow_id, current_user.id, db)

    wf.schedule_cron = None
    wf.schedule_enabled = False
    wf.schedule_inputs = None
    wf.schedule_timezone = "UTC"

    await db.commit()

    return ApiResponse(
        data=WorkflowScheduleResponse(
            schedule_cron=None,
            schedule_enabled=False,
            schedule_inputs=None,
            schedule_timezone="UTC",
            next_run_at=None,
        ).model_dump()
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


@router.get("/{workflow_id}/analytics", response_model=ApiResponse)
async def get_workflow_analytics(
    workflow_id: str,
    days: int = Query(default=30, ge=1, le=365),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Return comprehensive execution analytics for a workflow.

    Includes status distribution, percentile durations, daily run counts,
    most-failed nodes, and average node count per run.  The ``days`` query
    param controls the lookback window (default 30, max 365).
    """
    await _get_accessible_workflow(workflow_id, current_user.id, db)

    cutoff = datetime.now(UTC) - timedelta(days=days)
    base_filter = [
        WorkflowRun.workflow_id == workflow_id,
        WorkflowRun.created_at >= cutoff,
    ]

    # ------------------------------------------------------------------
    # 1. Total runs
    # ------------------------------------------------------------------
    total_result = await db.execute(
        select(func.count()).where(*base_filter)
    )
    total_runs: int = total_result.scalar_one()

    if total_runs == 0:
        return ApiResponse(
            data=WorkflowAnalyticsResponse(
                total_runs=0,
                status_distribution={},
            ).model_dump()
        )

    # ------------------------------------------------------------------
    # 2. Status distribution (single query with GROUP BY)
    # ------------------------------------------------------------------
    dist_result = await db.execute(
        select(WorkflowRun.status, func.count())
        .where(*base_filter)
        .group_by(WorkflowRun.status)
    )
    status_distribution: dict[str, int] = {
        row[0]: row[1] for row in dist_result.all()
    }

    completed = status_distribution.get("completed", 0)
    failed = status_distribution.get("failed", 0)
    finished = completed + failed
    success_rate = round(completed / finished * 100, 1) if finished > 0 else None

    # ------------------------------------------------------------------
    # 3. Duration stats (avg + percentiles from completed runs)
    # ------------------------------------------------------------------
    dur_result = await db.execute(
        select(WorkflowRun.duration_ms)
        .where(
            *base_filter,
            WorkflowRun.status == "completed",
            WorkflowRun.duration_ms.isnot(None),
        )
        .order_by(WorkflowRun.duration_ms)
    )
    durations: list[int] = [row[0] for row in dur_result.all()]

    avg_duration_ms: int | None = None
    p50_duration_ms: int | None = None
    p95_duration_ms: int | None = None
    p99_duration_ms: int | None = None

    if durations:
        avg_duration_ms = int(sum(durations) / len(durations))
        p50_duration_ms = _percentile(durations, 50)
        p95_duration_ms = _percentile(durations, 95)
        p99_duration_ms = _percentile(durations, 99)

    # ------------------------------------------------------------------
    # 4. Runs per day
    # ------------------------------------------------------------------
    # Fetch created_at + status for each run and bucket in Python.
    # This avoids dialect-specific date functions (SQLite vs PG).
    rpd_result = await db.execute(
        select(WorkflowRun.created_at, WorkflowRun.status)
        .where(*base_filter)
        .order_by(WorkflowRun.created_at)
    )
    day_buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {"count": 0, "completed": 0, "failed": 0}
    )
    for row in rpd_result.all():
        created_at_val = row[0]
        if created_at_val is None:
            continue
        day_key = created_at_val.strftime("%Y-%m-%d") if hasattr(created_at_val, "strftime") else str(created_at_val)[:10]
        bucket = day_buckets[day_key]
        bucket["count"] += 1
        run_status = row[1]
        if run_status == "completed":
            bucket["completed"] += 1
        elif run_status == "failed":
            bucket["failed"] += 1

    runs_per_day = [
        RunsPerDay(date=day, count=b["count"], completed=b["completed"], failed=b["failed"])
        for day, b in sorted(day_buckets.items())
    ]

    # ------------------------------------------------------------------
    # 5. Most-failed nodes (from node_results JSON)
    # ------------------------------------------------------------------
    nr_result = await db.execute(
        select(WorkflowRun.node_results)
        .where(
            *base_filter,
            WorkflowRun.node_results.isnot(None),
        )
    )
    node_failure_counts: dict[str, int] = defaultdict(int)
    node_total_counts: dict[str, int] = defaultdict(int)
    total_node_counts_per_run: list[int] = []

    for (node_results_json,) in nr_result.all():
        if not isinstance(node_results_json, dict):
            continue
        total_node_counts_per_run.append(len(node_results_json))
        for node_id, nr in node_results_json.items():
            if not isinstance(nr, dict):
                continue
            node_total_counts[node_id] += 1
            if nr.get("status") == "failed":
                node_failure_counts[node_id] += 1

    # Sort by failure count descending, take top 10
    most_failed_nodes = sorted(
        [
            MostFailedNode(
                node_id=nid,
                failure_count=count,
                total_runs=node_total_counts[nid],
            )
            for nid, count in node_failure_counts.items()
        ],
        key=lambda x: x.failure_count,
        reverse=True,
    )[:10]

    # ------------------------------------------------------------------
    # 6. Average nodes per run
    # ------------------------------------------------------------------
    avg_nodes_per_run: float | None = None
    if total_node_counts_per_run:
        avg_nodes_per_run = round(
            sum(total_node_counts_per_run) / len(total_node_counts_per_run), 1
        )

    # ------------------------------------------------------------------
    # Assemble response
    # ------------------------------------------------------------------
    analytics = WorkflowAnalyticsResponse(
        total_runs=total_runs,
        status_distribution=status_distribution,
        success_rate=success_rate,
        avg_duration_ms=avg_duration_ms,
        p50_duration_ms=p50_duration_ms,
        p95_duration_ms=p95_duration_ms,
        p99_duration_ms=p99_duration_ms,
        runs_per_day=runs_per_day,
        most_failed_nodes=most_failed_nodes,
        avg_nodes_per_run=avg_nodes_per_run,
    )
    return ApiResponse(data=analytics.model_dump())


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


@router.delete("/{workflow_id}/runs/{run_id}", response_model=ApiResponse)
async def delete_workflow_run(
    workflow_id: str,
    run_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Delete a single workflow run record.

    Only completed, failed, or cancelled runs can be deleted.
    Running/pending runs must be cancelled first.
    """
    await _get_owned_workflow(workflow_id, current_user.id, db)

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
        raise AppError("cannot_delete_active_run", status_code=409)

    await db.delete(run)
    await db.commit()
    return ApiResponse(data={"deleted": run_id})


@router.delete("/{workflow_id}/runs", response_model=ApiResponse)
async def clear_workflow_runs(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Delete all completed/failed/cancelled runs for a workflow.

    Active (pending/running) runs are preserved.
    """
    await _get_owned_workflow(workflow_id, current_user.id, db)

    result = await db.execute(
        delete(WorkflowRun).where(
            WorkflowRun.workflow_id == workflow_id,
            WorkflowRun.status.in_(["completed", "failed", "cancelled"]),
        )
    )
    await db.commit()
    return ApiResponse(data={"deleted_count": result.rowcount})


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

    The response includes an ``unresolved_references`` list and ``warnings``
    for any nodes that reference external resources (agents, connectors,
    knowledge bases, sub-workflows, MCP servers) that do not exist or are
    not accessible to the importing user.  The import still succeeds even
    when references are unresolved.
    """
    from fim_one.core.workflow import parse_blueprint, resolve_blueprint_references

    # Resolve the workflow data from either envelope or legacy shape
    data = body.workflow or body.data
    if data is None:
        raise AppError("import_invalid_format", status_code=400)

    # Validate format field when present
    if body.format is not None and body.format != "fim_workflow_v1":
        raise AppError("import_invalid_format", status_code=400)

    # Validate blueprint structure: must have nodes with at least a start node
    raw_blueprint = data.blueprint
    nodes = raw_blueprint.get("nodes", [])
    if not nodes:
        raise AppError("import_invalid_blueprint", status_code=400)

    has_start = any(
        (n.get("data", {}) or {}).get("type", "").upper() == "START"
        or n.get("type", "").upper() == "START"
        for n in nodes
    )
    if not has_start:
        raise AppError("import_invalid_blueprint", status_code=400)

    # Parse the blueprint so the resolver can work with typed node definitions
    try:
        parsed_bp = parse_blueprint(raw_blueprint)
    except Exception:
        raise AppError("import_invalid_blueprint", status_code=400)

    # Resolve external references
    user_org_ids = await get_user_org_ids(current_user.id, db)
    resolution = await resolve_blueprint_references(
        parsed_bp, db, current_user.id, user_org_ids
    )

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

    import_response = WorkflowImportResponse(
        workflow=_workflow_to_response(wf),
        unresolved_references=[
            UnresolvedReferenceItem(
                node_id=u.node_id,
                node_type=u.node_type,
                field_name=u.field_name,
                referenced_id=u.referenced_id,
                resource_type=u.resource_type,
            )
            for u in resolution.unresolved
        ],
        warnings=resolution.warnings,
    )
    return ApiResponse(data=import_response.model_dump())


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


# ---------------------------------------------------------------------------
# API key management (for public trigger endpoint)
# ---------------------------------------------------------------------------


@router.post("/{workflow_id}/generate-api-key", response_model=ApiResponse)
async def generate_workflow_api_key(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Generate a new API key for external workflow triggering.

    The key is returned in the response and is only shown once.
    If an existing key exists, it is replaced.
    """
    wf = await _get_owned_workflow(workflow_id, current_user.id, db)

    # Generate a new API key: wf_ prefix + 43-char token = ~46 chars total
    new_key = f"wf_{secrets.token_urlsafe(32)}"
    wf.api_key = new_key
    await db.commit()

    response = WorkflowApiKeyResponse(
        api_key=new_key,
        workflow_id=wf.id,
    )
    return ApiResponse(data=response.model_dump())


@router.delete("/{workflow_id}/api-key", response_model=ApiResponse)
async def revoke_workflow_api_key(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Revoke the API key for a workflow, disabling external triggers."""
    wf = await _get_owned_workflow(workflow_id, current_user.id, db)

    if not wf.api_key:
        raise AppError("no_api_key", status_code=404, detail="Workflow has no API key")

    wf.api_key = None
    await db.commit()

    return ApiResponse(data={"revoked": True, "workflow_id": wf.id})
