"""Conversation and message request/response schemas."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field

from fim_one.db.models.conversation import BUSINESS_REF_MAX_LENGTH


def normalize_business_ref(value: object) -> str | None:
    """The stored form of a business reference, or ``None`` for "no reference".

    The reference is opaque text: surrounding whitespace is trimmed and an
    empty result means no reference. Control characters (PostgreSQL refuses
    NUL in text) and anything longer than ``BUSINESS_REF_MAX_LENGTH`` are
    refused rather than cut, so a reference is never silently changed.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("business_ref must be a string")
    ref = value.strip()
    if not ref:
        return None
    if len(ref) > BUSINESS_REF_MAX_LENGTH:
        raise ValueError(
            f"business_ref must be at most {BUSINESS_REF_MAX_LENGTH} characters"
        )
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in ref):
        raise ValueError("business_ref must not contain control characters")
    return ref


BusinessRef = Annotated[str | None, BeforeValidator(normalize_business_ref)]


class ConversationCreate(BaseModel):
    title: str = ""
    mode: Literal["react", "dag", "auto"] = "auto"
    agent_id: str | None = None
    model_name: str | None = None
    business_ref: BusinessRef = None


class ConversationUpdate(BaseModel):
    title: str | None = None
    status: Literal["active", "archived"] | None = None
    starred: bool | None = None
    # Sent (even as ``null`` or ``""``) = set or clear; left out = unchanged.
    business_ref: BusinessRef = None


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
    business_ref: str | None = None
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
