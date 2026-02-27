"""Agent request/response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    instructions: str | None = None
    execution_mode: Literal["react", "dag"] = "react"
    model_config_json: dict | None = None
    tool_categories: list[str] | None = None
    suggested_prompts: list[str] | None = None
    kb_ids: list[str] | None = None
    grounding_config: dict | None = None


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    execution_mode: Literal["react", "dag"] | None = None
    model_config_json: dict | None = None
    tool_categories: list[str] | None = None
    suggested_prompts: list[str] | None = None
    kb_ids: list[str] | None = None
    grounding_config: dict | None = None


class AgentResponse(BaseModel):
    id: str
    name: str
    description: str | None
    instructions: str | None
    execution_mode: str
    model_config_json: dict | None
    tool_categories: list[str] | None
    suggested_prompts: list[str] | None
    kb_ids: list[str] | None
    grounding_config: dict | None
    status: str
    published_at: str | None
    created_at: str
    updated_at: str | None
