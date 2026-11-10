"""Workflow ORM models — blueprint-based visual workflow execution."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fim_one.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .user import User


class Workflow(UUIDPKMixin, TimestampMixin, Base):
    """A visual workflow blueprint — defines static execution steps."""

    __tablename__ = "workflows"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    blueprint: Any = Column(JSON, nullable=False)  # blueprint v2 (core/workflow/blueprint_v2.py)
    # Pre-v2 copy kept by migration t10p3c4d5e6f for one release; NULL for
    # rows written as v2. Not read by the application.
    blueprint_legacy: Any = Column(JSON, nullable=True)
    input_schema: Any = Column(JSON, nullable=True)  # extracted from Start node on save
    output_schema: Any = Column(JSON, nullable=True)  # extracted from End node on save
    status: Mapped[str] = mapped_column(String(20), default="draft")
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=sa.text("TRUE")
    )
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, default="personal", server_default="personal"
    )
    org_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=True, index=True
    )

    # Publish review fields (same columns as Agent)
    publish_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Fork lineage — ID of the source workflow this was forked from
    forked_from: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Per-workflow run retention (NULL = use global default)
    run_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Human-readable summary of last blueprint change (auto-generated)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Maximum run duration in seconds (NULL = use engine default of 600s)
    max_run_duration_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    # Optional webhook URL — receives POST on run completion/failure
    webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Trigger key for the public trigger endpoint, stored as a hex SHA-256
    # hash plus a short display prefix; the plain key is shown only once.
    api_key_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    api_key_prefix: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Encrypted env vars for workflow (stored as encrypted JSON)
    env_vars_blob: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Scheduled trigger fields
    schedule_cron: Mapped[str | None] = mapped_column(String(100), nullable=True)
    schedule_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=sa.text("FALSE")
    )
    schedule_inputs: Any = Column(JSON, nullable=True)
    schedule_timezone: Mapped[str | None] = mapped_column(
        String(50), nullable=True, default="UTC", server_default="UTC"
    )
    last_scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User | None] = relationship(back_populates="workflows", lazy="raise")
    runs: Mapped[list[WorkflowRun]] = relationship(
        back_populates="workflow", lazy="raise", passive_deletes=True
    )
    versions: Mapped[list[WorkflowVersion]] = relationship(
        back_populates="workflow", lazy="raise", passive_deletes=True
    )


class WorkflowTemplate(UUIDPKMixin, TimestampMixin, Base):
    """A reusable workflow template — admin-managed blueprints users can clone."""

    __tablename__ = "workflow_templates"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    icon: Mapped[str] = mapped_column(String(100), nullable=False, default="🔄")
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    blueprint: Any = Column(JSON, nullable=False)
    # Pre-v2 copy kept by migration t10p3c4d5e6f for one release; NULL for
    # rows written as v2. Not read by the application.
    blueprint_legacy: Any = Column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=sa.text("TRUE")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )


class WorkflowRun(UUIDPKMixin, TimestampMixin, Base):
    """A single execution run of a workflow blueprint."""

    __tablename__ = "workflow_runs"

    workflow_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    blueprint_snapshot: Any = Column(JSON, nullable=False)  # frozen copy at run time
    # Pre-v2 copy kept by migration t10p3c4d5e6f for one release; NULL for
    # rows written as v2. Not read by the application.
    blueprint_legacy: Any = Column(JSON, nullable=True)
    inputs: Any = Column(JSON, nullable=True)
    outputs: Any = Column(JSON, nullable=True)
    node_results: Any = Column(
        JSON, nullable=True
    )  # { node_id: { status, output, error, started_at, completed_at, duration } }
    # pending / running / waiting (for a human's decision) / completed /
    # failed / cancelled.
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Tokens consumed by LLM/Agent nodes during this run, billed to the
    # workflow owner. Counted toward the owner's quota window so unattended
    # (webhook/cron) runs can't mint free, unmetered LLM usage.
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # Entry point that started the run: interactive, batch, trigger,
    # schedule, tool or test_node. NULL on rows written before the column
    # existed; readers treat NULL as an ordinary run.
    mode: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # What a run waiting for a decision needs to be continued: its
    # variables (never the author's env vars), each step's state and
    # result, and the request each waiting step is waiting on. Written
    # when the run enters ``waiting``, emptied when it ends.
    suspend_state: Any = Column(JSON, nullable=True)
    # The server process executing the run and when it last said so. A run
    # still ``running`` whose heartbeat is old was left by a process that
    # died (``core/workflow/run_recovery.py``).
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    workflow: Mapped[Workflow] = relationship(back_populates="runs", lazy="raise")


# ``WorkflowRun.mode`` of a single-node test: such rows exist only so the
# test's token usage counts toward quota.
RUN_MODE_TEST_NODE = "test_node"


def history_run_clause() -> Any:
    """WHERE clause excluding single-node test rows from run history, counts,
    rates and recent-run lists. Quota sums deliberately include them."""
    return sa.or_(WorkflowRun.mode.is_(None), WorkflowRun.mode != RUN_MODE_TEST_NODE)


class WorkflowVersion(UUIDPKMixin, Base):
    """An immutable snapshot of a workflow blueprint at a point in time."""

    __tablename__ = "workflow_versions"

    workflow_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    blueprint: Any = Column(JSON, nullable=False)  # frozen snapshot
    # Pre-v2 copy kept by migration t10p3c4d5e6f for one release; NULL for
    # rows written as v2. Not read by the application.
    blueprint_legacy: Any = Column(JSON, nullable=True)
    input_schema: Any = Column(JSON, nullable=True)
    output_schema: Any = Column(JSON, nullable=True)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=sa.text("(CURRENT_TIMESTAMP)"),
        nullable=False,
    )

    workflow: Mapped[Workflow] = relationship(back_populates="versions", lazy="raise")
