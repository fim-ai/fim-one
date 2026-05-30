"""Workflow ORM models — blueprint-based visual workflow execution."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
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
    blueprint: Any = Column(JSON, nullable=False)  # { nodes: [], edges: [], viewport: {} }
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

    # API key for external trigger (public endpoint, no user auth)
    api_key: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )

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
    inputs: Any = Column(JSON, nullable=True)
    outputs: Any = Column(JSON, nullable=True)
    node_results: Any = Column(
        JSON, nullable=True
    )  # { node_id: { status, output, error, started_at, completed_at, duration } }
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending"
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    workflow: Mapped[Workflow] = relationship(back_populates="runs", lazy="raise")
    approvals: Mapped[list[WorkflowApproval]] = relationship(
        back_populates="run", lazy="raise", passive_deletes=True
    )


class WorkflowApproval(UUIDPKMixin, TimestampMixin, Base):
    """A pending approval request created by a HumanIntervention node."""

    __tablename__ = "workflow_approvals"

    workflow_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignee: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    decision_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout_hours: Mapped[float] = mapped_column(
        Float, nullable=False, default=24.0, server_default="24"
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    run: Mapped[WorkflowRun] = relationship(back_populates="approvals", lazy="raise")


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
