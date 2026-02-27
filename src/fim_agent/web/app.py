"""FastAPI application factory for the FIM Agent web layer.

Usage::

    from fim_agent.web import create_app

    app = create_app()
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.agents import router as agents_router
from .api.auth import router as auth_router
from .api.chat import router as chat_router
from .api.conversations import router as conversations_router
from .api.files import router as files_router
from .api.knowledge_bases import router as kb_router
from .api.models import router as models_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Startup / shutdown lifecycle for the FastAPI application.

    On startup the async database engine is initialised and tables are created.
    On shutdown the engine is disposed so connections are released cleanly.
    """
    from fim_agent.db import init_db, shutdown_db

    await init_db()
    yield
    await shutdown_db()


def create_app() -> FastAPI:
    """Create and configure a :class:`FastAPI` application.

    The returned app includes:

    * Database lifecycle management via the ``lifespan`` context manager.
    * CORS middleware allowing ``localhost:3000`` (Next.js dev server) and all
      origins (``*``) so that production front-ends work out of the box.
    * The ``/api/react`` and ``/api/dag`` SSE chat endpoints from
      :mod:`fim_agent.web.api.chat`.
    * Auth, conversation, and agent CRUD routers.
    * Static file serving for ``/uploads`` when the directory exists.

    Returns
    -------
    FastAPI
        A fully-configured FastAPI instance ready to be served by Uvicorn.
    """
    app = FastAPI(title="FIM Agent API", lifespan=lifespan)

    # -- CORS ---------------------------------------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:3001",
            "*",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Routers ------------------------------------------------------------
    app.include_router(chat_router)
    app.include_router(auth_router)
    app.include_router(conversations_router)
    app.include_router(agents_router)
    app.include_router(files_router)
    app.include_router(kb_router)
    app.include_router(models_router)

    # -- Static uploads -----------------------------------------------------
    uploads_dir = Path("uploads")
    if uploads_dir.is_dir():
        app.mount(
            "/uploads",
            StaticFiles(directory=str(uploads_dir)),
            name="uploads",
        )

    logger.info("FIM Agent API application created")
    return app
