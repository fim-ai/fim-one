"""FastAPI application factory for the FIM One web layer.

Usage::

    from fim_one.web import create_app

    app = create_app()
"""

from __future__ import annotations

__fim_license__ = "FIM-SAL-1.1"
__fim_origin__ = "https://github.com/fim-ai/fim-one"

import logging
import os
import sys
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# ── Configure root logger BEFORE any getLogger() calls ──────────────
_log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# At INFO the HTTP clients log each request with its full URL, and the MCP
# SDK logs failed connections with theirs: user-info, query string and
# token-shaped path segments are cut before any handler sees them. Here, so
# that every uvicorn worker has it from its first record.
from fim_one.core.security.log_secrets import install_url_secret_log_filter

install_url_secret_log_filter()

import json

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from fim_one import __version__
from fim_one.core.assembly.errors import AssemblyRefusal

from .exceptions import AppError
from .api.admin import SETTING_MAINTENANCE_MODE, router as admin_router
from .api.admin_utils import get_setting
from .api.admin_security import router as admin_security_router
from .api.admin_api_keys import router as admin_api_keys_router
from .api.admin_resources import router as admin_resources_router
from .api.admin_sensitive_words import router as admin_sensitive_words_router
from .api.admin_extra import router as admin_extra_router
from .api.admin_workflows import router as admin_workflows_router
from .api.admin_skills import router as admin_skills_router
from .api.admin_eval import router as admin_eval_router
from .api.admin_credentials import router as admin_credentials_router
from .api.admin_reviews import router as admin_reviews_router
from .api.admin_schedules import router as admin_schedules_router
from .api.admin_analytics import router as admin_analytics_router
from .api.admin_resources_overview import router as admin_resources_overview_router
from .api.admin_notifications import router as admin_notifications_router
from .api.admin_batch import router as admin_batch_router
from .api.admin_market import router as admin_market_router
from .api.agents import router as agents_router
from .api.auth import router as auth_router
from .api.billing import router as billing_router
from .api.chat import router as chat_router
from .api.oauth import router as oauth_router
from .api.agent_ai import router as agent_ai_router
from .api.connector_ai import router as connector_ai_router
from .api.builder import router as builder_router
from .api.connectors import router as connectors_router
from .api.organizations import (
    router as organizations_router,
    admin_router as admin_organizations_router,
)
from .api.reviews import router as reviews_router
from .api.db_connectors import router as db_connectors_router
from .api.conversations import router as conversations_router
from .api.export import router as export_router
from .api.mcp_servers import router as mcp_servers_router
from .api.artifacts import router as artifacts_router, global_artifacts_router
from .api.files import router as files_router
from .api.knowledge_bases import router as kb_router
from .api.models import router as models_router
from .api.tools import router as tools_router
from .api.eval_datasets import router as eval_datasets_router
from .api.eval_runs import router as eval_runs_router
from .api.dashboard import router as dashboard_router
from .api.market import router as market_router
from .api.skills import router as skills_router
from .api.skill_templates import router as skill_templates_router
from .api.workflows import router as workflows_router
from .api.workflow_templates import (
    router as workflow_templates_router,
    admin_router as admin_workflow_templates_router,
)
from .api.agent_templates import router as agent_templates_router
from .api.connector_templates import router as connector_templates_router
from .api.channels import router as channels_router
from .api.confirmations import router as confirmations_router
from .api.hooks import router as hooks_router
from .api.metrics import router as metrics_router
from .api.notifications import router as notifications_router
from .api.user_settings import router as user_settings_router
from .api.version import router as version_router
from .api.webhooks import router as webhooks_router

logger = logging.getLogger(__name__)

# Endpoints published in the OpenAPI schema; every other route is hidden.
_PUBLIC_API: frozenset[tuple[str, str]] = frozenset({
    ("POST", "/api/react"),
    ("POST", "/api/dag"),
    ("POST", "/api/chat/inject"),
    ("GET", "/api/conversations"),
    ("POST", "/api/conversations"),
    ("GET", "/api/conversations/latest"),
    ("GET", "/api/conversations/{conversation_id}"),
    ("PATCH", "/api/conversations/{conversation_id}"),
    ("DELETE", "/api/conversations/{conversation_id}"),
    ("GET", "/api/agents"),
    ("GET", "/api/agents/{agent_id}"),
    ("GET", "/api/knowledge-bases"),
    ("POST", "/api/knowledge-bases/{kb_id}/retrieve"),
    ("GET", "/api/me/api-keys"),
    ("POST", "/api/me/api-keys"),
    ("DELETE", "/api/me/api-keys/{key_id}"),
    ("PATCH", "/api/me/api-keys/{key_id}/active"),
})

_SCHEMA_REF_PREFIX = "#/components/schemas/"


def _collect_schema_refs(node: Any, found: set[str]) -> None:
    """Add every ``#/components/schemas/<name>`` referenced under *node*."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(_SCHEMA_REF_PREFIX):
            found.add(ref[len(_SCHEMA_REF_PREFIX):])
        for value in node.values():
            _collect_schema_refs(value, found)
    elif isinstance(node, list):
        for item in node:
            _collect_schema_refs(item, found)


def filter_openapi_to_public(
    schema: dict[str, Any], public: frozenset[tuple[str, str]]
) -> dict[str, Any]:
    """Keep only *public* operations and the component schemas they reach.

    Filtering the generated schema, rather than flipping
    ``include_in_schema`` on ``app.routes``, keeps working when FastAPI
    nests included routers: since 0.16x ``app.routes`` holds one wrapper
    per ``include_router`` call, so a loop over it hid only the few
    top-level routes and published every internal endpoint.
    """
    paths: dict[str, Any] = {}
    for path, operations in schema.get("paths", {}).items():
        kept = {
            method: op
            for method, op in operations.items()
            if (method.upper(), path) in public
        }
        if kept:
            paths[path] = kept
    schema["paths"] = paths

    components = schema.get("components", {})
    all_schemas: dict[str, Any] = components.get("schemas", {})
    reachable: set[str] = set()
    _collect_schema_refs(paths, reachable)
    pending = list(reachable)
    while pending:
        nested: set[str] = set()
        _collect_schema_refs(all_schemas.get(pending.pop(), {}), nested)
        for name in nested - reachable:
            reachable.add(name)
            pending.append(name)
    if all_schemas:
        components["schemas"] = {
            name: body for name, body in all_schemas.items() if name in reachable
        }
    return schema


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # noqa: ARG001
    """Startup / shutdown lifecycle for the FastAPI application.

    On startup the async database engine is initialised and tables are created.
    The workflow scheduler is started as a background task so that cron-based
    workflows are triggered automatically.
    On shutdown the scheduler is stopped and the engine is disposed so
    connections are released cleanly.
    """
    import asyncio

    # Each worker process: its /proc entries (environment, memory) are not
    # for the commands it starts to read. No-op outside Linux.
    from fim_one.core.security.host_access import harden_process

    harden_process()
    # The sandbox proxy's token: out of the environment before anything is
    # started that would inherit it.
    from fim_one.core.tool.sandbox import take_proxy_token

    take_proxy_token()

    from fim_one.db import init_db, shutdown_db
    from fim_one.core.model import close_shared_http_client
    from fim_one.core.workflow.scheduler import WorkflowScheduler
    from fim_one.core.workflow.run_cleanup import WorkflowRunCleaner
    from fim_one.core.channels.expiry import ConfirmationRequestExpirer
    from fim_one.web.confirmation_sse import register_confirmation_bridge

    await init_db()

    # Config sanity check (non-fatal): the fast tool/compaction model must
    # be able to hold a message list compacted against the general input
    # budget, or long conversations overflow it with HTTP 400s.
    from fim_one.db import create_session
    from fim_one.web.deps import warn_on_context_budget_mismatch

    try:
        _budget_db = create_session()
        try:
            await warn_on_context_budget_mismatch(_budget_db)
        finally:
            await _budget_db.close()
    except Exception:
        logger.exception("Context budget sanity check failed (non-fatal)")

    # The assembly layer's ports (quota numbers, agent runtime). Registered
    # in create_app(); checked here so a process that would refuse every
    # turn does not come up at all.
    from fim_one.core.assembly.quota import get_quota_backend
    from fim_one.core.assembly.runtime import get_agent_runtime_factory

    get_quota_backend()
    get_agent_runtime_factory()

    # Bridge the core ``emit_inline_confirmation`` hook to the chat SSE
    # stream.  Must run before any agent turn — any tool flagged
    # requires_confirmation would otherwise skip the inline push.
    register_confirmation_bridge()

    # Start the workflow scheduler daemon
    scheduler = WorkflowScheduler()
    scheduler_task = asyncio.create_task(scheduler.run())
    logger.info("Workflow scheduler background task created")

    # Start periodic workflow run cleanup
    cleaner = WorkflowRunCleaner(
        max_age_days=int(os.getenv("WORKFLOW_RUN_MAX_AGE_DAYS", "30")),
        max_runs_per_workflow=int(os.getenv("WORKFLOW_RUN_MAX_PER_WORKFLOW", "100")),
        cleanup_interval_hours=int(
            os.getenv("WORKFLOW_RUN_CLEANUP_INTERVAL_HOURS", "24")
        ),
    )
    cleanup_task = asyncio.create_task(cleaner.run_loop())

    # Audit records older than their retention period go once a day
    # (AUDIT_RETENTION_DAYS, CALL_LOG_RETENTION_DAYS; 0 keeps them for ever).
    from fim_one.core.audit.retention import AuditRetentionSweeper

    audit_retention_task = asyncio.create_task(AuditRetentionSweeper().run_loop())

    # Workflow runs: continue a suspended run when its request is decided,
    # stamp the runs this process executes, and recover what a dead process
    # or a missed decision left behind (sweep at startup and periodically).
    from fim_one.core.workflow.run_recovery import (
        WorkflowRunSupervisor,
        install_resume_on_decision,
    )

    remove_resume_hook = install_resume_on_decision()
    run_supervisor = WorkflowRunSupervisor.from_env()
    run_supervisor_task = asyncio.create_task(run_supervisor.run_loop())

    # Start periodic expiry sweep for stale confirmation requests.
    confirmation_expirer = ConfirmationRequestExpirer(
        max_age_minutes=int(
            os.getenv("CHANNEL_CONFIRMATION_TTL_MINUTES", "1440")
        ),
        sweep_interval_seconds=int(
            os.getenv("CHANNEL_CONFIRMATION_SWEEP_INTERVAL_SECONDS", "600")
        ),
    )
    confirmation_expirer_task = asyncio.create_task(
        confirmation_expirer.run_loop()
    )

    # Start the billing-subscription lifecycle sweep. Demotes users with
    # canceled subscriptions to Free at ``current_period_end`` so the
    # webhook handler can keep a clean ledger without flipping plan_id
    # the moment the user cancels.
    from fim_one.web.services.subscription_lifecycle import (
        start_lifecycle_loop,
    )

    subscription_lifecycle_task = start_lifecycle_loop()
    logger.info("Subscription lifecycle background task created")

    yield

    # Graceful shutdown: stop sweepers, scheduler, then dispose the DB.
    # Each wait is bounded: a loop that does not end is logged and left,
    # and the shutdown goes on (core.utils.stop_background).
    from fim_one.core.tool.sandbox.docker_backend import finish_cleanups
    from fim_one.core.utils import stop_background

    # First: sandbox containers whose run ended early and whose removal is
    # still under way are not left behind by the exit. Done before the other
    # stops, which can take their full bounds, so that a stop signal's grace
    # period is not spent before these removals get their turn.
    await finish_cleanups(timeout=10)

    await stop_background(
        subscription_lifecycle_task, timeout=10, name="Subscription lifecycle loop"
    )
    await stop_background(
        confirmation_expirer_task, timeout=10, name="Confirmation request expirer"
    )

    remove_resume_hook()
    # Asked, not cancelled: a cancel that interrupts a database call can
    # leave a task behind that keeps the process from exiting (SQLite).
    # Cancelled only if the pass it is in does not end in time.
    run_supervisor.stop()
    if not await stop_background(
        run_supervisor_task, timeout=15, name="Workflow run supervisor", cancel=False
    ):
        await stop_background(run_supervisor_task, timeout=5, name="Workflow run supervisor")

    await stop_background(cleanup_task, timeout=10, name="Workflow run cleaner")
    await stop_background(audit_retention_task, timeout=10, name="Audit retention sweeper")

    await scheduler.stop()
    if not await stop_background(
        scheduler_task, timeout=35, name="Workflow scheduler", cancel=False
    ):
        await stop_background(scheduler_task, timeout=5, name="Workflow scheduler")
    # Removals that the stops above set off (runs they cut short); returns
    # at once when there are none.
    await finish_cleanups(timeout=5)
    await close_shared_http_client()
    # Usage metered and not yet written (turns and runs cut short by the
    # shutdown, background writes in flight) goes to the ledger first.
    from fim_one.core.usage.meter import flush_all as flush_usage

    await flush_usage()
    await shutdown_db()


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:  # noqa: ARG001
    """422 with the location and message of each error.

    The submitted value (``input``) is not echoed back: it may be a password,
    and it may hold characters that cannot be encoded as JSON text.
    """

    def _safe_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
        """Stringify any non-serializable values (e.g. Pydantic v2 ctx exception objects)."""
        safe = []
        for err in errors:
            err = dict(err)
            err.pop("input", None)
            if "ctx" in err and isinstance(err["ctx"], dict):
                err["ctx"] = {
                    k: str(v) if isinstance(v, Exception) else v
                    for k, v in err["ctx"].items()
                }
            safe.append(err)
        return safe

    errs = _safe_errors(exc.errors())
    return JSONResponse(
        status_code=422,
        content={
            "detail": errs,
            "error_code": "validation_error",
            "error_args": {"errors": errs},
        },
    )


def create_app() -> FastAPI:
    """Create and configure a :class:`FastAPI` application.

    The returned app includes:

    * Database lifecycle management via the ``lifespan`` context manager.
    * CORS middleware allowing explicit localhost origins (Next.js dev server)
      plus any extra origins supplied via the ``CORS_ORIGINS`` env var.
      ``allow_credentials=True`` requires explicit origins — wildcard (``*``)
      is intentionally excluded.
    * The ``/api/react`` and ``/api/dag`` SSE chat endpoints from
      :mod:`fim_one.web.api.chat`.
    * Auth, conversation, and agent CRUD routers.
    * File downloads served through the authenticated ``/api/files`` router
      (no unauthenticated static-file mount).

    Returns
    -------
    FastAPI
        A fully-configured FastAPI instance ready to be served by Uvicorn.
    """
    # -- License notice (stderr — cannot be silenced by log config) ----------
    print(
        "🚀 FIM One — Licensed under FIM One Source Available License\n"
        "Copyright 2024-2026 FIM Labs Pte. Ltd.",
        file=sys.stderr,
    )

    from fim_one.web.services.assembly_ports import register_assembly_ports

    register_assembly_ports()

    app = FastAPI(
        title="FIM One API",
        description="All-in-one agent platform for Global × China enterprises — programmatic access to agents, conversations, knowledge bases, and more.",
        version=__version__,
        lifespan=lifespan,
    )

    # -- X-Powered-By header ------------------------------------------------
    @app.middleware("http")
    async def add_powered_by_header(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response: Response = await call_next(request)
        response.headers["X-Powered-By"] = "FIM-One"
        return response

    # -- Maintenance mode middleware ----------------------------------------
    # Passes: OPTIONS (CORS preflight), /api/auth/* (login still works),
    #         /api/admin/* (admins can turn maintenance off), /api/system/*
    _MAINTENANCE_PASS = (
        "/api/auth/",
        "/api/admin/",
        "/api/system/",
        "/api/version",
        "/api/webhooks/",
    )
    _maintenance_cache: list[tuple[bool, float] | None] = [None]
    _MAINTENANCE_TTL = 5.0

    @app.middleware("http")
    async def maintenance_mode_gate(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        path = request.url.path
        if (
            any(path.startswith(p) for p in _MAINTENANCE_PASS)
            or request.method == "OPTIONS"
        ):
            return await call_next(request)

        now = time.monotonic()
        cached = _maintenance_cache[0]
        if cached is not None and now - cached[1] < _MAINTENANCE_TTL:
            is_maintenance = cached[0]
        else:
            from fim_one.db import create_session

            async with create_session() as db:
                value = await get_setting(db, SETTING_MAINTENANCE_MODE, default="false")
            is_maintenance = value.lower() == "true"
            _maintenance_cache[0] = (is_maintenance, now)

        if is_maintenance:
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "System is under maintenance. Please try again later."
                },
                headers={"Retry-After": "300"},
            )
        return await call_next(request)

    # -- IP allow/deny rules (admin Security → IP Rules) --------------------
    # Registered after maintenance so it runs first: a blocked client never
    # reaches any other middleware.
    from fim_one.web.ip_rules import (
        RuleCache,
        enforcement_enabled,
        is_client_allowed,
        path_is_exempt,
    )

    from fim_one.core.audit import actions as audit_actions
    from fim_one.core.audit.refusals import record_refusal, safe_path

    _ip_rule_cache = RuleCache()

    @app.middleware("http")
    async def ip_rule_gate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not enforcement_enabled() or path_is_exempt(request.url.path, request.method):
            return await call_next(request)
        allow, deny = await _ip_rule_cache.get()
        client_ip = request.client.host if request.client else None
        if not is_client_allowed(client_ip, allow, deny):
            # One record per address and ten minutes: a scan must not fill
            # the audit table. The address itself is on the record from the
            # request context.
            await record_refusal(
                audit_actions.SECURITY_IP_BLOCKED,
                source=client_ip,
                detail=f"{request.method} {safe_path(request.url.path)}",
            )
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Access from this IP address is not allowed.",
                    "error_code": "ip_not_allowed",
                    "error_args": {},
                },
            )
        return await call_next(request)

    # -- Where the request came from, for the audit writer -------------------
    # Added last, so it is outermost: every other middleware and every
    # endpoint runs with the request's address already set.
    from fim_one.web.request_meta import RequestMetaMiddleware

    app.add_middleware(RequestMetaMiddleware)

    # -- Exception handlers -------------------------------------------------
    @app.exception_handler(AppError)
    async def app_error_handler(
        request: Request, exc: AppError
    ) -> JSONResponse:  # noqa: ARG001
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "error_code": exc.error_code,
                "error_args": exc.detail_args,
            },
        )

    @app.exception_handler(AssemblyRefusal)
    async def assembly_refusal_handler(
        request: Request, exc: AssemblyRefusal
    ) -> JSONResponse:
        # A refusal from the assembly layer that an endpoint let propagate:
        # same body as an AppError with the same code.
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "error_code": exc.error_code,
                "error_args": exc.detail_args or {},
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:  # noqa: ARG001
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "error_code": None,
                "error_args": {},
            },
        )

    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]

    # -- CORS ---------------------------------------------------------------
    # Do NOT include "*" alongside allow_credentials=True — browsers reject
    # credentialed requests to wildcard origins (and it exposes all endpoints
    # to any origin).  List explicit allowed origins only; operators can extend
    # via the CORS_ORIGINS env var (comma-separated).
    _extra_origins = [
        o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:3001",
            *_extra_origins,
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Read by the admin page after an export, to say when it was cut short.
        expose_headers=["X-Export-Truncated", "X-Export-Rows", "X-Export-Total"],
    )

    # -- Routers ------------------------------------------------------------
    app.include_router(admin_router)
    app.include_router(admin_security_router)
    app.include_router(admin_api_keys_router)
    app.include_router(admin_resources_router)
    app.include_router(admin_sensitive_words_router)
    app.include_router(admin_extra_router)
    app.include_router(admin_workflows_router)
    app.include_router(admin_skills_router)
    app.include_router(admin_eval_router)
    app.include_router(admin_credentials_router)
    app.include_router(admin_reviews_router)
    app.include_router(admin_schedules_router)
    app.include_router(admin_analytics_router)
    app.include_router(admin_resources_overview_router)
    app.include_router(admin_notifications_router)
    app.include_router(admin_batch_router)
    app.include_router(admin_market_router)
    app.include_router(chat_router)
    app.include_router(auth_router)
    app.include_router(oauth_router)
    app.include_router(conversations_router)
    app.include_router(export_router)
    app.include_router(agents_router)
    app.include_router(builder_router)
    app.include_router(connectors_router)
    app.include_router(organizations_router)
    app.include_router(admin_organizations_router)
    app.include_router(reviews_router)
    app.include_router(db_connectors_router)
    app.include_router(connector_ai_router)
    app.include_router(connector_templates_router)
    app.include_router(mcp_servers_router)
    app.include_router(agent_ai_router)
    app.include_router(artifacts_router)
    app.include_router(global_artifacts_router)
    app.include_router(files_router)
    app.include_router(kb_router)
    app.include_router(models_router)
    app.include_router(tools_router)
    app.include_router(eval_datasets_router)
    app.include_router(eval_runs_router)
    app.include_router(dashboard_router)
    app.include_router(market_router)
    app.include_router(skills_router)
    app.include_router(skill_templates_router)
    app.include_router(workflows_router)
    app.include_router(workflow_templates_router)
    app.include_router(admin_workflow_templates_router)
    app.include_router(agent_templates_router)
    app.include_router(metrics_router)
    app.include_router(hooks_router)
    app.include_router(channels_router)
    app.include_router(confirmations_router)
    app.include_router(notifications_router)
    app.include_router(user_settings_router)
    app.include_router(version_router)
    app.include_router(billing_router)
    app.include_router(webhooks_router)

    # ---------------------------------------------------------------------------
    # OpenAPI: custom schema limited to _PUBLIC_API, with security scheme and
    # server placeholder
    # ---------------------------------------------------------------------------
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = filter_openapi_to_public(
            get_openapi(
                title=app.title,
                version=app.version,
                description=app.description,
                routes=app.routes,
            ),
            _PUBLIC_API,
        )
        # Add security scheme
        schema.setdefault("components", {})["securitySchemes"] = {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": "API Key (fim_xxx) or JWT token",
            }
        }
        schema["security"] = [{"BearerAuth": []}]
        # Add server placeholder
        schema["servers"] = [
            {"url": "https://your-domain.com", "description": "Your FIM One instance"}
        ]
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    # NOTE: /uploads is intentionally NOT mounted as a public StaticFiles route.
    # File downloads are served through the authenticated API endpoint at
    # GET /api/files/{file_id}/download, which enforces access control.
    uploads_dir = Path(os.environ.get("UPLOADS_DIR", "uploads"))
    uploads_dir.mkdir(parents=True, exist_ok=True)

    logger.info("FIM One API application created")
    return app
