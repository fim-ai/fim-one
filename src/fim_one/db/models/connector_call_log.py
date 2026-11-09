"""Call log ORM model: one row per call a tool made to an outside resource."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from fim_one.db.base import Base, TimestampMixin, UUIDPKMixin


class ConnectorCallLog(UUIDPKMixin, TimestampMixin, Base):
    """A call to an API connector, a database connector or an MCP server.

    The table keeps its original name. ``resource_kind`` says what was
    called: a connector row (``connector`` / ``database``) carries
    ``connector_id``; an MCP row (``mcp``) carries ``mcp_server_id`` and has
    no ``connector_id``. ``connector_name`` and ``action_name`` hold the
    resource's and the action's (or MCP tool's) name for either.
    """

    __tablename__ = "connector_call_logs"

    # ``SET NULL`` on both references: a record outlives the connector and
    # the conversation it names, and must not block deleting either.
    connector_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("connectors.id", ondelete="SET NULL"), nullable=True, index=True
    )
    connector_name: Mapped[str] = mapped_column(String(200), nullable=False)
    action_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action_name: Mapped[str] = mapped_column(String(200), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True, index=True
    )
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    request_method: Mapped[str] = mapped_column(String(10), nullable=False)
    request_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON blob naming the fences that applied to this call, e.g.
    # {"read_only": true, "redacted_columns": ["salary"]}. Null for callers
    # that record no fence state.
    scope_rules_applied: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "connector", "database" or "mcp". Rows written before the column
    # existed read "connector", database calls among them.
    resource_kind: Mapped[str | None] = mapped_column(
        String(20), nullable=True, server_default="connector"
    )
    # Not foreign keys: a record outlives the server or run it names.
    mcp_server_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    workflow_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Where the call entered (``core.assembly.principal.Entry``), and whose
    # credential it went out with (``core.assembly.credentials.CredentialSource``).
    entry: Mapped[str | None] = mapped_column(String(20), nullable=True)
    credential_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
