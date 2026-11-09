"""Which credential a call goes out with.

One rule for every entry point (chat with or without an agent, sub-agents,
workflow nodes):

1. The acting user's own credential, when they saved one, is used alone:
   it is never mixed with the owner's.
2. The resource's owner uses the credential stored on the resource.
3. Anyone else may borrow the owner's only when the owner switched
   ``allow_fallback`` on.
4. Otherwise the call is refused with ``credential_required``.

Database connectors are a separate case: their tools run raw SQL on the
owner's database account with no per-caller scoping, so only the owner gets
the connection config (``owner_only``).

A lookup that fails is refused with ``credential_unavailable``; it never
falls through to the owner's credential.
"""

from __future__ import annotations

import logging
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
    "resolve_credential",
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

    *resource* is a ``Connector`` row. Raises :class:`AssemblyRefusal`
    ``credential_required`` (403), ``credential_unavailable`` (503) or, for a
    database connector, ``owner_only`` (403).
    """
    if getattr(resource, "type", None) == "database":
        return _database_credential(principal, resource)
    return await _connector_credential(db, principal, resource)


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


async def _connector_credential(
    db: AsyncSession, principal: Principal, conn: Any
) -> ResolvedCredential:
    from fim_one.core.security.connector_credentials import load_connector_credential

    is_owner = _is_owner(principal, conn)
    may_borrow = is_owner or bool(getattr(conn, "allow_fallback", True))
    try:
        personal = (
            await load_connector_credential(db, conn.id, principal.user_id)
            if principal.user_id
            else None
        )
        # A row of the user's own decides, even one that reads back empty
        # (blank, or not decryptable): the owner's is not tried in its place.
        stored = (
            await load_connector_credential(db, conn.id, None)
            if personal is None and may_borrow
            else None
        )
    except Exception as exc:
        logger.warning("Failed to load credentials for connector %r", _name(conn), exc_info=True)
        raise _credential_unavailable("Connector", conn) from exc

    if personal:
        return ResolvedCredential(source=CredentialSource.PERSONAL, connector=personal)
    if not may_borrow:
        raise _credential_required("Connector", conn)
    if not stored:
        # Nothing usable is stored: the request goes out bare.
        return ResolvedCredential(source=CredentialSource.NONE, connector={})
    return ResolvedCredential(
        source=CredentialSource.OWNER if is_owner else CredentialSource.OWNER_FALLBACK,
        connector=stored,
    )
