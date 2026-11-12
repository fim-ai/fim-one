"""Which stdio MCP servers may be started, decided when one is about to be.

A stdio server is a command run inside the application container. The
write endpoints only let a system administrator configure one, but a row
can predate that rule, be written past the API, or belong to somebody who
has since stopped being an administrator. So the rule is applied again
here, at the point of use, for every way a server is opened (an agent turn,
a workflow MCP step, the step editor's tool listing, the test-connection
endpoint):

- stdio must be enabled on the instance (``ALLOW_STDIO_MCP``);
- the server's owner must be an active system administrator *now*.

A row that fails is left in the database untouched and is never started.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.core import security
from fim_one.core.security.mcp import (
    STDIO_MCP_DISABLED,
    STDIO_MCP_ENV_NOT_ALLOWED,
    STDIO_MCP_OWNER_NOT_ADMIN,
    stdio_env_violations,
)

from .errors import AssemblyRefusal
from .principal import Principal

__all__ = [
    "personal_env_refusal",
    "stdio_refusal",
    "stdio_refusals",
]

logger = logging.getLogger(__name__)


def _name(server: Any) -> str:
    return str(getattr(server, "name", None) or getattr(server, "id", ""))


def _disabled(server: Any) -> AssemblyRefusal:
    return AssemblyRefusal(
        STDIO_MCP_DISABLED,
        403,
        f"MCP server '{_name(server)}' runs a local command (stdio) and stdio MCP "
        "servers are disabled on this instance.",
        {"name": _name(server)},
    )


def _owner_not_admin(server: Any) -> AssemblyRefusal:
    return AssemblyRefusal(
        STDIO_MCP_OWNER_NOT_ADMIN,
        403,
        f"MCP server '{_name(server)}' runs a local command (stdio) and is not started: "
        "only a stdio server owned by an active system administrator may run. "
        "Ask an administrator to set it up, or delete it.",
        {"name": _name(server)},
    )


async def stdio_refusals(db: AsyncSession, servers: Sequence[Any]) -> dict[str, AssemblyRefusal]:
    """Why each stdio server among *servers* may not be started, by server id.

    A server that is not stdio, or that may be started, has no entry. One
    query, and only when there is a stdio server to decide about. Not
    knowing who the administrators are refuses every stdio server.
    """
    stdio = [s for s in servers if getattr(s, "transport", None) == "stdio"]
    if not stdio:
        return {}
    if not security.is_stdio_allowed():
        return {s.id: _disabled(s) for s in stdio}

    from fim_one.db.models.user import User

    owner_ids = {s.user_id for s in stdio if getattr(s, "user_id", None)}
    admins: set[str] = set()
    if owner_ids:
        try:
            rows = await db.execute(
                select(User.id).where(
                    User.id.in_(owner_ids),
                    User.is_admin == True,  # noqa: E712
                    User.is_active == True,  # noqa: E712
                )
            )
            admins = {row[0] for row in rows.all()}
        except Exception:
            logger.warning(
                "Could not look up the owners of %d stdio MCP server(s); none is started",
                len(stdio),
                exc_info=True,
            )
            admins = set()
    return {s.id: _owner_not_admin(s) for s in stdio if s.user_id not in admins}


async def stdio_refusal(db: AsyncSession, server: Any) -> AssemblyRefusal | None:
    """Why *server* may not be started, or ``None`` (always ``None`` when not stdio)."""
    return (await stdio_refusals(db, [server])).get(server.id)


def personal_env_refusal(
    principal: Principal, server: Any, env: dict[str, Any] | None
) -> AssemblyRefusal | None:
    """Why *principal* may not start the stdio *server* with their own *env*.

    The environment decides what a process loads (``PATH``, ``NODE_OPTIONS``,
    ``PYTHONPATH``, ``LD_PRELOAD``, …), so somebody who is not an
    administrator may only fill in the variables the server's owner
    declared, and none of the loader ones.
    """
    if getattr(server, "transport", None) != "stdio" or not env or principal.is_admin:
        return None
    refused = stdio_env_violations(getattr(server, "env", None) or {}, env)
    if not refused:
        return None
    return AssemblyRefusal(
        STDIO_MCP_ENV_NOT_ALLOWED,
        403,
        f"Your saved credentials for MCP server '{_name(server)}' set environment "
        f"variables its administrator did not declare ({', '.join(refused)}). "
        "Only the variables listed on the server can be set; update your credentials.",
        {"name": _name(server), "variables": ", ".join(refused)},
    )
