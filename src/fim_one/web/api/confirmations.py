"""Inline confirmation respond endpoint.

When an agent hits a tool flagged ``requires_confirmation=True``, the
approval gate creates a ``ConfirmationRequest`` row with ``mode='inline'``
(or ``mode='channel'`` if routed to Feishu) and blocks its poll until
someone flips the status.

This router exposes the frontend-facing counterpart to Feishu's
``/api/channels/{id}/callback``: an authenticated user clicks Approve /
Reject in the portal → POST ``/api/confirmations/{id}/respond`` → the row
flips, the hook wakes up, the tool proceeds or aborts.

Who may decide was fixed when the request was created and is read off the
row by ``ConfirmationService.may_decide`` (the Feishu callback applies the
same rule).  The request body also accepts an optional ``reason`` string,
stored as the decision note.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import CursorResult, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from typing import cast as _cast

from fim_one.core.assembly.confirmation import ConfirmationService, record_decision
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import AuthMethod, Principal, resolve_principal
from fim_one.db import get_session
from fim_one.web.auth import get_current_user
from fim_one.web.exceptions import AppError
from fim_one.db.models.channel import ConfirmationRequest
from fim_one.db.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/confirmations", tags=["confirmations"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


Decision = Literal["approve", "reject"]


class ConfirmationRespondRequest(BaseModel):
    decision: Decision
    reason: str | None = Field(default=None, max_length=2000)


class ConfirmationRespondResponse(BaseModel):
    status: str
    confirmation_id: str
    decided_at: str


class ConfirmationStatusResponse(BaseModel):
    confirmation_id: str
    status: str
    mode: str
    kind: str = "confirmation"
    tool_name: str
    arguments: dict[str, Any]
    created_at: str
    decided_at: str | None
    approver_user_id: str | None
    # kind="user_question" only: the questions asked and (once answered)
    # the recorded answers plus optional per-question notes.
    questions: list[dict[str, Any]] | None = None
    answers: dict[str, Any] | None = None
    notes: dict[str, str] | None = None


class QuestionAnswerRequest(BaseModel):
    """Body for ``POST /api/confirmations/{id}/answer`` (kind=user_question).

    ``answers`` maps each question's text to the selected option label, a
    list of labels (multi_select), or the user's free "Other" text.
    ``notes`` optionally maps a question's text to a free-text addition
    qualifying the selection ("option 1, but …").  Set ``skip=true``
    (with empty answers) when the user dismisses the card.
    """

    answers: dict[str, Any] = Field(default_factory=dict)
    notes: dict[str, str] = Field(default_factory=dict)
    skip: bool = False


class QuestionAnswerResponse(BaseModel):
    status: str
    confirmation_id: str
    decided_at: str


# ---------------------------------------------------------------------------
# The service, and the former name of its write
# ---------------------------------------------------------------------------

def _service() -> ConfirmationService:
    # Resolved per call: the session factory is the module attribute, which
    # tests point at their own engine.
    from fim_one.db import create_session

    return ConfirmationService(create_session)


async def _actor(db: AsyncSession, user: User) -> Principal:
    return await resolve_principal(db, user_id=user.id, auth=AuthMethod.SESSION)


async def apply_confirmation_decision(
    db: AsyncSession,
    *,
    confirmation_id: str,
    decision: Decision,
    approver_user_id: str | None = None,
    responded_by_open_id: str | None = None,
) -> tuple[str | None, bool, dict[str, Any] | None]:
    """Former name of :func:`fim_one.core.assembly.confirmation.record_decision`.

    Kept for one release. It writes without checking who may decide; new
    callers go through ``ConfirmationService.decide``.
    """
    return await record_decision(
        db,
        confirmation_id=confirmation_id,
        decision=decision,
        decided_by=approver_user_id,
        external_actor=responded_by_open_id,
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/{confirmation_id}/respond",
    response_model=ConfirmationRespondResponse,
)
async def respond_to_confirmation(
    confirmation_id: str,
    body: ConfirmationRespondRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ConfirmationRespondResponse:
    """Approve or reject a pending inline confirmation.

    * ``404`` — confirmation not found.
    * ``403`` — caller may not decide it (``confirmation_forbidden``).
    * ``409`` — already decided / expired.
    * ``200`` — flipped successfully.
    """
    if body.reason:
        logger.info(
            "confirmations.respond: user=%s decision=%s reason=%r",
            current_user.id,
            body.decision,
            body.reason[:200],
        )
    try:
        row = await _service().decide(
            db,
            confirmation_id=confirmation_id,
            actor=await _actor(db, current_user),
            decision=body.decision,
            note=body.reason,
        )
    except AssemblyRefusal as refusal:
        raise AppError.from_refusal(refusal) from None

    decided_at_dt = row.responded_at or datetime.now(timezone.utc)
    if decided_at_dt.tzinfo is None:
        decided_at_dt = decided_at_dt.replace(tzinfo=timezone.utc)

    return ConfirmationRespondResponse(
        status=row.status,
        confirmation_id=confirmation_id,
        decided_at=decided_at_dt.astimezone(timezone.utc).isoformat(),
    )


@router.post(
    "/{confirmation_id}/answer",
    response_model=QuestionAnswerResponse,
)
async def answer_user_question(
    confirmation_id: str,
    body: QuestionAnswerRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> QuestionAnswerResponse:
    """Answer (or dismiss) a pending ``kind=user_question`` request.

    The ``ask_user_question`` tool polls the row; flipping it to
    ``answered`` / ``dismissed`` wakes the paused ReAct run.

    * ``404`` — request not found or not a user question.
    * ``403`` — caller is not the initiator (admins bypass).
    * ``409`` — already answered / dismissed / expired, or raced.
    * ``422`` — no answers given and ``skip`` not set, or unknown question.
    """
    row = (
        await db.execute(
            select(ConfirmationRequest).where(
                ConfirmationRequest.id == confirmation_id
            )
        )
    ).scalar_one_or_none()
    if row is None or str(getattr(row, "kind", "")) != "user_question":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Question request not found.",
        )

    if row.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Question is already {row.status}.",
        )

    # Only the user who asked for the run may answer their own question.
    if not await _service().may_decide(db, row, await _actor(db, current_user)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorised to answer this question.",
        )

    payload: dict[str, Any] = row.payload if isinstance(row.payload, dict) else {}
    known_questions = {
        str(q.get("question") or "")
        for q in (payload.get("questions") or [])
        if isinstance(q, dict)
    }

    answers: dict[str, Any] = {}
    notes: dict[str, str] = {}
    if not body.skip:
        for question, answer in (body.answers or {}).items():
            if question not in known_questions:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown question: {question!r}",
                )
            if isinstance(answer, list):
                answers[question] = [str(a)[:2000] for a in answer[:16]]
            else:
                answers[question] = str(answer)[:2000]
        if not answers:
            raise HTTPException(
                status_code=422,
                detail="Provide at least one answer, or set skip=true.",
            )
        for question, note in (body.notes or {}).items():
            if question not in known_questions:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown question in notes: {question!r}",
                )
            note = str(note).strip()
            if note:
                notes[question] = note[:2000]

    new_status = "dismissed" if body.skip else "answered"
    now = datetime.now(timezone.utc)
    values: dict[str, Any] = {
        "status": new_status,
        "responded_at": now,
        "approver_user_id": current_user.id,
    }
    if not body.skip:
        response_payload: dict[str, Any] = {"answers": answers}
        if notes:
            response_payload["notes"] = notes
        values["response_payload"] = response_payload

    # Conditional UPDATE so concurrent submissions race cleanly — same
    # pattern as apply_confirmation_decision.
    stmt = (
        sa_update(ConfirmationRequest)
        .where(
            ConfirmationRequest.id == confirmation_id,
            ConfirmationRequest.status == "pending",
        )
        .values(**values)
    )
    result = _cast(CursorResult[Any], await db.execute(stmt))
    await db.commit()
    if (result.rowcount or 0) != 1:
        refreshed_status = (
            await db.execute(
                select(ConfirmationRequest.status).where(
                    ConfirmationRequest.id == confirmation_id
                )
            )
        ).scalar_one_or_none()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Question is already {refreshed_status or 'gone'}.",
        )

    return QuestionAnswerResponse(
        status=new_status,
        confirmation_id=confirmation_id,
        decided_at=now.isoformat(),
    )


@router.get(
    "/{confirmation_id}",
    response_model=ConfirmationStatusResponse,
)
async def get_confirmation(
    confirmation_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ConfirmationStatusResponse:
    """Read a single confirmation row.

    Primary use: the inline approval card polls this on mount to rehydrate
    its resolved state after the chat SSE stream tears down and remounts
    the card in a new parent tree (e.g. transitioning from live-streaming
    layout to done-collapsed layout). Without rehydration, the card's
    ``useState`` loses the prior approve/reject decision and the buttons
    reappear.

    Only users who may decide the request may observe it.
    """
    stmt = select(ConfirmationRequest).where(
        ConfirmationRequest.id == confirmation_id
    )
    result = await db.execute(stmt)
    request = result.scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    kind = str(getattr(request, "kind", "") or "confirmation")
    payload: dict[str, Any] = request.payload or {}

    if not await _service().may_decide(db, request, await _actor(db, current_user)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    if kind == "user_question":
        response_payload: dict[str, Any] = (
            request.response_payload
            if isinstance(request.response_payload, dict)
            else {}
        )
        return ConfirmationStatusResponse(
            confirmation_id=request.id,
            status=request.status,
            mode=request.mode or "inline",
            kind=kind,
            tool_name="ask_user_question",
            arguments={},
            created_at=(
                request.created_at.isoformat() if request.created_at else ""
            ),
            decided_at=(
                request.responded_at.isoformat() if request.responded_at else None
            ),
            approver_user_id=request.approver_user_id,
            questions=payload.get("questions") or [],
            answers=response_payload.get("answers"),
            notes=response_payload.get("notes"),
        )

    return ConfirmationStatusResponse(
        confirmation_id=request.id,
        status=request.status,
        mode=request.mode or "channel",
        kind=kind,
        tool_name=str(payload.get("tool_name") or ""),
        arguments=payload.get("arguments") or {},
        created_at=request.created_at.isoformat() if request.created_at else "",
        decided_at=(
            request.responded_at.isoformat() if request.responded_at else None
        ),
        approver_user_id=request.approver_user_id,
    )


__all__ = ["router", "apply_confirmation_decision"]
