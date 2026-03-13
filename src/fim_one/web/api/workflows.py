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
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session, create_session
from fim_one.web.exceptions import AppError
from fim_one.web.auth import get_current_user, get_user_org_ids
from fim_one.web.models import User, Workflow, WorkflowRun
from fim_one.web.schemas.common import ApiResponse, PaginatedResponse
from fim_one.web.schemas.workflow import (
    WorkflowCreate,
    WorkflowEnvVarsUpdate,
    WorkflowExportData,
    WorkflowImportRequest,
    WorkflowResponse,
    WorkflowRunRequest,
    WorkflowRunResponse,
    WorkflowUpdate,
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

    Returns (input_schema, output_schema).
    """
    input_schema: dict | None = None
    output_schema: dict | None = None

    nodes = blueprint.get("nodes", [])
    for node in nodes:
        node_type = (node.get("data", {}) or {}).get("type", "") or node.get("type", "")
        node_data = node.get("data", {}) or {}

        if node_type.upper() == "START":
            input_schema = node_data.get("input_schema") or node_data.get("schema")
        elif node_type.upper() == "END":
            output_schema = node_data.get("output_schema") or node_data.get("schema")

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

    await db.commit()
    result = await db.execute(select(Workflow).where(Workflow.id == wf.id))
    wf = result.scalar_one()
    return ApiResponse(data=_workflow_to_response(wf).model_dump())


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
) -> StreamingResponse:
    """Execute a workflow and stream progress via SSE."""
    wf = await _get_accessible_workflow(workflow_id, current_user.id, db)

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
# Run history endpoints
# ---------------------------------------------------------------------------


@router.get("/{workflow_id}/runs", response_model=PaginatedResponse)
async def list_workflow_runs(
    workflow_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    # Verify access to the workflow
    await _get_accessible_workflow(workflow_id, current_user.id, db)

    base = select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id)

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


@router.get("/{workflow_id}/export", response_model=ApiResponse)
async def export_workflow(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    wf = await _get_accessible_workflow(workflow_id, current_user.id, db)
    export_data = WorkflowExportData(
        name=wf.name,
        icon=wf.icon,
        description=wf.description,
        blueprint=wf.blueprint or {"nodes": [], "edges": [], "viewport": {}},
        input_schema=wf.input_schema,
        output_schema=wf.output_schema,
    )
    return ApiResponse(data=export_data.model_dump())


@router.post("/import", response_model=ApiResponse)
async def import_workflow(
    body: WorkflowImportRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    data = body.data
    wf = Workflow(
        user_id=current_user.id,
        name=data.name,
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
