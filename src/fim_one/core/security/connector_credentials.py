"""Who may reach a database connector.

Which credential a call goes out with, and the reading of the credential
rows it decides between, is in ``fim_one.core.assembly.credentials``.
"""

from __future__ import annotations

from typing import Any

__all__ = ["db_raw_sql_tool_allowed"]


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

