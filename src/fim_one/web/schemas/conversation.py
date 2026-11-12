"""Conversation and message request/response schemas."""

from __future__ import annotations

import unicodedata
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field

from fim_one.db.models.conversation import BUSINESS_REF_MAX_LENGTH

# Trimmed from both ends: ASCII whitespace and the Unicode space separators.
_TRIM = " \t\n\r\v\f"
# Refused anywhere in the value: C0 and C1 controls and DEL (Cc), format
# and zero-width characters (Cf), line and paragraph separators (Zl, Zp).
_REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})


def _is_trimmed(ch: str) -> bool:
    return ch in _TRIM or unicodedata.category(ch) == "Zs"


def normalize_business_ref(value: object) -> str | None:
    """The stored form of a business reference, or ``None`` for "no reference".

    The reference is opaque text compared exactly. It is normalised to
    Unicode NFC (so the same text typed in composed or decomposed form is
    the same reference), then whitespace is trimmed from both ends; an empty
    result means no reference. Invisible or control characters anywhere in
    it (categories Cc, Cf, Zl, Zp: C0/C1 controls, zero-width and format
    characters, U+2028/U+2029) and a normalised value longer than
    ``BUSINESS_REF_MAX_LENGTH`` are refused rather than removed or cut, so a
    reference is never silently changed beyond normalisation and trimming.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("business_ref must be a string")
    ref = unicodedata.normalize("NFC", value)
    start, end = 0, len(ref)
    while start < end and _is_trimmed(ref[start]):
        start += 1
    while end > start and _is_trimmed(ref[end - 1]):
        end -= 1
    ref = ref[start:end]
    if not ref:
        return None
    if any(unicodedata.category(ch) in _REFUSED_CATEGORIES for ch in ref):
        raise ValueError(
            "business_ref must not contain control, format or separator characters"
        )
    if len(ref) > BUSINESS_REF_MAX_LENGTH:
        raise ValueError(
            f"business_ref must be at most {BUSINESS_REF_MAX_LENGTH} characters"
        )
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
