"""Knowledge Base request/response schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class KBCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    chunk_strategy: Literal["recursive", "fixed", "semantic", "markdown"] = "recursive"
    chunk_size: int = Field(default=1000, ge=100, le=6000)
    chunk_overlap: int = Field(default=200, ge=0, le=1000)
    retrieval_mode: Literal["hybrid", "dense", "fts"] = "hybrid"
    is_active: bool = True


class KBUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    retrieval_mode: Literal["hybrid", "dense", "fts"] | None = None
    is_active: bool | None = None


class KBResponse(BaseModel):
    id: str
    user_id: str
    name: str
    description: str | None
    chunk_strategy: str
    chunk_size: int
    chunk_overlap: int
    retrieval_mode: str
    document_count: int
    total_chunks: int
    status: str
    is_active: bool = True
    visibility: str = "personal"
    org_id: str | None = None
    publish_status: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None
    source: str | None = None
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
    metadata: dict[str, Any] | None = None
    score: float


class ChunkResponse(BaseModel):
    id: str
    text: str
    chunk_index: int
    metadata: dict[str, Any] | None = None
    content_hash: str


class ChunkUpdate(BaseModel):
    text: str = Field(min_length=1, max_length=6000)


class DocumentCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=200, pattern=r"^[\w\-. ]+\.md$")
    content: str = Field(min_length=1)


class ImportUrlsRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=20)


class UrlImportResult(BaseModel):
    url: str
    status: Literal["success", "failed"]
    doc_id: str | None = None
    error: str | None = None


class ImportUrlsResponse(BaseModel):
    results: list[UrlImportResult]
