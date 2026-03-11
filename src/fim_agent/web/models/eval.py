"""Evaluation Center ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fim_agent.db.base import Base, TimestampMixin, UUIDPKMixin


class EvalDataset(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "eval_datasets"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    cases: Mapped[list["EvalCase"]] = relationship(
        back_populates="dataset", lazy="raise", passive_deletes=True
    )


class EvalCase(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "eval_cases"

    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    expected_behavior: Mapped[str] = mapped_column(Text, nullable=False)
    assertions: Any = Column(JSON, nullable=True)

    dataset: Mapped["EvalDataset"] = relationship(back_populates="cases", lazy="raise")


class EvalRun(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "eval_runs"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agents.id"), nullable=False, index=True
    )
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_datasets.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending"
    )
    total_cases: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    passed_cases: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    failed_cases: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    avg_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EvalCaseResult(UUIDPKMixin, Base):
    __tablename__ = "eval_case_results"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_cases.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # pass|fail|error
    agent_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    grader_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=sa.text("(CURRENT_TIMESTAMP)"),
        nullable=False,
    )
