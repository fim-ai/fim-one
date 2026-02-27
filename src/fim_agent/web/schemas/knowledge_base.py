"""Knowledge Base request/response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class KBCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    chunk_strategy: Literal["recursive", "fixed", "semantic"] = "recursive"
    chunk_size: int = Field(default=1000, ge=100, le=10000)
    chunk_overlap: int = Field(default=200, ge=0, le=5000)
    retrieval_mode: Literal["hybrid", "dense", "fts"] = "hybrid"


class KBUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    chunk_strategy: Literal["recursive", "fixed", "semantic"] | None = None
    chunk_size: int | None = Field(default=None, ge=100, le=10000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=5000)
    retrieval_mode: Literal["hybrid", "dense", "fts"] | None = None


class KBResponse(BaseModel):
    id: str
    name: str
    description: str | None
    chunk_strategy: str
    chunk_size: int
    chunk_overlap: int
    retrieval_mode: str
    document_count: int
    total_chunks: int
    status: str
    created_at: str
    updated_at: str | None


class KBDocumentResponse(BaseModel):
    id: str
    kb_id: str
    filename: str
    file_size: int
    file_type: str
    chunk_count: int
    status: str
    error_message: str | None
    created_at: str


class KBRetrieveRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)


class KBRetrieveResponse(BaseModel):
    content: str
    metadata: dict | None = None
    score: float
