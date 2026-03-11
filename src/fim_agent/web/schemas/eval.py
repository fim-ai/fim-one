"""Pydantic schemas for the Evaluation Center."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class EvalDatasetCreate(BaseModel):
    name: str
    description: str | None = None


class EvalDatasetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class EvalDatasetResponse(BaseModel):
    id: str
    name: str
    description: str | None
    case_count: int = 0
    created_at: str
    updated_at: str | None


class EvalCaseCreate(BaseModel):
    prompt: str
    expected_behavior: str
    assertions: list[str] | None = None


class EvalCaseUpdate(BaseModel):
    prompt: str | None = None
    expected_behavior: str | None = None
    assertions: list[str] | None = None


class EvalCaseResponse(BaseModel):
    id: str
    dataset_id: str
    prompt: str
    expected_behavior: str
    assertions: list[str] | None
    created_at: str
    updated_at: str | None


class EvalRunCreate(BaseModel):
    agent_id: str
    dataset_id: str


class EvalRunResponse(BaseModel):
    id: str
    agent_id: str
    agent_name: str | None = None
    dataset_id: str
    dataset_name: str | None = None
    status: str  # pending|running|completed|failed
    total_cases: int
    passed_cases: int
    failed_cases: int
    avg_latency_ms: float | None
    total_tokens: int | None
    error_message: str | None
    completed_at: str | None
    created_at: str
    updated_at: str | None


class EvalCaseResultResponse(BaseModel):
    id: str
    run_id: str
    case_id: str
    case_prompt: str | None = None
    case_expected_behavior: str | None = None
    status: str  # pass|fail|error
    agent_answer: str | None
    grader_reasoning: str | None
    latency_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    created_at: str


class EvalRunDetailResponse(EvalRunResponse):
    results: list[EvalCaseResultResponse] = []
