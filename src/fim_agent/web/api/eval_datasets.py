"""Eval dataset + case CRUD API."""

from __future__ import annotations

import logging
import math

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_agent.db import get_session
from fim_agent.web.auth import get_current_user
from fim_agent.web.exceptions import AppError
from fim_agent.web.models.eval import EvalCase, EvalDataset
from fim_agent.web.models.user import User
from fim_agent.web.schemas.common import ApiResponse, PaginatedResponse
from fim_agent.web.schemas.eval import (
    EvalCaseCreate,
    EvalCaseResponse,
    EvalCaseUpdate,
    EvalDatasetCreate,
    EvalDatasetResponse,
    EvalDatasetUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/eval/datasets", tags=["eval"])


async def _get_owned_dataset(
    dataset_id: str, user_id: str, db: AsyncSession
) -> EvalDataset:
    result = await db.execute(
        select(EvalDataset).where(
            EvalDataset.id == dataset_id,
            EvalDataset.user_id == user_id,
        )
    )
    ds = result.scalar_one_or_none()
    if ds is None:
        raise AppError("dataset_not_found", status_code=404)
    return ds


def _case_to_response(case: EvalCase) -> EvalCaseResponse:
    return EvalCaseResponse(
        id=case.id,
        dataset_id=case.dataset_id,
        prompt=case.prompt,
        expected_behavior=case.expected_behavior,
        assertions=case.assertions,
        created_at=case.created_at.isoformat() if case.created_at else "",
        updated_at=case.updated_at.isoformat() if case.updated_at else None,
    )


# ---------------------------------------------------------------------------
# Dataset CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=ApiResponse)
async def create_dataset(
    body: EvalDatasetCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    ds = EvalDataset(
        user_id=current_user.id,
        name=body.name,
        description=body.description,
    )
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    return ApiResponse(
        data=EvalDatasetResponse(
            id=ds.id,
            name=ds.name,
            description=ds.description,
            case_count=0,
            created_at=ds.created_at.isoformat() if ds.created_at else "",
            updated_at=ds.updated_at.isoformat() if ds.updated_at else None,
        )
    )


@router.get("", response_model=PaginatedResponse)
async def list_datasets(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    offset = (page - 1) * size

    total_result = await db.execute(
        select(func.count())
        .select_from(EvalDataset)
        .where(EvalDataset.user_id == current_user.id)
    )
    total = total_result.scalar_one()

    datasets_result = await db.execute(
        select(EvalDataset)
        .where(EvalDataset.user_id == current_user.id)
        .order_by(EvalDataset.created_at.desc())
        .offset(offset)
        .limit(size)
    )
    datasets = datasets_result.scalars().all()

    # Batch count cases
    case_counts: dict[str, int] = {}
    if datasets:
        ds_ids = [d.id for d in datasets]
        count_result = await db.execute(
            select(EvalCase.dataset_id, func.count(EvalCase.id))
            .where(EvalCase.dataset_id.in_(ds_ids))
            .group_by(EvalCase.dataset_id)
        )
        case_counts = dict(count_result.all())

    items = [
        EvalDatasetResponse(
            id=d.id,
            name=d.name,
            description=d.description,
            case_count=case_counts.get(d.id, 0),
            created_at=d.created_at.isoformat() if d.created_at else "",
            updated_at=d.updated_at.isoformat() if d.updated_at else None,
        )
        for d in datasets
    ]
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if size else 1,
    )


@router.get("/{dataset_id}", response_model=ApiResponse)
async def get_dataset(
    dataset_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    ds = await _get_owned_dataset(dataset_id, current_user.id, db)
    count_result = await db.execute(
        select(func.count(EvalCase.id)).where(EvalCase.dataset_id == dataset_id)
    )
    case_count = count_result.scalar_one()
    return ApiResponse(
        data=EvalDatasetResponse(
            id=ds.id,
            name=ds.name,
            description=ds.description,
            case_count=case_count,
            created_at=ds.created_at.isoformat() if ds.created_at else "",
            updated_at=ds.updated_at.isoformat() if ds.updated_at else None,
        )
    )


@router.put("/{dataset_id}", response_model=ApiResponse)
async def update_dataset(
    dataset_id: str,
    body: EvalDatasetUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    ds = await _get_owned_dataset(dataset_id, current_user.id, db)
    if body.name is not None:
        ds.name = body.name
    if body.description is not None:
        ds.description = body.description
    await db.commit()
    await db.refresh(ds)
    count_result = await db.execute(
        select(func.count(EvalCase.id)).where(EvalCase.dataset_id == dataset_id)
    )
    case_count = count_result.scalar_one()
    return ApiResponse(
        data=EvalDatasetResponse(
            id=ds.id,
            name=ds.name,
            description=ds.description,
            case_count=case_count,
            created_at=ds.created_at.isoformat() if ds.created_at else "",
            updated_at=ds.updated_at.isoformat() if ds.updated_at else None,
        )
    )


@router.delete("/{dataset_id}", response_model=ApiResponse)
async def delete_dataset(
    dataset_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    ds = await _get_owned_dataset(dataset_id, current_user.id, db)
    await db.delete(ds)
    await db.commit()
    return ApiResponse(data={"deleted": dataset_id})


# ---------------------------------------------------------------------------
# Case CRUD (nested under dataset)
# ---------------------------------------------------------------------------


@router.get("/{dataset_id}/cases", response_model=PaginatedResponse)
async def list_cases(
    dataset_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    await _get_owned_dataset(dataset_id, current_user.id, db)
    offset = (page - 1) * size
    total_result = await db.execute(
        select(func.count())
        .select_from(EvalCase)
        .where(EvalCase.dataset_id == dataset_id)
    )
    total = total_result.scalar_one()
    cases_result = await db.execute(
        select(EvalCase)
        .where(EvalCase.dataset_id == dataset_id)
        .order_by(EvalCase.created_at.asc())
        .offset(offset)
        .limit(size)
    )
    cases = cases_result.scalars().all()
    return PaginatedResponse(
        items=[_case_to_response(c) for c in cases],
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if size else 1,
    )


@router.post("/{dataset_id}/cases", response_model=ApiResponse)
async def create_case(
    dataset_id: str,
    body: EvalCaseCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    await _get_owned_dataset(dataset_id, current_user.id, db)
    case = EvalCase(
        dataset_id=dataset_id,
        user_id=current_user.id,
        prompt=body.prompt,
        expected_behavior=body.expected_behavior,
        assertions=body.assertions,
    )
    db.add(case)
    await db.commit()
    await db.refresh(case)
    return ApiResponse(data=_case_to_response(case))


@router.put(
    "/{dataset_id}/cases/{case_id}", response_model=ApiResponse
)
async def update_case(
    dataset_id: str,
    case_id: str,
    body: EvalCaseUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    await _get_owned_dataset(dataset_id, current_user.id, db)
    result = await db.execute(
        select(EvalCase).where(
            EvalCase.id == case_id,
            EvalCase.dataset_id == dataset_id,
        )
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise AppError("case_not_found", status_code=404)
    if body.prompt is not None:
        case.prompt = body.prompt
    if body.expected_behavior is not None:
        case.expected_behavior = body.expected_behavior
    if body.assertions is not None:
        case.assertions = body.assertions
    await db.commit()
    await db.refresh(case)
    return ApiResponse(data=_case_to_response(case))


@router.delete("/{dataset_id}/cases/{case_id}", response_model=ApiResponse)
async def delete_case(
    dataset_id: str,
    case_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    await _get_owned_dataset(dataset_id, current_user.id, db)
    result = await db.execute(
        select(EvalCase).where(
            EvalCase.id == case_id,
            EvalCase.dataset_id == dataset_id,
        )
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise AppError("case_not_found", status_code=404)
    await db.delete(case)
    await db.commit()
    return ApiResponse(data={"deleted": case_id})
