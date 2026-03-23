"""Eval run execution + results API."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.core.agent import ReActAgent
from fim_one.core.model.structured import structured_llm_call
from fim_one.core.model.types import ChatMessage
from fim_one.db import create_session, get_session
from fim_one.web.auth import get_current_user
from fim_one.web.deps import get_effective_fast_llm, get_effective_llm, get_llm_from_config, get_tools
from fim_one.web.exceptions import AppError
from fim_one.web.models.agent import Agent
from fim_one.web.models.eval import EvalCase, EvalCaseResult, EvalDataset, EvalRun
from fim_one.web.models.user import User
from fim_one.web.schemas.common import ApiResponse, PaginatedResponse
from fim_one.web.schemas.eval import (
    EvalCaseResultResponse,
    EvalRunCreate,
    EvalRunDetailResponse,
    EvalRunResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/eval/runs", tags=["eval"])

_GRADER_SYSTEM = """\
You are an impartial AI evaluator. Your job is to judge whether an AI agent's answer meets the expected behavior for a given prompt.

Be strict but fair. A "pass" requires the answer to genuinely address the prompt according to the expected behavior. A "fail" means the answer is wrong, incomplete, off-topic, or misses key requirements.\
"""

_GRADER_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "reasoning": {"type": "string"},
    },
    "required": ["verdict", "reasoning"],
}


def _run_to_response(
    run: EvalRun,
    agent_name: str | None = None,
    dataset_name: str | None = None,
) -> EvalRunResponse:
    return EvalRunResponse(
        id=run.id,
        agent_id=run.agent_id,
        agent_name=agent_name,
        dataset_id=run.dataset_id,
        dataset_name=dataset_name,
        status=run.status,
        total_cases=run.total_cases,
        passed_cases=run.passed_cases,
        failed_cases=run.failed_cases,
        avg_latency_ms=run.avg_latency_ms,
        total_tokens=run.total_tokens,
        error_message=run.error_message,
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        created_at=run.created_at.isoformat() if run.created_at else "",
        updated_at=run.updated_at.isoformat() if run.updated_at else None,
    )


async def _run_one_case(
    case: dict[str, Any],
    agent_instructions: str | None,
    tools: Any,
    llm: Any,
    grader_llm: Any,
    run_id: str,
    db: AsyncSession,
    sem: asyncio.Semaphore,
) -> None:
    """Run a single eval case, grade it, and write the EvalCaseResult row."""
    async with sem:
        t0 = time.monotonic()
        status = "error"
        answer = None
        reasoning = None
        latency_ms = 0
        prompt_tokens = None
        completion_tokens = None

        try:
            react_agent = ReActAgent(
                llm=llm,
                tools=tools,
                extra_instructions=agent_instructions,
            )
            result = await react_agent.run(case["prompt"])
            latency_ms = int((time.monotonic() - t0) * 1000)
            answer = result.answer
            if result.usage:
                prompt_tokens = result.usage.prompt_tokens
                completion_tokens = result.usage.completion_tokens

            # Build grader user message
            assertions_list = case.get("assertions") or []
            if assertions_list:
                assertions_text = "\n".join(f"- {a}" for a in assertions_list)
            else:
                assertions_text = "None specified"

            grader_user = (
                f"## Prompt given to the agent\n{case['prompt']}\n\n"
                f"## Expected behavior\n{case['expected_behavior']}\n\n"
                f"## Specific assertions to verify\n{assertions_text}\n\n"
                f"## Agent's actual answer\n{answer}\n\n"
                "Evaluate whether the agent's answer passes or fails."
            )
            grader_messages = [
                ChatMessage(role="system", content=_GRADER_SYSTEM),
                ChatMessage(role="user", content=grader_user),
            ]
            sc: Any = await structured_llm_call(
                grader_llm,
                grader_messages,
                schema=_GRADER_SCHEMA,
                function_name="grade_answer",
                default_value={
                    "verdict": "fail",
                    "reasoning": "Grader failed to produce a verdict.",
                },
            )
            verdict = sc.value.get("verdict", "fail")
            reasoning = sc.value.get("reasoning", "")
            status = "pass" if verdict == "pass" else "fail"
        except Exception as exc:
            logger.exception("Error running eval case %s", case["id"])
            status = "error"
            answer = None
            reasoning = str(exc)
            latency_ms = int((time.monotonic() - t0) * 1000)

        result_row = EvalCaseResult(
            run_id=run_id,
            case_id=case["id"],
            status=status,
            agent_answer=answer,
            grader_reasoning=reasoning,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        db.add(result_row)
        await db.flush()


async def _execute_eval_run(
    run_id: str,
    agent_instructions: str | None,
    cases: list[dict[str, Any]],
    llm: Any,
    grader_llm: Any,
) -> None:
    """Background task: run all cases, grade them, and update the EvalRun row."""
    async with create_session() as db:
        # Mark as running
        run_result = await db.execute(select(EvalRun).where(EvalRun.id == run_id))
        run = run_result.scalar_one_or_none()
        if run is None:
            return
        run.status = "running"
        await db.commit()

        tools = get_tools()
        sem = asyncio.Semaphore(5)

        try:
            await asyncio.gather(
                *[
                    _run_one_case(
                        case, agent_instructions, tools, llm, grader_llm, run_id, db, sem
                    )
                    for case in cases
                ],
                return_exceptions=True,
            )
            await db.commit()

            # Aggregate results
            results_result = await db.execute(
                select(EvalCaseResult).where(EvalCaseResult.run_id == run_id)
            )
            results = results_result.scalars().all()

            passed = sum(1 for r in results if r.status == "pass")
            failed = sum(1 for r in results if r.status in ("fail", "error"))
            latencies = [r.latency_ms for r in results if r.latency_ms is not None]
            avg_latency = sum(latencies) / len(latencies) if latencies else None
            total_tok = sum(
                (r.prompt_tokens or 0) + (r.completion_tokens or 0)
                for r in results
            )

            run_result2 = await db.execute(select(EvalRun).where(EvalRun.id == run_id))
            run = run_result2.scalar_one_or_none()
            if run:
                run.status = "completed"
                run.passed_cases = passed
                run.failed_cases = failed
                run.avg_latency_ms = avg_latency
                run.total_tokens = total_tok if total_tok > 0 else None
                run.completed_at = datetime.now(UTC)
                await db.commit()

        except Exception as exc:
            logger.exception("Fatal error in eval run %s", run_id)
            run_result3 = await db.execute(select(EvalRun).where(EvalRun.id == run_id))
            run = run_result3.scalar_one_or_none()
            if run:
                run.status = "failed"
                run.error_message = str(exc)
                run.completed_at = datetime.now(UTC)
                await db.commit()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=ApiResponse)
async def create_run(
    body: EvalRunCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    # Verify agent ownership
    agent_result = await db.execute(
        select(Agent).where(
            Agent.id == body.agent_id,
            Agent.user_id == current_user.id,
        )
    )
    agent = agent_result.scalar_one_or_none()
    if agent is None:
        raise AppError("agent_not_found", status_code=404)

    # Verify dataset ownership
    ds_result = await db.execute(
        select(EvalDataset).where(
            EvalDataset.id == body.dataset_id,
            EvalDataset.user_id == current_user.id,
        )
    )
    dataset = ds_result.scalar_one_or_none()
    if dataset is None:
        raise AppError("dataset_not_found", status_code=404)

    # Require at least one test case
    count_result = await db.execute(
        select(func.count(EvalCase.id)).where(EvalCase.dataset_id == body.dataset_id)
    )
    case_count = count_result.scalar_one()
    if case_count == 0:
        raise AppError("dataset_empty", status_code=400)

    # Load all cases as plain dicts before the session closes
    cases_result = await db.execute(
        select(EvalCase).where(EvalCase.dataset_id == body.dataset_id)
    )
    cases = [
        {
            "id": c.id,
            "prompt": c.prompt,
            "expected_behavior": c.expected_behavior,
            "assertions": c.assertions,
        }
        for c in cases_result.scalars().all()
    ]

    # Build LLMs while we still have a DB session for system model config lookup
    llm = get_llm_from_config(agent.model_config_json or {})
    if llm is None:
        llm = await get_effective_llm(db)
    grader_llm = await get_effective_fast_llm(db)

    # Create the EvalRun record
    run = EvalRun(
        user_id=current_user.id,
        agent_id=body.agent_id,
        dataset_id=body.dataset_id,
        status="pending",
        total_cases=case_count,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    run_response = _run_to_response(run, agent_name=agent.name, dataset_name=dataset.name)

    # Fire background task — capture agent instructions and LLM instances by value
    agent_instructions = agent.instructions
    asyncio.create_task(
        _execute_eval_run(run.id, agent_instructions, cases, llm, grader_llm)
    )

    return ApiResponse(data=run_response)


@router.get("", response_model=PaginatedResponse)
async def list_runs(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    offset = (page - 1) * size

    total_result = await db.execute(
        select(func.count())
        .select_from(EvalRun)
        .where(EvalRun.user_id == current_user.id)
    )
    total = total_result.scalar_one()

    runs_result = await db.execute(
        select(EvalRun)
        .where(EvalRun.user_id == current_user.id)
        .order_by(EvalRun.created_at.desc())
        .offset(offset)
        .limit(size)
    )
    runs = runs_result.scalars().all()

    # Batch load agent and dataset names
    agent_ids = list({r.agent_id for r in runs})
    dataset_ids = list({r.dataset_id for r in runs})

    agent_names: dict[str, str] = {}
    dataset_names: dict[str, str] = {}

    if agent_ids:
        a_result = await db.execute(
            select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids))
        )
        agent_names = {row[0]: row[1] for row in a_result.all()}
    if dataset_ids:
        d_result = await db.execute(
            select(EvalDataset.id, EvalDataset.name).where(EvalDataset.id.in_(dataset_ids))
        )
        dataset_names = {row[0]: row[1] for row in d_result.all()}

    items = [
        _run_to_response(
            r,
            agent_name=agent_names.get(r.agent_id),
            dataset_name=dataset_names.get(r.dataset_id),
        )
        for r in runs
    ]
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if size else 1,
    )


@router.get("/{run_id}", response_model=ApiResponse)
async def get_run(
    run_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    run_result = await db.execute(
        select(EvalRun).where(
            EvalRun.id == run_id,
            EvalRun.user_id == current_user.id,
        )
    )
    run = run_result.scalar_one_or_none()
    if run is None:
        raise AppError("run_not_found", status_code=404)

    # Load agent and dataset names
    a_result = await db.execute(
        select(Agent.id, Agent.name).where(Agent.id == run.agent_id)
    )
    agent_row = a_result.first()
    d_result = await db.execute(
        select(EvalDataset.id, EvalDataset.name).where(EvalDataset.id == run.dataset_id)
    )
    dataset_row = d_result.first()

    # Load case results ordered by creation time
    results_result = await db.execute(
        select(EvalCaseResult)
        .where(EvalCaseResult.run_id == run_id)
        .order_by(EvalCaseResult.created_at.asc())
    )
    results = results_result.scalars().all()

    # Batch load case prompts and expected behaviors
    case_ids = [r.case_id for r in results]
    case_map: dict[str, EvalCase] = {}
    if case_ids:
        cases_result = await db.execute(
            select(EvalCase).where(EvalCase.id.in_(case_ids))
        )
        for c in cases_result.scalars().all():
            case_map[c.id] = c

    result_responses = [
        EvalCaseResultResponse(
            id=r.id,
            run_id=r.run_id,
            case_id=r.case_id,
            case_prompt=case_map[r.case_id].prompt if r.case_id in case_map else None,
            case_expected_behavior=(
                case_map[r.case_id].expected_behavior if r.case_id in case_map else None
            ),
            status=r.status,
            agent_answer=r.agent_answer,
            grader_reasoning=r.grader_reasoning,
            latency_ms=r.latency_ms,
            prompt_tokens=r.prompt_tokens,
            completion_tokens=r.completion_tokens,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in results
    ]

    run_resp = _run_to_response(
        run,
        agent_name=agent_row[1] if agent_row else None,
        dataset_name=dataset_row[1] if dataset_row else None,
    )
    return ApiResponse(
        data=EvalRunDetailResponse(**run_resp.model_dump(), results=result_responses)
    )


@router.delete("/{run_id}", response_model=ApiResponse)
async def delete_run(
    run_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    run_result = await db.execute(
        select(EvalRun).where(
            EvalRun.id == run_id,
            EvalRun.user_id == current_user.id,
        )
    )
    run = run_result.scalar_one_or_none()
    if run is None:
        raise AppError("run_not_found", status_code=404)
    await db.delete(run)
    await db.commit()
    return ApiResponse(data={"deleted": run_id})
