"""Agent request/response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    icon: str | None = None
    description: str | None = None
    instructions: str | None = None
    model_config_json: dict | None = None
    tool_categories: list[str] | None = None
    suggested_prompts: list[str] | None = None
    kb_ids: list[str] | None = None
    connector_ids: list[str] | None = None
    grounding_config: dict | None = None
    sandbox_config: dict | None = None
    execution_mode: Literal["react", "dag"] = "react"


class AgentUpdate(BaseModel):
    name: str | None = None
    icon: str | None = None
    description: str | None = None
    instructions: str | None = None
    model_config_json: dict | None = None
    tool_categories: list[str] | None = None
    suggested_prompts: list[str] | None = None
    kb_ids: list[str] | None = None
    connector_ids: list[str] | None = None
    grounding_config: dict | None = None
    sandbox_config: dict | None = None
    execution_mode: Literal["react", "dag"] | None = None


class AgentResponse(BaseModel):
    id: str
    name: str
    icon: str | None
    description: str | None
    instructions: str | None
    model_config_json: dict | None
    tool_categories: list[str] | None
    suggested_prompts: list[str] | None
    kb_ids: list[str] | None
    connector_ids: list[str] | None
    grounding_config: dict | None
    sandbox_config: dict | None
    execution_mode: str
    status: str
    published_at: str | None
    is_global: bool = False
    is_builder: bool = False
    visibility: str = "personal"
    org_id: str | None = None
    created_at: str
    updated_at: str | None


class AICreateAgentRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=5000)


class AIRefineAgentRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    history: list[dict] = Field(default_factory=list)


class AICreateAgentResult(BaseModel):
    agent: AgentResponse
    message: str = ""
    message_key: str = ""
    message_args: dict = {}


class AIRefineAgentResult(BaseModel):
    agent: AgentResponse
    modified_fields: list[str] = []
    message: str = ""
    message_key: str = ""
    message_args: dict = {}
