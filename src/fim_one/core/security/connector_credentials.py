"""Shared credential-resolution helper for connector calls.

Two callers need the same lookup semantics when executing a connector action:

- Chat SSE endpoint (``fim_one.web.api.chat``) when building per-request tools.
- Workflow engine (``fim_one.core.workflow.nodes``) when executing a
  ``connector_action`` node.

Previously both paths inlined slightly different variants of the lookup, which
drifted and produced a silent 401 ("Requires authentication") when the caller
was the connector's owner and ``allow_fallback`` was disabled: the per-user
row did not exist (owners don't carry a per-user credential — their token
lives in the default row with ``user_id IS NULL``) and the fallback gate was
closed against them.  Centralising the logic eliminates that class of bug.

Lookup semantics:

1. If ``calling_user_id`` is set, try to load a per-user credential row
   (``user_id == calling_user_id``) first.  This lets individual users bring
   their own tokens without ever touching the owner's default credential.
2. Otherwise (or if the per-user row is absent), try the *default* credential
   (``user_id IS NULL``).  This default row is the owner's shared credential.
3. The default row is only returned when one of these is true:

   - ``conn.allow_fallback`` is ``True`` — the owner has explicitly opted in
     to letting other users borrow the default credential.
   - ``calling_user_id == conn.user_id`` — the caller **is** the owner; they
     are always permitted to use their own default credential regardless of
     the flag.  Without this carve-out an owner whose connector uses only a
     default credential would be 401ing on their own tool.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.core.security.encryption import decrypt_credential

__all__ = ["resolve_connector_credentials"]


async def resolve_connector_credentials(
    conn: Any,
    calling_user_id: str | None,
    session: AsyncSession,
) -> dict[str, Any]:
    """Return the decrypted auth credentials for a connector call.

    Parameters
    ----------
    conn:
        The ORM ``Connector`` row.  Must expose ``id``, ``user_id`` (the
        connector's owner), and ``allow_fallback``.
    calling_user_id:
        The user making the call (may be ``None`` for system / anonymous).
    session:
        An active ``AsyncSession`` the helper may use for the lookup.

    Returns
    -------
    dict
        Decrypted credential fields (e.g. ``{"default_token": "ghp_..."}``).
        Empty dict when no credential is reachable — callers should treat
        that as "send the request unauthenticated" and let the downstream
        service decide whether to accept it.
    """
    # Local import to avoid a cycle at module import time — the ORM model
    # module transitively imports fim_one.db which pulls in settings.
    from fim_one.db.models.connector_credential import ConnectorCredential

    if calling_user_id:
        row = (
            await session.execute(
                select(ConnectorCredential).where(
                    ConnectorCredential.connector_id == conn.id,
                    ConnectorCredential.user_id == calling_user_id,
                )
            )
        ).scalar_one_or_none()
        if row:
            return decrypt_credential(row.credentials_blob)

    allow_fallback = bool(getattr(conn, "allow_fallback", True))
    is_owner = bool(
        calling_user_id
        and getattr(conn, "user_id", None) == calling_user_id
    )
    if allow_fallback or is_owner:
        row = (
            await session.execute(
                select(ConnectorCredential).where(
                    ConnectorCredential.connector_id == conn.id,
                    ConnectorCredential.user_id.is_(None),
                )
            )
        ).scalar_one_or_none()
        if row:
            return decrypt_credential(row.credentials_blob)

    return {}
