"""Conversation and message request/response schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    title: str = ""
    mode: Literal["react", "dag", "auto"] = "auto"
    agent_id: str | None = None
    model_name: str | None = None


class ConversationUpdate(BaseModel):
    title: str | None = None
    status: Literal["active", "archived"] | None = None
    starred: bool | None = None


class BatchDeleteRequest(BaseModel):
    ids: list[str] = Field(..., max_length=50)


class ConversationResponse(BaseModel):
    id: str
    title: str
    mode: str
    agent_id: str | None
    status: str
    starred: bool
    model_name: str | None
    total_tokens: int
    created_at: str
    updated_at: str | None


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str | None
    message_type: str
    metadata: dict[str, Any] | None
    created_at: str


class ConversationDetail(ConversationResponse):
    messages: list[MessageResponse] = []
