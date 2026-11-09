"""Connector credential rows, and who may reach a database connector.

Which credential a call goes out with is decided in
``fim_one.core.assembly.credentials``; this module only reads the rows it
decides between:

- a per-user row (``user_id == <caller>``): the caller's own credential;
- the default row (``user_id IS NULL``): the owner's credential, which
  someone else may borrow only when the owner switched ``allow_fallback`` on.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.core.security.encryption import decrypt_credential

__all__ = ["db_raw_sql_tool_allowed", "load_connector_credential"]


def db_raw_sql_tool_allowed(conn: Any, calling_user_id: str | None) -> bool:
    """Whether the raw-SQL database tool may be exposed to this caller.

    Raw-SQL DB tools execute against the connector owner's (typically
    high-privilege) database account with **no per-caller row/column
    scoping** — they expose arbitrary ``SELECT`` over the whole schema. They
    are therefore **owner-only**:

    - ``allow_fallback`` is designed for sharing an *API* credential, where
      the upstream service still enforces its own RBAC on the borrowed token.
      A database has no such per-caller enforcement when everyone rides the
      owner's account, so ``allow_fallback`` does **not** grant a non-owner
      the raw-SQL surface.
    - Non-owners get nothing here until the declarative-action layer
      (per-caller scoped, default-deny) lands — see
      ``dev/legacy-capability-layer/01-read-path-db.md``.

    Parameters
    ----------
    conn:
        The ORM ``Connector`` row. Must expose ``user_id`` (the owner).
    calling_user_id:
        The user making the call (may be ``None`` for system / anonymous).
    """
    return bool(calling_user_id) and getattr(conn, "user_id", None) == calling_user_id


async def load_connector_credential(
    session: AsyncSession,
    connector_id: str,
    user_id: str | None,
) -> dict[str, Any] | None:
    """The decrypted credential row of *connector_id* for *user_id*.

    ``user_id=None`` reads the default row (the owner's credential). Returns
    ``None`` when there is no such row, and an empty dict for a row whose
    content is blank or cannot be decrypted.
    """
    # Local import to avoid a cycle at module import time — the ORM model
    # module transitively imports fim_one.db which pulls in settings.
    from fim_one.db.models.connector_credential import ConnectorCredential

    owner_clause = (
        ConnectorCredential.user_id.is_(None)
        if user_id is None
        else ConnectorCredential.user_id == user_id
    )
    row = (
        await session.execute(
            select(ConnectorCredential).where(
                ConnectorCredential.connector_id == connector_id,
                owner_clause,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return decrypt_credential(row.credentials_blob)

