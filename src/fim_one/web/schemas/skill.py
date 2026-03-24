"""Skill request/response schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    content: str = Field(default="")
    script: str | None = None
    script_type: Literal["python", "shell"] | None = None
    is_active: bool = True
    resource_refs: list[dict[str, Any]] | None = None


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    content: str | None = None
    script: str | None = None
    script_type: Literal["python", "shell"] | None = None
    is_active: bool | None = None
    resource_refs: list[dict[str, Any]] | None = None


class SkillForkRequest(BaseModel):
    """Optional overrides when forking (cloning) a skill."""

    name: str | None = None  # Custom name; defaults to "{original} (Fork)"


class SkillResponse(BaseModel):
    id: str
    user_id: str | None
    name: str
    description: str | None
    content: str
    script: str | None
    script_type: str | None
    forked_from: str | None = None
    visibility: str = "personal"
    org_id: str | None = None
    is_active: bool = True
    status: str = "draft"
    publish_status: str | None = None
    published_at: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None
    source: str | None = None
    resource_refs: list[dict[str, Any]] | None = None
    created_at: str
    updated_at: str | None
