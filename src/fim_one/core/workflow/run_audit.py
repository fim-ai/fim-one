"""The audit records of workflow runs, written by the run service.

Every way of starting a run passes :class:`~.run_service.WorkflowRunService`;
it calls these, so each run admitted has one ``workflow.run_started`` and
each refusal one ``workflow.run_refused``, whatever the entry.

- **who**: the person the run is for (:func:`~.run_service.run_identity`).
  A trigger-key run carries the key's prefix as its credential; a
  scheduled run is the system acting for the author (``actor_kind``
  ``system``, ``actor_id`` the author). A run from a signed-in request
  says how the request was signed in (session or API key, and which key).
- **when it cannot fail the run**: the start is added to the session that
  inserts the run record and committed with it; every column is cut to
  its size and the table has no foreign keys, so the record fails only
  when the run record would. A refusal has no run record: it is written in
  a session of its own and a failure is logged.
- **how many refusals**: through the refusal throttle
  (``core/audit/refusals.py``): one record per workflow, person, key and
  reason in ten minutes; the rest are counted into the kind's summary
  record. A retry loop or a holder of a trigger key cannot fill the table.

Run inputs, outputs and step results are never part of a record.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fim_one.core.audit import actions
from fim_one.core.audit import writer as audit
from fim_one.core.audit.context import current_request_auth
from fim_one.core.audit.events import Actor, EventContext, RequestMeta
from fim_one.core.audit.refusals import admit_refusal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "NO_REQUEST",
    "record_cancelled",
    "record_refused",
    "record_started",
    "refusal_code",
    "run_actor",
]

logger = logging.getLogger(__name__)

# Entries whose run is the author's and has no person asking.
_TRIGGER = "trigger"
_SCHEDULE = "schedule"
# A run continued after an approval is not started by the request that
# made the decision: its records carry no request.
NO_REQUEST = RequestMeta(ip=None, user_agent=None, request_id=None)


def refusal_code(error_code: str) -> str:
    """The code of a refusal, without the free text a few codes carry.

    ``invalid_blueprint: <the parser's message>`` names steps of the
    blueprint; only the part before the colon is kept.
    """
    return error_code.split(":", 1)[0].strip()[:64] or "refused"


async def _person(user_id: str | None, db: AsyncSession | None) -> tuple[str | None, bool]:
    """``(username or email, is administrator)`` of *user_id*; ``(None, False)`` if unknown."""
    if not user_id:
        return None, False
    from sqlalchemy import select

    from fim_one.db.models import User

    query = select(User.username, User.email, User.is_admin).where(User.id == user_id)
    try:
        if db is not None:
            row = (await db.execute(query)).first()
        else:
            from fim_one.db import create_session

            async with create_session() as own:
                row = (await own.execute(query)).first()
    except Exception as exc:
        logger.warning("No label for the actor of a run record: %s", type(exc).__name__)
        return None, False
    if row is None:
        return None, False
    return (row[0] or row[1]), bool(row[2])


async def run_actor(
    user_id: str | None,
    mode: str,
    workflow: Any,
    *,
    auth: str | None = None,
    db: AsyncSession | None = None,
) -> Actor:
    """Who a run of *workflow* through entry *mode* is recorded as."""
    label, is_admin = await _person(user_id, db)
    if mode == _SCHEDULE:
        return Actor(user_id=user_id, label=label, kind="system", auth="schedule")
    if mode == _TRIGGER:
        return Actor(
            user_id=user_id,
            label=label,
            kind="admin" if is_admin else "user",
            auth="trigger_key",
            credential_ref=getattr(workflow, "api_key_prefix", None),
        )
    signed_in = current_request_auth()
    return Actor(
        user_id=user_id,
        label=label,
        kind="admin" if is_admin else "user",
        auth=signed_in[0] if signed_in else (auth or "session"),
        credential_ref=signed_in[1] if signed_in else None,
    )


async def record_started(
    workflow: Any,
    *,
    user_id: str,
    mode: str,
    run_id: str | None,
    db: AsyncSession | None = None,
) -> None:
    """``workflow.run_started`` for a run admitted through entry *mode*.

    With *db*, added to the session that inserts the run record (the
    caller commits both). Without (a single-step test, which has no run
    record), written on its own; a failure is logged.
    """
    actor = await run_actor(user_id, mode, workflow, db=db)
    await audit.record(
        actions.WORKFLOW_RUN_STARTED,
        actor=actor,
        org_id=getattr(workflow, "org_id", None),
        target_type="workflow",
        target_id=workflow.id,
        target_label=getattr(workflow, "name", None),
        detail=mode,
        context=EventContext(workflow_run_id=run_id) if run_id else None,
        db=db,
    )


async def record_refused(
    workflow: Any,
    *,
    user_id: str | None,
    mode: str,
    error_code: str,
    run_id: str | None = None,
    not_started: tuple[int, int] | None = None,
    request: RequestMeta | None = None,
) -> bool:
    """``workflow.run_refused`` (``denied``) for a run the service would not run.

    The detail is the refusal's code. *not_started* is ``(count, of)`` for
    the items of a batch that were not started for this reason: one record
    for all of them, not throttled (a batch is admitted at most at the
    runs-per-minute rate). Any other refusal passes the refusal throttle:
    one record per workflow, person, entry and code in ten minutes, the
    rest counted into the next summary record. Written in a session of its
    own; never raises. Returns whether a record was written.
    """
    code = refusal_code(error_code)
    try:
        if not_started is None and not admit_refusal(
            actions.WORKFLOW_RUN_REFUSED,
            f"{workflow.id}|{user_id or '-'}|{mode}|{code}",
        ):
            return False
        actor = await run_actor(user_id, mode, workflow)
        detail = code
        if not_started is not None:
            detail = f"{code}: {not_started[0]} of {not_started[1]} items not started"
        await audit.record(
            actions.WORKFLOW_RUN_REFUSED,
            actor=actor,
            org_id=getattr(workflow, "org_id", None),
            target_type="workflow",
            target_id=workflow.id,
            target_label=getattr(workflow, "name", None),
            outcome="denied",
            detail=detail,
            context=EventContext(workflow_run_id=run_id) if run_id else None,
            request=request,
        )
    except Exception as exc:
        logger.error("Could not record a refused run: %s", type(exc).__name__)
        return False
    return True


async def record_cancelled(workflow: Any, *, run_id: str, actor: Actor, was: str) -> None:
    """``workflow.run_cancelled``: *actor* stopped run *run_id*, which was *was*.

    Written on its own once the run is closed; a failure is logged.
    """
    await audit.record(
        actions.WORKFLOW_RUN_CANCELLED,
        actor=actor,
        org_id=getattr(workflow, "org_id", None),
        target_type="workflow",
        target_id=workflow.id,
        target_label=getattr(workflow, "name", None),
        detail=f"was {was}",
        context=EventContext(workflow_run_id=run_id),
    )
