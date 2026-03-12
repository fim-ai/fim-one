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
from contextlib import asynccontextmanager
from pathlib import Path

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
from fastapi.responses import JSONResponse

from .exceptions import AppError
from .api.admin import SETTING_MAINTENANCE_MODE, get_setting, router as admin_router
from .api.admin_security import router as admin_security_router
from .api.admin_api_keys import router as admin_api_keys_router
from .api.admin_resources import router as admin_resources_router
from .api.admin_templates import router as admin_templates_router
from .api.admin_extra import router as admin_extra_router
from .api.agents import router as agents_router
from .api.auth import router as auth_router
from .api.chat import router as chat_router
from .api.oauth import router as oauth_router
from .api.agent_ai import router as agent_ai_router
from .api.connector_ai import router as connector_ai_router
from .api.builder import router as builder_router
from .api.connectors import router as connectors_router
from .api.organizations import router as organizations_router, admin_router as admin_organizations_router
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

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Startup / shutdown lifecycle for the FastAPI application.

    On startup the async database engine is initialised and tables are created.
    On shutdown the engine is disposed so connections are released cleanly.
    """
    from fim_one.db import init_db, shutdown_db

    await init_db()
    yield
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
        "🚀 FIM One v0.6 — Licensed under FIM One Source Available License\n"
        "Copyright 2024-2026 Beijing Feimu Network Technology Co., Ltd.",
        file=sys.stderr,
    )

    app = FastAPI(title="FIM One API", lifespan=lifespan)

    # -- X-Powered-By header ------------------------------------------------
    @app.middleware("http")
    async def add_powered_by_header(request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Powered-By"] = "FIM-One"
        return response

    # -- Maintenance mode middleware ----------------------------------------
    # Passes: OPTIONS (CORS preflight), /api/auth/* (login still works),
    #         /api/admin/* (admins can turn maintenance off), /api/system/*
    _MAINTENANCE_PASS = ("/api/auth/", "/api/admin/", "/api/system/")
    _maintenance_cache: list[tuple[bool, float] | None] = [None]
    _MAINTENANCE_TTL = 5.0

    @app.middleware("http")
    async def maintenance_mode_gate(request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in _MAINTENANCE_PASS) or request.method == "OPTIONS":
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
                content={"detail": "System is under maintenance. Please try again later."},
                headers={"Retry-After": "300"},
            )
        return await call_next(request)

    # -- Exception handlers -------------------------------------------------
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:  # noqa: ARG001
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "error_code": exc.error_code,
                "error_args": exc.detail_args,
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:  # noqa: ARG001
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "error_code": None,
                "error_args": {},
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:  # noqa: ARG001
        return JSONResponse(
            status_code=422,
            content={
                "detail": exc.errors(),
                "error_code": "validation_error",
                "error_args": {"errors": exc.errors()},
            },
        )

    # -- CORS ---------------------------------------------------------------
    # Do NOT include "*" alongside allow_credentials=True — browsers reject
    # credentialed requests to wildcard origins (and it exposes all endpoints
    # to any origin).  List explicit allowed origins only; operators can extend
    # via the CORS_ORIGINS env var (comma-separated).
    _extra_origins = [
        o.strip()
        for o in os.environ.get("CORS_ORIGINS", "").split(",")
        if o.strip()
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
    app.include_router(db_connectors_router)
    app.include_router(connector_ai_router)
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

    # NOTE: /uploads is intentionally NOT mounted as a public StaticFiles route.
    # File downloads are served through the authenticated API endpoint at
    # GET /api/files/{file_id}/download, which enforces access control.
    uploads_dir = Path(os.environ.get("UPLOADS_DIR", "uploads"))
    uploads_dir.mkdir(parents=True, exist_ok=True)

    logger.info("FIM One API application created")
    return app
