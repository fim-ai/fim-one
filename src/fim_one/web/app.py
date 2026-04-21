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

import json

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from fim_one import __version__

from .exceptions import AppError
from .api.admin import SETTING_MAINTENANCE_MODE, router as admin_router
from .api.admin_utils import get_setting
from .api.admin_security import router as admin_security_router
from .api.admin_api_keys import router as admin_api_keys_router
from .api.admin_resources import router as admin_resources_router
from .api.admin_templates import router as admin_templates_router
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

logger = logging.getLogger(__name__)


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

    from fim_one.db import init_db, shutdown_db
    from fim_one.core.model import close_shared_http_client
    from fim_one.core.workflow.scheduler import WorkflowScheduler
    from fim_one.core.workflow.run_cleanup import WorkflowRunCleaner
    from fim_one.core.channels.expiry import ConfirmationRequestExpirer
    from fim_one.web.confirmation_sse import register_confirmation_bridge

    await init_db()

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

    yield

    # Graceful shutdown: stop sweepers, scheduler, then dispose the DB
    confirmation_expirer_task.cancel()
    try:
        await confirmation_expirer_task
    except asyncio.CancelledError:
        pass

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    await scheduler.stop()
    try:
        await asyncio.wait_for(scheduler_task, timeout=35.0)
    except asyncio.TimeoutError:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
    except asyncio.CancelledError:
        pass
    await close_shared_http_client()
    await shutdown_db()


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
        "Copyright 2024-2026 Beijing Feimu Network Technology Co., Ltd.",
        file=sys.stderr,
    )

    app = FastAPI(
        title="FIM One API",
        description="AI-Powered Connector Hub — programmatic access to agents, conversations, knowledge bases, and more.",
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
    _MAINTENANCE_PASS = ("/api/auth/", "/api/admin/", "/api/system/", "/api/version")
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

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:  # noqa: ARG001
        def _safe_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
            """Stringify any non-serializable values (e.g. Pydantic v2 ctx exception objects)."""
            safe = []
            for err in errors:
                err = dict(err)
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
    )

    # -- Routers ------------------------------------------------------------
    app.include_router(admin_router)
    app.include_router(admin_security_router)
    app.include_router(admin_api_keys_router)
    app.include_router(admin_resources_router)
    app.include_router(admin_templates_router)
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

    # ---------------------------------------------------------------------------
    # OpenAPI: hide all routes, whitelist public API endpoints
    # ---------------------------------------------------------------------------
    _PUBLIC_API = {
        ("POST", "/api/react"),
        ("POST", "/api/dag"),
        ("POST", "/api/chat/inject"),
        ("GET", "/api/conversations"),
        ("POST", "/api/conversations"),
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
    }

    for route in app.routes:
        if not hasattr(route, "include_in_schema"):
            continue
        route.include_in_schema = False
        if hasattr(route, "methods") and hasattr(route, "path"):
            for method in route.methods:
                if (method, route.path) in _PUBLIC_API:
                    route.include_in_schema = True
                    break

    # ---------------------------------------------------------------------------
    # OpenAPI: custom schema with security scheme and server placeholder
    # ---------------------------------------------------------------------------
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
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
