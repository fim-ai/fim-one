"""Which credential a call goes out with.

One rule for API connectors and MCP servers, from every entry point (chat
with or without an agent, sub-agents, workflow nodes):

1. A connector whose owner declared that it takes no credential (auth type
   ``none``) is called with none. ``allow_fallback`` has nothing to gate
   there: there is nothing of the owner's to borrow.
2. The acting user's own credential, when they saved one that the resource
   can use, is used alone: it is never mixed with the owner's.
3. The resource's owner uses the credential stored on the resource.
4. Anyone else may borrow the owner's only when the owner switched
   ``allow_fallback`` on.
5. Otherwise the call is refused with ``credential_required``.

Step 1 has no MCP counterpart. An MCP server has no "takes no credential"
declaration, and an empty ``env`` and ``headers`` does not amount to one:
the URL and the command arguments are the owner's as well. So with
``allow_fallback`` off, a non-owner connects to an MCP server with a
credential of their own or not at all, whatever the server stores. A server
that stores nothing is opened with nothing by its owner and by those the
owner lends to (source ``none``).

"That the resource can use" matters for MCP servers. A server row carries
the owner's ``env`` (read by a stdio server) and ``headers`` (sent to an SSE
or streamable-HTTP server), and a personal credential may hold either. An
``env``-only credential on an HTTP server supplies nothing: counting it as
"has a credential" would send the owner's headers under the user's name.

Database connectors are a separate case: their tools run raw SQL on the
owner's database account with no per-caller scoping, so only the owner gets
the connection config (``owner_only``).

A lookup that fails is refused with ``credential_unavailable``; it never
falls through to the owner's credential.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from .errors import CREDENTIAL_REQUIRED, CREDENTIAL_UNAVAILABLE, OWNER_ONLY, AssemblyRefusal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from .principal import Principal

__all__ = [
    "CredentialSource",
    "ResolvedCredential",
    "must_bring_own_credential",
    "personal_credential_fits",
    "resolve_credential",
    "resolve_credentials",
]

logger = logging.getLogger(__name__)


class CredentialSource(StrEnum):
    """Whose credential a call goes out with."""

    PERSONAL = "personal"  # the acting user's own
    OWNER = "owner"  # the owner, using what is stored on the resource
    OWNER_FALLBACK = "owner_fallback"  # someone else, borrowing the owner's
    NONE = "none"  # nothing is sent


@dataclass(frozen=True)
class ResolvedCredential:
    """The secrets one call may use, and whose they are."""

    source: CredentialSource
    # API connector: the decrypted credential fields.
    connector: dict[str, Any] | None = None
    # Database connector (owner only): the decrypted connection config.
    db_config: dict[str, Any] | None = None
    # MCP server: what a stdio server is started with, and what an SSE or
    # streamable-HTTP server is sent.
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None


def _name(resource: Any) -> str:
    return str(getattr(resource, "name", None) or getattr(resource, "id", ""))


def _is_owner(principal: Principal, resource: Any) -> bool:
    return bool(principal.user_id) and getattr(resource, "user_id", None) == principal.user_id


def _credential_required(label: str, resource: Any, needs: str = "credentials") -> AssemblyRefusal:
    return AssemblyRefusal(
        CREDENTIAL_REQUIRED,
        403,
        f"{label} '{_name(resource)}' requires your own credentials (fallback to the "
        f"owner's is disabled). Save your own {needs} for it to use it.",
    )


def _credential_unavailable(label: str, resource: Any) -> AssemblyRefusal:
    return AssemblyRefusal(
        CREDENTIAL_UNAVAILABLE,
        503,
        f"The credentials for {label.lower()} '{_name(resource)}' could not be loaded. "
        "Try again shortly.",
    )


async def resolve_credential(
    db: AsyncSession, principal: Principal, resource: Any
) -> ResolvedCredential:
    """The credential *principal*'s call to *resource* goes out with.

    *resource* is a ``Connector`` or ``MCPServer`` row. Raises
    :class:`AssemblyRefusal` ``credential_required`` (403),
    ``credential_unavailable`` (503) or, for a database connector,
    ``owner_only`` (403).
    """
    (answer,) = await resolve_credentials(db, principal, [resource])
    if isinstance(answer, AssemblyRefusal):
        raise answer
    return answer


async def resolve_credentials(
    db: AsyncSession, principal: Principal, resources: Sequence[Any]
) -> list[ResolvedCredential | AssemblyRefusal]:
    """:func:`resolve_credential` for many resources: one answer each, in order.

    A refusal is returned, not raised, so that one resource the user may not
    call does not stop the rest. The stored credentials of all the API
    connectors are read in one query, and those of all the MCP servers in
    another, however many there are: a chat with no agent resolves every
    connector and server the user can see on every turn.
    """
    answers: dict[int, ResolvedCredential | AssemblyRefusal] = {}
    connectors: list[tuple[int, Any]] = []
    servers: list[tuple[int, Any]] = []
    for index, resource in enumerate(resources):
        if hasattr(resource, "transport"):
            servers.append((index, resource))
        elif getattr(resource, "type", None) == "database":
            try:
                answers[index] = _database_credential(principal, resource)
            except AssemblyRefusal as refusal:
                answers[index] = refusal
        else:
            connectors.append((index, resource))
    answers.update(await _connector_credentials(db, principal, connectors))
    answers.update(await _mcp_credentials(db, principal, servers))
    return [answers[index] for index in range(len(resources))]


def _database_credential(principal: Principal, conn: Any) -> ResolvedCredential:
    from fim_one.core.security.connector_credentials import db_raw_sql_tool_allowed
    from fim_one.core.security.encryption import decrypt_db_config

    if not db_raw_sql_tool_allowed(conn, principal.user_id):
        raise AssemblyRefusal(
            OWNER_ONLY, 403, f"Database connector '{_name(conn)}' can only be used by its owner"
        )
    return ResolvedCredential(
        source=CredentialSource.OWNER,
        db_config=decrypt_db_config(conn.db_config or {}),
    )


# Where a connector's secret lived before credentials had a table of their
# own. A save through the connector API moves it to the default credential
# row; a row written by a path that does not (or not yet re-saved) still
# carries it here.
_AUTH_CONFIG_SECRET_KEYS = (
    "default_token",
    "default_api_key",
    "default_username",
    "default_password",
)


def _secrets_left_in_auth_config(conn: Any) -> dict[str, Any]:
    """The owner's secret fields still stored in ``conn.auth_config``.

    They are the owner's stored credential like the default row is, and are
    handed out under the same rule. The tool adapter does not read them.
    """
    config = getattr(conn, "auth_config", None)
    if not isinstance(config, dict):
        return {}
    return {key: config[key] for key in _AUTH_CONFIG_SECRET_KEYS if config.get(key)}


def _read(ciphertext: str) -> dict[str, Any]:
    """A stored credential blob as the dict it holds."""
    from fim_one.core.security.encryption import decrypt_credential

    return decrypt_credential(ciphertext)


async def _connector_credentials(
    db: AsyncSession, principal: Principal, connectors: Sequence[tuple[int, Any]]
) -> dict[int, ResolvedCredential | AssemblyRefusal]:
    """Answers for API connectors, keyed by the index each came with."""
    answers: dict[int, ResolvedCredential | AssemblyRefusal] = {}
    needing: list[tuple[int, Any]] = []
    for index, conn in connectors:
        if (getattr(conn, "auth_type", None) or "none") == "none":
            # The connector sends no credential, so there is none to resolve
            # and none of the owner's to gate.
            answers[index] = ResolvedCredential(source=CredentialSource.NONE, connector={})
        else:
            needing.append((index, conn))
    if not needing:
        return answers

    from sqlalchemy import or_, select

    from fim_one.db.models.connector_credential import ConnectorCredential

    # connector id -> the caller's own row / the default (owner's) row
    personal_rows: dict[str, str] = {}
    default_rows: dict[str, str] = {}
    try:
        whose: Any = ConnectorCredential.user_id.is_(None)
        if principal.user_id:
            whose = or_(whose, ConnectorCredential.user_id == principal.user_id)
        rows = (
            await db.execute(
                select(
                    ConnectorCredential.connector_id,
                    ConnectorCredential.user_id,
                    ConnectorCredential.credentials_blob,
                ).where(ConnectorCredential.connector_id.in_([c.id for _, c in needing]), whose)
            )
        ).all()
        for connector_id, user_id, blob in rows:
            (default_rows if user_id is None else personal_rows)[connector_id] = blob
    except Exception:
        logger.warning(
            "Failed to load credentials for %d connector(s)", len(needing), exc_info=True
        )
        for index, conn in needing:
            answers[index] = _credential_unavailable("Connector", conn)
        return answers

    for index, conn in needing:
        try:
            answers[index] = _connector_answer(
                principal, conn, personal_rows.get(conn.id), default_rows.get(conn.id)
            )
        except AssemblyRefusal as refusal:
            answers[index] = refusal
    return answers


def _connector_answer(
    principal: Principal, conn: Any, personal_blob: str | None, default_blob: str | None
) -> ResolvedCredential:
    is_owner = _is_owner(principal, conn)
    may_borrow = is_owner or bool(getattr(conn, "allow_fallback", True))
    # A row of the user's own decides, even one that reads back empty: the
    # owner's is not tried in its place.
    personal = _read(personal_blob) if personal_blob is not None else None
    if personal:
        return ResolvedCredential(source=CredentialSource.PERSONAL, connector=personal)
    if not may_borrow:
        raise _credential_required("Connector", conn)
    stored: dict[str, Any] = {}
    if personal is None:
        stored = {
            **_secrets_left_in_auth_config(conn),
            **(_read(default_blob) if default_blob is not None else {}),
        }
    if not stored:
        # Nothing usable is stored: the request goes out bare.
        return ResolvedCredential(source=CredentialSource.NONE, connector={})
    return ResolvedCredential(
        source=CredentialSource.OWNER if is_owner else CredentialSource.OWNER_FALLBACK,
        connector=stored,
    )


# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------

_HEADER_TRANSPORTS = frozenset({"sse", "streamable_http"})


def personal_credential_fits(
    transport: str | None,
    env: Mapping[str, Any] | None,
    headers: Mapping[str, Any] | None,
) -> bool:
    """Whether a personal credential supplies what *transport* reads.

    stdio reads ``env``; SSE and streamable HTTP read ``headers``.  Any other
    transport cannot be connected, so nothing fits it.
    """
    if transport == "stdio":
        return bool(env)
    if transport in _HEADER_TRANSPORTS:
        return bool(headers)
    return False


def must_bring_own_credential(server: Any, user_id: str | None) -> bool:
    """Whether *user_id* may connect to *server* only with their own credential.

    True when the owner has switched ``allow_fallback`` off and the caller is
    not the owner.  A caller with no identity is not the owner.
    """
    if getattr(server, "allow_fallback", True):
        return False
    return not user_id or getattr(server, "user_id", None) != user_id


async def _mcp_credentials(
    db: AsyncSession, principal: Principal, servers: Sequence[tuple[int, Any]]
) -> dict[int, ResolvedCredential | AssemblyRefusal]:
    """Answers for MCP servers, keyed by the index each came with."""
    answers: dict[int, ResolvedCredential | AssemblyRefusal] = {}
    if not servers:
        return answers

    # server id -> (env blob, headers blob) of the caller's own credential
    personal_rows: dict[str, tuple[str | None, str | None]] = {}
    if principal.user_id:
        from sqlalchemy import Text, select, type_coerce

        from fim_one.db.models.mcp_server_credential import MCPServerCredential

        try:
            rows = (
                await db.execute(
                    select(
                        MCPServerCredential.server_id,
                        # As stored: reading a blob is this module's business.
                        type_coerce(MCPServerCredential.env_blob, Text),
                        type_coerce(MCPServerCredential.headers_blob, Text),
                    ).where(
                        MCPServerCredential.server_id.in_([s.id for _, s in servers]),
                        MCPServerCredential.user_id == principal.user_id,
                    )
                )
            ).all()
            for server_id, env_blob, headers_blob in rows:
                personal_rows[server_id] = (env_blob, headers_blob)
        except Exception:
            # Not knowing whether the caller saved a credential is not the
            # same as knowing they did not: the owner's is not used instead.
            logger.warning(
                "Failed to load credentials for %d MCP server(s)", len(servers), exc_info=True
            )
            for index, server in servers:
                answers[index] = _credential_unavailable("MCP server", server)
            return answers

    for index, server in servers:
        try:
            answers[index] = _mcp_answer(principal, server, personal_rows.get(server.id))
        except AssemblyRefusal as refusal:
            answers[index] = refusal
    return answers


def _mcp_answer(
    principal: Principal, server: Any, personal_blobs: tuple[str | None, str | None] | None
) -> ResolvedCredential:
    if personal_blobs is not None:
        env = _read(personal_blobs[0]) if personal_blobs[0] is not None else None
        headers = _read(personal_blobs[1]) if personal_blobs[1] is not None else None
        if personal_credential_fits(server.transport, env, headers):
            # Only the caller's own values, never merged with the owner's.
            return ResolvedCredential(
                source=CredentialSource.PERSONAL, env=env or None, headers=headers or None
            )
    if must_bring_own_credential(server, principal.user_id):
        # The credential must supply what the transport reads. One that does
        # not supplies nothing, and the owner's must not fill the gap. This
        # holds whatever the server stores: "no env, no headers" does not
        # mean the owner lends nothing (the URL and the command arguments
        # are the owner's too, and can carry a token).
        raise _credential_required(
            "MCP server",
            server,
            "environment variables" if server.transport == "stdio" else "HTTP headers",
        )
    if not server.env and not server.headers:
        # The owner, or anyone the owner lends to, and nothing is stored.
        return ResolvedCredential(source=CredentialSource.NONE)
    return ResolvedCredential(
        source=(
            CredentialSource.OWNER
            if _is_owner(principal, server)
            else CredentialSource.OWNER_FALLBACK
        ),
        env=server.env,
        headers=server.headers,
    )
