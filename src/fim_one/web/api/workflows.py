"""Workflow CRUD endpoints with SSE execution streaming."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
import socket
import uuid
from collections import defaultdict
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import CursorResult, and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.core.security import get_safe_async_client, is_private_ip
from fim_one.web.exceptions import AppError
from fim_one.web.auth import get_current_user, get_user_org_ids
from fim_one.web.platform import MARKET_ORG_ID, is_market_org
from fim_one.core.assembly.confirmation import (
    ConfirmationService,
    close_requests_of_runs,
    validate_assignee,
)
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import AuthMethod, Principal, resolve_principal
from fim_one.db.models import User, Workflow, WorkflowRun, WorkflowVersion
from fim_one.db.models.channel import ConfirmationRequest
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.db.models.workflow import RUN_MODE_TEST_NODE
from fim_one.web.schemas.common import ApiResponse, PaginatedResponse, PublishRequest
from fim_one.web.schemas.workflow import (
    BatchRunResultItem,
    BlueprintWarningItem,
    DryRunNodePlan,
    MostFailedNode,
    RunsPerDay,
    NodeTestRequest,
    NodeTestResponse,
    WorkflowAnalyticsResponse,
    WorkflowApiKeyResponse,
    WorkflowBatchRunRequest,
    WorkflowBatchRunResponse,
    WorkflowCreate,
    WorkflowDryRunResponse,
    WorkflowEnvVarsUpdate,
    WorkflowExportData,
    WorkflowExportFile,
    WorkflowForkRequest,
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
    WorkflowApprovalResponse,
    ApprovalDecisionRequest,
    _compute_next_run,
)
from fim_one.web.visibility import build_visibility_filter
from fim_one.web.services.feature_flags import require_workflows_enabled

logger = logging.getLogger(__name__)

# The whole Workflows module is soft-shelvable (off by default). Gating at
# the router level 404s every endpoint — including webhook/API-key triggers
# — in lock-step when an admin has not enabled the module.
router = APIRouter(
    prefix="/api/workflows",
    tags=["workflows"],
    dependencies=[Depends(require_workflows_enabled)],
)



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


def _validate_webhook_url(url: str) -> None:
    """Validate a webhook URL to prevent SSRF attacks.

    Only allows ``http://`` and ``https://`` schemes and rejects URLs
    that resolve to private/loopback IP ranges or ``localhost``.

    Raises
    ------
    AppError
        If the URL is invalid or points to a private/internal address.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        raise AppError(
            "invalid_webhook_url",
            status_code=400,
            detail="Invalid webhook URL",
        )

    # Scheme check
    if parsed.scheme not in ("http", "https"):
        raise AppError(
            "invalid_webhook_url",
            status_code=400,
            detail="Webhook URL must use http or https scheme",
        )

    hostname = parsed.hostname
    if not hostname:
        raise AppError(
            "invalid_webhook_url",
            status_code=400,
            detail="Webhook URL must include a hostname",
        )

    # Reject localhost by name
    if hostname.lower() in ("localhost", "localhost.localdomain"):
        raise AppError(
            "invalid_webhook_url",
            status_code=400,
            detail="Webhook URL must not point to localhost",
        )

    # Resolve hostname to IP and check for private/loopback ranges
    try:
        addr_infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise AppError(
            "invalid_webhook_url",
            status_code=400,
            detail="Could not resolve webhook URL hostname",
        )

    for family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_reserved
            or ip.is_link_local
            or is_private_ip(str(ip))
        ):
            raise AppError(
                "invalid_webhook_url",
                status_code=400,
                detail="Webhook URL must not point to a private or internal address",
            )


def _sse(event: str, data: Any) -> str:
    """Format a Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _workflow_to_response(wf: Workflow, *, is_owner: bool = True) -> WorkflowResponse:
    # Non-owners: strip internal content (blueprint, schemas, webhook are IP)
    if is_owner:
        blueprint = wf.blueprint or {"nodes": [], "edges": [], "viewport": {}}
        input_schema = wf.input_schema
        output_schema = wf.output_schema
        webhook_url = getattr(wf, "webhook_url", None)
    else:
        blueprint = None
        input_schema = None
        output_schema = None
        webhook_url = None
    return WorkflowResponse(
        id=wf.id,
        user_id=wf.user_id,
        name=wf.name,
        icon=wf.icon,
        description=wf.description,
        blueprint=blueprint,
        input_schema=input_schema,
        output_schema=output_schema,
        status=wf.status,
        is_active=wf.is_active,
        visibility=getattr(wf, "visibility", "personal"),
        org_id=getattr(wf, "org_id", None),
        change_summary=getattr(wf, "change_summary", None),
        publish_status=getattr(wf, "publish_status", None),
        published_at=(
            pub_at.isoformat() if (pub_at := getattr(wf, "published_at", None)) else None
        ),
        reviewed_by=getattr(wf, "reviewed_by", None),
        reviewed_at=(
            rev_at.isoformat()
            if (rev_at := getattr(wf, "reviewed_at", None))
            else None
        ),
        review_note=getattr(wf, "review_note", None),
        webhook_url=webhook_url,
        schedule_cron=getattr(wf, "schedule_cron", None),
        schedule_enabled=getattr(wf, "schedule_enabled", False),
        schedule_inputs=getattr(wf, "schedule_inputs", None),
        schedule_timezone=getattr(wf, "schedule_timezone", "UTC") or "UTC",
        has_api_key=bool(wf.api_key_hash),
        api_key_prefix=wf.api_key_prefix if is_owner else None,
        forked_from=getattr(wf, "forked_from", None),
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
    """Fetch a workflow the user owns, org-shared, or Market-installed."""
    from fim_one.web.visibility import resolve_visibility
    vis_filter, _, _ = await resolve_visibility(Workflow, user_id, "workflow", db)
    result = await db.execute(
        select(Workflow).where(Workflow.id == workflow_id, vis_filter)
    )
    wf = result.scalar_one_or_none()
    if wf is None:
        raise AppError("workflow_not_found", status_code=404)
    return wf


def _own_runs_clause(user_id: str) -> Any:
    """Restrict run queries to runs the caller started (or that bill to them).

    Run records carry the runner's inputs and outputs, so their contents are
    shown only to that runner. This holds for the workflow author too: an
    author sees subscribers' runs only through the aggregate endpoints
    (counts, durations, success and failure), never their inputs or outputs.
    """
    return and_(WorkflowRun.user_id == user_id, _history_run_clause())


def _history_run_clause() -> Any:
    """Exclude single-node test executions from run history and statistics."""
    from fim_one.db.models.workflow import history_run_clause

    return history_run_clause()


def _run_stats_clauses(wf: Workflow, user_id: str) -> list[Any]:
    """WHERE clauses for aggregate run statistics.

    The author aggregates over every run of the workflow; anyone else only
    over the runs they started themselves.
    """
    clauses: list[Any] = [WorkflowRun.workflow_id == wf.id, _history_run_clause()]
    if wf.user_id != user_id:
        clauses.append(_own_runs_clause(user_id))
    return clauses


def normalize_blueprint(raw: Any) -> dict[str, Any]:
    """Upgrade an incoming blueprint to v2 and validate it against the schema.

    Every write of a blueprint goes through here, so stored blueprints are
    always v2. A schema violation answers 422 ``blueprint_schema_invalid``
    with ``error_args.errors`` listing ``{loc, node_id, message}`` per field.
    """
    from fim_one.core.workflow.blueprint_v2 import BlueprintSchemaError, normalize_for_save

    try:
        blueprint = normalize_for_save(raw)
    except BlueprintSchemaError as exc:
        raise AppError(
            "blueprint_schema_invalid",
            status_code=422,
            detail="The workflow blueprint has invalid fields",
            detail_args={"errors": exc.errors},
        ) from None
    ensure_nodes_enabled(blueprint)
    return blueprint


def ensure_nodes_enabled(blueprint: Any) -> None:
    """Refuse to store a blueprint that contains a switched-off node type.

    Called for every blueprint that is about to be written, including the
    copies made by fork, version restore and create-from-template, which
    do not pass through :func:`normalize_blueprint`. Answers 422
    ``human_intervention_disabled``; ``error_args.errors`` lists
    ``{loc, node_id, label, message}`` per node (the shape
    ``blueprint_schema_invalid`` uses) and ``error_args.nodes`` their labels.
    """
    from fim_one.core.workflow.node_gates import (
        HUMAN_INTERVENTION_DISABLED,
        disabled_node_errors,
    )

    errors = disabled_node_errors(blueprint)
    if errors:
        nodes = ", ".join(str(e["label"]) for e in errors)
        raise AppError(
            HUMAN_INTERVENTION_DISABLED,
            status_code=422,
            detail=(
                "Human Intervention steps are switched off on this instance. "
                f"Remove these steps to continue: {nodes}"
            ),
            detail_args={"errors": errors, "nodes": nodes},
        )


async def ensure_assignees_valid(
    db: AsyncSession, blueprint: Any, *, user: User, org_id: str | None
) -> None:
    """Refuse a blueprint whose Human Intervention step names an assignee it cannot have.

    Checks the assignees written as literals (a ``{{variable}}`` is checked
    when the run interpolates it) with the rule the run applies: an active
    user in the workflow's organization, or, for a workflow in none, one
    sharing an organization with the saver. Answers 400
    ``approval_assignee_not_in_org`` naming the step (``error_args.step``)
    and the assignee.
    """
    from fim_one.core.workflow.blueprint_v2 import node_type_of
    from fim_one.core.workflow.types import NodeType

    if not isinstance(blueprint, dict):
        return
    nodes = blueprint.get("nodes")
    saver: Principal | None = None
    for node in nodes if isinstance(nodes, list) else []:
        if node_type_of(node) != NodeType.HUMAN_INTERVENTION:
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        assignee = str(data.get("assignee") or "").strip()
        if not assignee or "{{" in assignee:
            continue
        if saver is None:
            saver = await _principal_of(db, user)
        try:
            await validate_assignee(db, assignee=assignee, initiator=saver, org_id=org_id)
        except AssemblyRefusal as refusal:
            label = data.get("label") or node.get("id") or ""
            raise AppError(
                refusal.error_code,
                status_code=refusal.status_code,
                detail=f"Human Intervention step '{label}': {refusal.detail}",
                detail_args={"step": str(label), "assignee": assignee},
            ) from None


def _disabled_node_items(blueprint: Any) -> list[BlueprintWarningItem]:
    """One validation item per switched-off node in *blueprint*."""
    from fim_one.core.workflow.node_gates import (
        HUMAN_INTERVENTION_DISABLED,
        disabled_node_errors,
    )

    return [
        BlueprintWarningItem(
            node_id=e["node_id"], code=HUMAN_INTERVENTION_DISABLED, message=e["message"]
        )
        for e in disabled_node_errors(blueprint)
    ]


def _refused(exc: Any) -> AppError:
    """Map a run service refusal onto the API's error envelope."""
    return AppError(exc.error_code, status_code=exc.status_code, detail=exc.detail)


async def _get_version_owner_workflow(
    workflow_id: str,
    user_id: str,
    db: AsyncSession,
) -> Workflow:
    """Fetch a workflow whose version history the user may read.

    Versions carry full blueprints, which non-authors never see (the same
    rule ``_workflow_to_response`` applies to the live workflow). A user who
    can see the workflow but is not its author gets 403; anyone else 404.
    """
    wf = await _get_accessible_workflow(workflow_id, user_id, db)
    if wf.user_id != user_id:
        raise AppError(
            "workflow_versions_owner_only",
            status_code=403,
            detail="Only the workflow author can view its version history",
        )
    return wf


def _extract_schemas_from_blueprint(
    blueprint: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Extract input/output schemas from Start and End nodes in the blueprint.

    The frontend stores Start node inputs as ``data.variables``:
    ``[{name, type, default_value, required}]``.  We convert this to a
    JSON Schema dict for the ``input_schema`` column.

    Returns (input_schema, output_schema).
    """
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None

    nodes = blueprint.get("nodes", [])
    for node in nodes:
        node_type = str(node.get("type") or "")  # v2: "start" / "end"
        node_data = node.get("data", {}) or {}

        if node_type.upper() == "START":
            # First check for explicit input_schema (legacy)
            input_schema = node_data.get("input_schema") or node_data.get("schema")
            # Convert variables array to JSON Schema if no explicit schema
            if not input_schema:
                variables = node_data.get("variables", [])
                if variables:
                    properties: dict[str, dict[str, Any]] = {}
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
    blueprint = normalize_blueprint(body.blueprint)
    await ensure_assignees_valid(db, blueprint, user=current_user, org_id=None)
    input_schema, output_schema = _extract_schemas_from_blueprint(blueprint)
    wf = Workflow(
        user_id=current_user.id,
        name=body.name,
        icon=body.icon,
        description=body.description,
        blueprint=blueprint,
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

    # Get subscribed workflow IDs with org_id for source tagging
    sub_result = await db.execute(
        select(ResourceSubscription.resource_id, ResourceSubscription.org_id).where(
            ResourceSubscription.user_id == current_user.id,
            ResourceSubscription.resource_type == "workflow",
        )
    )
    sub_rows = sub_result.all()
    subscribed_workflow_ids = [r.resource_id for r in sub_rows]
    sub_org_map = {r.resource_id: r.org_id for r in sub_rows}

    base = select(Workflow).where(
        build_visibility_filter(Workflow, current_user.id, user_org_ids, subscribed_ids=subscribed_workflow_ids),
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
    run_stats: dict[str, dict[str, Any]] = {}
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

    subscribed_workflow_ids_set = set(subscribed_workflow_ids)
    items = []
    for w in workflows:
        _is_owner = w.user_id == current_user.id
        resp = _workflow_to_response(w, is_owner=_is_owner)
        stats = run_stats.get(w.id, {})
        resp.total_runs = stats.get("total_runs", 0)
        resp.last_run_at = stats.get("last_run_at")
        resp.success_rate = stats.get("success_rate")
        if _is_owner:
            resp.source = "own"
        elif w.id in subscribed_workflow_ids_set:
            sub_org_id = sub_org_map.get(w.id)
            resp.source = "market" if sub_org_id == MARKET_ORG_ID else "org"
        else:
            resp.source = "org"  # fallback, should not be reached
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
    from fim_one.core.workflow.node_gates import disabled_node_errors
    from fim_one.core.workflow.templates import list_templates

    # Templates built on a switched-off node type are not offered.
    templates = [t for t in list_templates() if not disabled_node_errors(t["blueprint"])]
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
    ensure_nodes_enabled(blueprint)
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


# Successor of the deprecated key-in-path trigger route.
TRIGGER_PATH = "/api/workflows/trigger"
# Response headers on the deprecated route (RFC 9745 ``Deprecation``,
# RFC 8288 ``Link``). The route is kept for one release.
_TRIGGER_DEPRECATION_HEADERS = {
    "Deprecation": "true",
    "Link": f'<{TRIGGER_PATH}>; rel="successor-version"',
    "Warning": (
        '299 - "Passing the trigger key in the URL is deprecated; send it as '
        f'Authorization: Bearer <key> to POST {TRIGGER_PATH}"'
    ),
}


async def _resolve_trigger_workflow(raw_key: str | None, db: AsyncSession) -> Workflow:
    from fim_one.web.services.workflow_trigger_keys import (
        TriggerKeyRejectedError,
        resolve_trigger_key,
    )

    try:
        return await resolve_trigger_key(raw_key, db)
    except TriggerKeyRejectedError as exc:
        raise AppError(exc.error_code, status_code=401, detail=exc.detail) from None


@router.post("/trigger", response_model=ApiResponse)
async def trigger_workflow(
    body: WorkflowTriggerRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Trigger a workflow execution with its trigger key (no user session).

    This is the public webhook-style endpoint for external systems. Send the
    key as ``Authorization: Bearer <key>``. Runs synchronously (non-SSE) and
    returns the final result. The run is billed to the workflow's author.
    """
    from fim_one.web.services.workflow_trigger_keys import parse_bearer

    raw_key = parse_bearer(request.headers.get("authorization"))
    wf = await _resolve_trigger_workflow(raw_key, db)
    logger.info("Workflow %s triggered by key %s", wf.id, wf.api_key_prefix)
    return await _execute_trigger(wf, body)


@router.post("/trigger/{api_key}", response_model=ApiResponse, deprecated=True)
async def trigger_workflow_legacy(
    api_key: str,
    body: WorkflowTriggerRequest,
    response: Response,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Deprecated: trigger with the key in the URL path.

    Kept for one release so existing integrations keep working; responses
    carry ``Deprecation`` and ``Link`` headers pointing at ``POST /trigger``
    with an ``Authorization`` header. Keys in URLs end up in proxy and
    access logs.
    """
    response.headers.update(_TRIGGER_DEPRECATION_HEADERS)
    wf = await _resolve_trigger_workflow(api_key, db)
    logger.info(
        "Workflow %s triggered by key %s via the deprecated URL route",
        wf.id,
        wf.api_key_prefix,
    )
    return await _execute_trigger(wf, body)


async def _execute_trigger(
    wf: Workflow,
    body: WorkflowTriggerRequest,
) -> ApiResponse:
    """Run *wf* for a trigger-key request. It runs as its author."""
    from fim_one.core.workflow.run_service import (
        RunMode,
        RunRefusedError,
        get_run_service,
    )

    try:
        outcome = await get_run_service().run_to_completion(
            wf,
            caller_id=None,
            inputs=body.inputs,
            mode=RunMode.TRIGGER,
            # One trigger-key run at a time per workflow.
            single_flight=True,
        )
    except RunRefusedError as exc:
        raise _refused(exc) from None

    trigger_response = WorkflowTriggerResponse(
        run_id=outcome.run_id,
        status=outcome.status,
        outputs=outcome.outputs or None,
        error=outcome.error,
        duration_ms=outcome.duration_ms,
    )
    return ApiResponse(data=trigger_response.model_dump())


# ---------------------------------------------------------------------------
# Approval endpoints (MUST be before /{workflow_id} to avoid route clash)
# ---------------------------------------------------------------------------


# Workflow approvals are confirmation requests of kind "workflow_approval";
# these endpoints are a view onto ``ConfirmationService``.
APPROVAL_KIND = "workflow_approval"


def _confirmation_service() -> ConfirmationService:
    from fim_one.db import create_session

    return ConfirmationService(create_session)


async def _principal_of(db: AsyncSession, user: User) -> Principal:
    return await resolve_principal(db, user_id=user.id, auth=AuthMethod.SESSION)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _approval_to_response(row: ConfirmationRequest) -> WorkflowApprovalResponse:
    payload = row.payload if isinstance(row.payload, dict) else {}
    return WorkflowApprovalResponse(
        id=row.id,
        workflow_run_id=row.workflow_run_id or "",
        node_id=row.node_id or "",
        title=row.title or "",
        description=row.description,
        status=row.status,
        assignee=row.eligible_user_id if row.eligible_scope == "assignee" else None,
        decision_by=row.decided_by_user_id,
        decision_note=row.decision_note,
        timeout_hours=float(payload.get("timeout_hours") or 24.0),
        created_at=_iso(row.created_at) or "",
        resolved_at=_iso(row.responded_at),
        updated_at=_iso(row.updated_at),
    )


async def _decide_approval(
    approval_id: str,
    body: ApprovalDecisionRequest,
    current_user: User,
    db: AsyncSession,
    *,
    decision: Literal["approve", "reject"],
) -> ApiResponse:
    """Decide a workflow approval the user may decide, or raise.

    An assigned approval belongs to its assignee (``approval_not_assigned``
    to anyone else); an unassigned one to the user its run is attributed
    to, and to nobody else it does not exist (``approval_not_found``). A
    platform administrator may decide either.
    """
    row = (
        await db.execute(
            select(ConfirmationRequest).where(
                ConfirmationRequest.id == approval_id,
                ConfirmationRequest.kind == APPROVAL_KIND,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError("approval_not_found", status_code=404)
    actor = await _principal_of(db, current_user)
    service = _confirmation_service()
    if not await service.may_decide(db, row, actor):
        if row.eligible_scope == "assignee":
            raise AppError(
                "approval_not_assigned",
                status_code=403,
                detail="This approval is assigned to another user",
            )
        # Same response as a missing approval: do not reveal that it exists.
        raise AppError("approval_not_found", status_code=404)
    try:
        decided = await service.decide(
            db, confirmation_id=approval_id, actor=actor, decision=decision, note=body.note
        )
    except AssemblyRefusal as refusal:
        if refusal.error_code == "confirmation_already_decided":
            raise AppError(
                "approval_already_resolved",
                status_code=409,
                detail="Approval has already been resolved",
            ) from None
        raise AppError("approval_not_found", status_code=404) from None
    return ApiResponse(data=_approval_to_response(decided).model_dump())


@router.get("/approvals/pending", response_model=ApiResponse)
async def list_pending_approvals(
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """List pending approvals assigned to the current user, plus unassigned
    ones on runs attributed to the current user."""
    actor = await _principal_of(db, current_user)
    approvals = await _confirmation_service().pending_for(db, actor, kind=APPROVAL_KIND)
    return ApiResponse(
        data=[_approval_to_response(a).model_dump() for a in approvals]
    )


@router.post("/approvals/{approval_id}/approve", response_model=ApiResponse)
async def approve_approval(
    approval_id: str,
    body: ApprovalDecisionRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Approve a pending workflow approval."""
    return await _decide_approval(approval_id, body, current_user, db, decision="approve")


@router.post("/approvals/{approval_id}/reject", response_model=ApiResponse)
async def reject_approval(
    approval_id: str,
    body: ApprovalDecisionRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Reject a pending workflow approval."""
    return await _decide_approval(approval_id, body, current_user, db, decision="reject")


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
    _is_owner = wf.user_id == current_user.id
    return ApiResponse(data=_workflow_to_response(wf, is_owner=_is_owner).model_dump())


@router.put("/{workflow_id}", response_model=ApiResponse)
async def update_workflow(
    workflow_id: str,
    body: WorkflowUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    wf = await _get_owned_workflow(workflow_id, current_user.id, db)

    update_data = body.model_dump(exclude_unset=True)

    # Validate webhook URL against SSRF before applying
    if "webhook_url" in update_data and update_data["webhook_url"] is not None:
        _validate_webhook_url(update_data["webhook_url"])

    # Capture old blueprint BEFORE applying updates (for diff computation)
    old_blueprint: dict[str, Any] | None = None
    if "blueprint" in update_data and update_data["blueprint"] is not None:
        update_data["blueprint"] = normalize_blueprint(update_data["blueprint"])
        await ensure_assignees_valid(
            db, update_data["blueprint"], user=current_user, org_id=wf.org_id
        )
        old_blueprint = wf.blueprint or {"nodes": [], "edges": [], "viewport": {}}

    for field, value in update_data.items():
        setattr(wf, field, value)

    # Auto-extract schemas and compute change summary when blueprint is updated
    if old_blueprint is not None:
        new_bp = update_data["blueprint"]
        input_schema, output_schema = _extract_schemas_from_blueprint(new_bp)
        wf.input_schema = input_schema
        wf.output_schema = output_schema

        # Compute a human-readable diff between old and new blueprints
        from fim_one.core.workflow.blueprint_diff import compute_blueprint_diff

        diff_summary = compute_blueprint_diff(old_blueprint, new_bp)
        wf.change_summary = diff_summary

        # Auto-version: snapshot blueprint when it actually changes
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
                change_summary=diff_summary,
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


# ---------------------------------------------------------------------------
# Duplicate (fork with forked_from tracking)
# ---------------------------------------------------------------------------


@router.post("/{workflow_id}/fork", response_model=ApiResponse)
async def fork_workflow(
    workflow_id: str,
    body: WorkflowForkRequest | None = None,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Fork an existing workflow for the current user.

    Copies blueprint, schemas, and description but NOT cron schedule,
    API key, env vars, or run history.  The forked workflow is set to
    personal/draft ownership.
    Only the owner of the source workflow can fork it.
    """
    import copy as _copy

    source = await _get_accessible_workflow(workflow_id, current_user.id, db)
    if source.user_id != current_user.id:
        raise AppError("fork_denied", status_code=403, detail="Only the owner can fork this resource")
    ensure_nodes_enabled(source.blueprint)

    fork_name = (body.name if body and body.name else f"{source.name} (Copy)")[:200]

    forked = Workflow(
        user_id=current_user.id,
        name=fork_name,
        icon=source.icon,
        description=source.description,
        blueprint=_copy.deepcopy(source.blueprint),
        input_schema=_copy.deepcopy(source.input_schema) if source.input_schema else None,
        output_schema=_copy.deepcopy(source.output_schema) if source.output_schema else None,
        status="draft",
        is_active=False,
        visibility="personal",
        org_id=None,
        publish_status=None,
        forked_from=source.id,
    )
    db.add(forked)
    await db.commit()
    result = await db.execute(select(Workflow).where(Workflow.id == forked.id))
    forked = result.scalar_one()
    return ApiResponse(data=_workflow_to_response(forked).model_dump())


@router.delete("/{workflow_id}", response_model=ApiResponse)
async def delete_workflow(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    wf = await _get_owned_workflow(workflow_id, current_user.id, db)
    from fim_one.web.api.market import purge_resource_subscriptions

    await purge_resource_subscriptions(
        db, resource_type="workflow", resource_id=workflow_id
    )
    # The runs go with the workflow; their open approvals are closed.
    await close_requests_of_runs(
        db, select(WorkflowRun.id).where(WorkflowRun.workflow_id == workflow_id)
    )
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
        if not is_market_org(body.org_id):
            await require_org_member(body.org_id, current_user, db)
        # The blueprint was saved against the organization the workflow was
        # in then (or none); its literal assignees must fit the one it is
        # published to, the rule a run there applies.
        await ensure_assignees_valid(db, wf.blueprint, user=current_user, org_id=body.org_id)
        wf.visibility = "org"
        wf.org_id = body.org_id
        from fim_one.web.publish_review import apply_publish_status
        await apply_publish_status(wf, body.org_id, db, resource_type="workflow", publisher_id=current_user.id)
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
        from fim_one.web.auth import user_is_org_admin

        is_org_admin = await user_is_org_admin(wf.org_id, current_user, db)

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
    body: dict[str, Any],
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

    from fim_one.core.workflow.blueprint_v2 import (
        BlueprintSchemaError,
        normalize_for_save,
    )

    blueprint = body.get("blueprint", body)
    try:
        blueprint = normalize_for_save(blueprint)
    except BlueprintSchemaError as exc:
        return ApiResponse(data={
            "valid": False,
            "error": str(exc),
            "field_errors": exc.errors,
            "warnings": [],
        })
    # Switched-off node types make the blueprint unsaveable; each such node
    # also gets a warning entry so the canvas marks it.
    gate_errors = _disabled_node_items(blueprint)
    try:
        parsed = parse_blueprint(blueprint)
        warnings = _validate(parsed)
        data: dict[str, Any] = {
            "valid": not gate_errors,
            "node_count": len(parsed.nodes),
            "edge_count": len(parsed.edges),
            "warnings": [
                *(w.model_dump() for w in gate_errors),
                *(
                    {
                        "node_id": w.node_id,
                        "code": w.code,
                        "message": w.message,
                    }
                    for w in warnings
                ),
            ],
        }
        if gate_errors:
            data["error"] = "; ".join(w.message for w in gate_errors)
        return ApiResponse(data=data)
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
    Does **not** execute the workflow. Author only: the analysis names the
    blueprint's nodes.
    """
    from fim_one.core.workflow.parser import (
        BlueprintValidationError,
        parse_blueprint,
        topological_sort,
        validate_blueprint as _validate,
    )

    wf = await _get_owned_workflow(workflow_id, current_user.id, db)

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
    gate_errors = _disabled_node_items(blueprint)

    return ApiResponse(data=WorkflowValidateResponse(
        valid=not gate_errors,
        errors=[w.message for w in gate_errors],
        warnings=[
            *gate_errors,
            *(
                BlueprintWarningItem(
                    node_id=w.node_id,
                    code=w.code,
                    message=w.message,
                )
                for w in warnings
            ),
        ],
        node_count=len(parsed.nodes),
        edge_count=len(parsed.edges),
        topology_order=topo_order,
    ).model_dump())


# ---------------------------------------------------------------------------
# Execution endpoint (SSE streaming)
# ---------------------------------------------------------------------------



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
    A dry run is ``valid`` only if a real run by the caller would pass the
    run service's checks (account, quota, plan, switched-off steps);
    otherwise ``errors`` and ``error_code`` say why. It creates no run and
    uses no rate-limit budget, so it does not predict the concurrent-run
    and runs-per-minute limits.
    """
    from fim_one.core.workflow.run_service import (
        RunMode,
        RunRefusedError,
        get_run_service,
    )

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
                error_code="blueprint_empty",
            )
            return ApiResponse(data=dry_result.model_dump())  # type: ignore[return-value]

        is_author = wf.user_id == current_user.id
        try:
            parsed = parse_blueprint(blueprint)
        except BlueprintValidationError as exc:
            dry_result = WorkflowDryRunResponse(
                valid=False,
                # Parse errors name nodes; other users only learn that it
                # is invalid.
                errors=[str(exc) if is_author else "The workflow blueprint is invalid"],
                error_code="invalid_blueprint",
            )
            return ApiResponse(data=dry_result.model_dump())  # type: ignore[return-value]

        # What a real run by this caller would be refused for, if anything.
        refusal = await get_run_service().would_refuse(
            wf, current_user.id, RunMode.INTERACTIVE
        )
        refusal_errors = [refusal.detail] if refusal else []
        refusal_code = refusal.error_code if refusal else None

        if not is_author:
            # The plan, warnings and topology describe the blueprint, which
            # only its author sees.
            dry_result = WorkflowDryRunResponse(
                valid=refusal is None, errors=refusal_errors, error_code=refusal_code
            )
            return ApiResponse(data=dry_result.model_dump())  # type: ignore[return-value]

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
            valid=refusal is None,
            errors=refusal_errors,
            error_code=refusal_code,
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
        return ApiResponse(data=dry_result.model_dump())  # type: ignore[return-value]

    # --- Normal execution mode (SSE streaming) ---
    try:
        started = await get_run_service().start_run(
            wf,
            caller_id=current_user.id,
            inputs=body.inputs,
            mode=RunMode.INTERACTIVE,
        )
    except RunRefusedError as exc:
        raise _refused(exc) from None

    run_id = started.run_id

    async def generate() -> AsyncGenerator[str, None]:
        # The run executes in the run service's background task, which
        # persists it and releases its slot; this stream only relays events.
        # Until runs are decoupled from the browser (T10 P5), a client that
        # goes away cancels its run, however the disconnect is noticed.
        try:
            yield _sse("run_started", {"run_id": run_id, "status": "running"})
            async for sse_event, sse_data in started.events(keepalive=15.0):
                if await request.is_disconnected():
                    return
                if sse_event == "keepalive":
                    # Keepalive comment to prevent proxy/browser timeout
                    yield ": keepalive\n\n"
                    continue
                yield _sse(sse_event, sse_data)
        finally:
            if not started.done:
                started.cancel()
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

    Items count toward the caller's concurrent-run limit like any other
    run: each waits for a free slot, and one slot is always left for runs
    the caller starts elsewhere, so fewer than ``max_parallel`` items may
    run at once.
    """
    wf = await _get_accessible_workflow(workflow_id, current_user.id, db)

    from fim_one.core.workflow.run_service import (
        RunMode,
        RunRefusedError,
        get_run_service,
    )

    service = get_run_service()
    # Checked once for the whole batch, which counts as one start against
    # runs-per-minute; each item then runs as the caller.
    try:
        authorized = await service.authorize(
            wf, current_user.id, RunMode.BATCH, admit_batch=True
        )
    except RunRefusedError as exc:
        raise _refused(exc) from None

    batch_id = str(uuid.uuid4())
    semaphore = asyncio.Semaphore(body.max_parallel)

    async def _run_single(index: int, inputs: dict[str, Any]) -> BatchRunResultItem:
        """Execute one workflow run within the batch."""
        async with semaphore:
            try:
                outcome = await service.run_to_completion(
                    wf,
                    caller_id=current_user.id,
                    inputs=inputs,
                    mode=RunMode.BATCH,
                    authorized=authorized,
                )
            except Exception as exc:
                logger.exception("Batch run %s item %d failed", batch_id, index)
                return BatchRunResultItem(
                    run_id="",
                    inputs=inputs,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                    duration_ms=0,
                )
        return BatchRunResultItem(
            run_id=outcome.run_id,
            inputs=inputs,
            status=outcome.status,
            outputs=outcome.outputs or None,
            error=outcome.error,
            duration_ms=outcome.duration_ms,
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

    # Validate against SSRF before making the request
    _validate_webhook_url(webhook_url)

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
        async with get_safe_async_client(timeout=10.0) as client:
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
    except ValueError:
        # Raised by the SSRF transport; its message names the resolved IP,
        # which must not be reported back to the caller.
        return ApiResponse(
            data={
                "success": False,
                "status_code": None,
                "webhook_url": webhook_url,
                "error": "Webhook URL must not point to a private or internal address",
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
# Test Node (single-node isolated execution)
# ---------------------------------------------------------------------------

@router.post("/{workflow_id}/test-node", response_model=ApiResponse)
async def test_node(
    workflow_id: str,
    body: NodeTestRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Test a single workflow node in isolation with mock variable inputs.

    Executes the specified node without creating a WorkflowRun record.
    Useful for debugging individual nodes during workflow development.
    """
    from fim_one.core.workflow.nodes import get_executor
    from fim_one.core.workflow.types import NodeType

    # Node types that cannot be meaningfully tested in isolation
    non_testable = frozenset({NodeType.START, NodeType.END})

    wf = await _get_owned_workflow(workflow_id, current_user.id, db)

    if not wf.blueprint or not wf.blueprint.get("nodes"):
        raise AppError("blueprint_empty", status_code=400)

    # Find the target node in the blueprint
    raw_nodes = wf.blueprint.get("nodes", [])
    target_node_raw = None
    for n in raw_nodes:
        if n.get("id") == body.node_id:
            target_node_raw = n
            break

    if target_node_raw is None:
        raise AppError(
            "node_not_found",
            status_code=404,
            detail=f"Node '{body.node_id}' not found in workflow blueprint",
        )

    # Parse node type
    node_data = target_node_raw.get("data", {}) or {}

    from fim_one.core.workflow.parser import (
        BlueprintValidationError,
        parse_error_strategy,
        parse_timeout_ms,
        resolve_node_type,
    )
    from fim_one.core.workflow.types import WorkflowNodeDef

    try:
        node_type = resolve_node_type(target_node_raw)
        error_strategy = parse_error_strategy(node_data.get("error_strategy"))
    except BlueprintValidationError as exc:
        raise AppError(f"invalid_node_type: {exc}", status_code=400) from None

    # Reject non-testable node types
    if node_type in non_testable:
        raise AppError(
            "node_not_testable",
            status_code=400,
            detail=f"Node type '{node_type.value}' cannot be tested in isolation",
        )

    # Build a WorkflowNodeDef from raw data
    timeout_ms = parse_timeout_ms(node_data.get("timeout_ms"), node_type)

    node_def = WorkflowNodeDef(
        id=body.node_id,
        type=node_type,
        data=node_data,
        position=target_node_raw.get("position", {}),
        error_strategy=error_strategy,
        timeout_ms=timeout_ms,
    )

    try:
        get_executor(node_type)
    except ValueError as exc:
        raise AppError(f"executor_not_found: {exc}", status_code=400) from None

    from fim_one.core.workflow.run_service import RunRefusedError, get_run_service

    # Same principal, quota, plan and env rules as a full run; the tokens a
    # model-backed node uses count toward the tester's quota.
    try:
        result, snapshot = await get_run_service().test_node(
            wf,
            caller_id=current_user.id,
            node=node_def,
            variables=body.variables,
            env_overrides=body.env_vars,
        )
    except RunRefusedError as exc:
        raise _refused(exc) from None

    return ApiResponse(
        data=NodeTestResponse(
            node_id=body.node_id,
            node_type=node_type.value,
            status=result.status.value,
            output=result.output,
            error=result.error,
            duration_ms=result.duration_ms,
            variables_after=snapshot,
        ).model_dump()
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
    declared output variables with name and type. Author only: the map
    lists every node of the blueprint, which other users are not shown.
    """
    wf = await _get_owned_workflow(workflow_id, current_user.id, db)

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
    """List all versions for a workflow, newest first. Author only."""
    await _get_version_owner_workflow(workflow_id, current_user.id, db)

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
    """Get a specific workflow version by ID. Author only."""
    await _get_version_owner_workflow(workflow_id, current_user.id, db)

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
    ensure_nodes_enabled(ver.blueprint)
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
    wf_result = await db.execute(select(Workflow).where(Workflow.id == wf.id))
    wf = wf_result.scalar_one()
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

    base = select(WorkflowRun).where(
        WorkflowRun.workflow_id == workflow_id,
        _own_runs_clause(current_user.id),
    )
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
    wf = await _get_accessible_workflow(workflow_id, current_user.id, db)
    scope = _run_stats_clauses(wf, current_user.id)

    base = select(WorkflowRun).where(*scope)

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
                *scope,
                WorkflowRun.status == status_val,
            )
        )
        status_counts[status_val] = count_result.scalar_one()

    # Average duration of completed runs
    avg_result = await db.execute(
        select(func.avg(WorkflowRun.duration_ms)).where(
            *scope,
            WorkflowRun.status == "completed",
            WorkflowRun.duration_ms.isnot(None),
        )
    )
    avg_duration = avg_result.scalar_one()

    # Last run timestamp
    last_result = await db.execute(
        select(WorkflowRun.created_at)
        .where(*scope)
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
    wf = await _get_accessible_workflow(workflow_id, current_user.id, db)

    # Fetch recent runs that have node_results. Only status and duration are
    # read from them, so the author may aggregate over every run.
    result = await db.execute(
        select(WorkflowRun.node_results)
        .where(
            *_run_stats_clauses(wf, current_user.id),
            WorkflowRun.node_results.isnot(None),
        )
        .order_by(WorkflowRun.created_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()

    # Aggregate per-node stats
    node_stats: dict[str, dict[str, Any]] = {}
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
    wf = await _get_accessible_workflow(workflow_id, current_user.id, db)

    cutoff = datetime.now(UTC) - timedelta(days=days)
    base_filter = [
        *_run_stats_clauses(wf, current_user.id),
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


@router.get("/{workflow_id}/runs/export")
async def export_workflow_runs(
    workflow_id: str,
    status: str | None = Query(None, pattern="^(pending|running|completed|failed|cancelled)$"),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> Response:
    """Export workflow run history as a JSON file.

    Useful for analytics, debugging, and compliance.  Returns up to
    ``limit`` runs ordered by most recent first, optionally filtered by
    ``status``.
    """
    wf = await _get_accessible_workflow(workflow_id, current_user.id, db)

    base = select(WorkflowRun).where(
        WorkflowRun.workflow_id == workflow_id,
        _own_runs_clause(current_user.id),
    )
    if status:
        base = base.where(WorkflowRun.status == status)

    result = await db.execute(
        base.order_by(WorkflowRun.created_at.desc()).limit(limit)
    )
    runs = result.scalars().all()

    export_runs = []
    for r in runs:
        resp = _run_to_response(r)
        export_runs.append({
            "id": resp.id,
            "status": resp.status,
            "inputs": resp.inputs,
            "outputs": resp.outputs,
            "error": resp.error,
            "started_at": resp.started_at,
            "completed_at": resp.completed_at,
            "duration_ms": resp.duration_ms,
            "node_results": resp.node_results,
        })

    payload = {
        "workflow_id": wf.id,
        "workflow_name": wf.name,
        "exported_at": datetime.now(UTC).isoformat(),
        "total_runs": len(export_runs),
        "runs": export_runs,
    }

    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
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
            _own_runs_clause(current_user.id),
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
    # The runner may cancel their own run; the author may cancel any run of
    # the workflow. The run must belong to this workflow either way.
    wf = await _get_accessible_workflow(workflow_id, current_user.id, db)
    result = await db.execute(
        select(WorkflowRun).where(
            WorkflowRun.id == run_id,
            WorkflowRun.workflow_id == workflow_id,
        )
    )
    run = result.scalar_one_or_none()
    if run is None or (
        run.user_id != current_user.id and wf.user_id != current_user.id
    ):
        raise AppError("workflow_run_not_found", status_code=404)

    from fim_one.core.workflow.run_service import get_run_service

    if get_run_service().cancel_run(run_id):
        return ApiResponse(data={"cancelled": True, "run_id": run_id})

    if run.status in ("pending", "running"):
        run.status = "cancelled"
        await db.commit()

    return ApiResponse(data={"cancelled": True, "run_id": run_id})


@router.get("/{workflow_id}/runs/{run_id}/approvals", response_model=ApiResponse)
async def list_run_approvals(
    workflow_id: str,
    run_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """List all approvals for a specific workflow run."""
    # Verify access to the workflow
    await _get_accessible_workflow(workflow_id, current_user.id, db)

    # Verify run exists
    run_result = await db.execute(
        select(WorkflowRun).where(
            WorkflowRun.id == run_id,
            WorkflowRun.workflow_id == workflow_id,
        )
    )
    run = run_result.scalar_one_or_none()
    if run is None:
        raise AppError("workflow_run_not_found", status_code=404)

    # Same rule as deciding: someone else's run shows only the approvals
    # assigned to the current user.
    stmt = select(ConfirmationRequest).where(
        ConfirmationRequest.workflow_run_id == run_id,
        ConfirmationRequest.kind == APPROVAL_KIND,
    )
    if run.user_id != current_user.id:
        stmt = stmt.where(
            ConfirmationRequest.eligible_scope == "assignee",
            ConfirmationRequest.eligible_user_id == current_user.id,
        )
    result = await db.execute(stmt.order_by(ConfirmationRequest.created_at.desc()))
    approvals = result.scalars().all()

    return ApiResponse(
        data=[_approval_to_response(a).model_dump() for a in approvals]
    )


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
            _own_runs_clause(current_user.id),
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise AppError("workflow_run_not_found", status_code=404)

    if run.status in ("pending", "running"):
        raise AppError("cannot_delete_active_run", status_code=409)

    await close_requests_of_runs(db, [run.id])
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

    finished = (
        WorkflowRun.workflow_id == workflow_id,
        _own_runs_clause(current_user.id),
        WorkflowRun.status.in_(["completed", "failed", "cancelled"]),
    )
    await close_requests_of_runs(db, select(WorkflowRun.id).where(*finished))
    del_result = cast(CursorResult[Any], await db.execute(delete(WorkflowRun).where(*finished)))
    await db.commit()
    return ApiResponse(data={"deleted_count": del_result.rowcount})


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
    Only the owner of the workflow can export it.
    """
    wf = await _get_accessible_workflow(workflow_id, current_user.id, db)
    if wf.user_id != current_user.id:
        raise AppError("export_denied", status_code=403, detail="Only the owner can export this resource")
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
    # Exports written before blueprint v2 are upgraded here, once.
    if not (data.blueprint or {}).get("nodes"):
        raise AppError("import_invalid_blueprint", status_code=400)
    raw_blueprint = normalize_blueprint(data.blueprint)
    await ensure_assignees_valid(db, raw_blueprint, user=current_user, org_id=None)

    # Parse the blueprint so the resolver can work with typed node definitions
    try:
        parsed_bp = parse_blueprint(raw_blueprint)
    except Exception:
        raise AppError("import_invalid_blueprint", status_code=400) from None

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
        blueprint=raw_blueprint,
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


@router.get("/{workflow_id}/env", response_model=ApiResponse)
async def get_workflow_env(
    workflow_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Return the env-var key names (not values) for a workflow."""
    wf = await _get_owned_workflow(workflow_id, current_user.id, db)

    from fim_one.core.workflow.run_service import decrypt_workflow_env

    keys = list(decrypt_workflow_env(wf).keys())

    return ApiResponse(data={"keys": keys})


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
    from fim_one.web.services.workflow_trigger_keys import generate_trigger_key

    wf = await _get_owned_workflow(workflow_id, current_user.id, db)

    # wf_ prefix + 43-char token. Only the hash and a display prefix are
    # stored; the plain key is returned this once.
    new_key, key_hash, key_prefix = generate_trigger_key()
    wf.api_key_hash = key_hash
    wf.api_key_prefix = key_prefix
    await db.commit()
    logger.info(
        "Workflow %s trigger key %s generated by user %s",
        wf.id,
        key_prefix,
        current_user.id,
    )

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

    if not wf.api_key_hash:
        raise AppError("no_api_key", status_code=404, detail="Workflow has no API key")

    revoked_prefix = wf.api_key_prefix
    wf.api_key_hash = None
    wf.api_key_prefix = None
    await db.commit()
    logger.info(
        "Workflow %s trigger key %s revoked by user %s",
        wf.id,
        revoked_prefix,
        current_user.id,
    )

    return ApiResponse(data={"revoked": True, "workflow_id": wf.id})
