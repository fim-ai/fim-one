"""The one place a workflow run is started.

Every entry point (interactive SSE run, batch run, trigger key, schedule,
the ``run_workflow`` agent tool, and single-node test) goes through
:class:`WorkflowRunService`. The service decides, once:

- **who the run is for** (:func:`run_identity`): the author for trigger
  keys and schedules; the caller for everything else. That principal is the
  one used for credentials and quota, and the run record is attributed to
  it; the workflow is the binding its steps use resources through.
- **whether it may start** (:class:`RunGate`): account state, token quota and
  plan entitlement, decided by the assembly layer.
- **what the run gets**: the author's env vars only when the principal is the
  author, a full :class:`ExecutionContext` (approval nodes persist and wait),
  the workflow's own run-time limit.

It then creates the ``WorkflowRun`` record, drives the engine, accumulates
node results and token usage, persists the result, releases the rate-limit
slot and delivers the webhook.

A run that reaches a step waiting for a human's decision is **suspended**:
the engine's state is stored on the run record, the run's status becomes
``waiting`` and nothing of it stays in memory. :meth:`WorkflowRunService.resume_run`
continues it from the record, in whichever server process is told the
decision was made (``core/workflow/run_recovery.py`` wires that up and
sweeps for what was missed). Continuing is claimed with one conditional
update of the record, so however many processes try, one does.

While a run executes, the process doing so stamps the record with its
:data:`WORKER_ID` and a heartbeat; that is how a run left behind by a dead
process is told from a live one.

Decisions recorded in ``dev/T10-workflow-refactor.md`` §9 (2026-11-04).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import socket
import time
import uuid
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, cast

from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import (
    AuthMethod,
    Binding,
    CallContext,
    Entry,
    Principal,
    provisional_principal,
)
from fim_one.core.assembly.quota import AssemblyRunGate
from fim_one.core.assembly.sandbox import release_sandbox, workflow_run_sandbox_id

from .node_gates import (
    HUMAN_INTERVENTION_DISABLED,
    HUMAN_INTERVENTION_DISABLED_DETAIL,
    has_disabled_node,
)
from .rate_limiter import WorkflowRateLimiter
from .types import ExecutionContext, NodeResult, NodeStatus, NodeType, WorkflowNodeDef

logger = logging.getLogger(__name__)

DEFAULT_RUN_TIMEOUT_SECONDS = 600

# This server process, as written on the runs it executes.
WORKER_ID = f"{socket.gethostname()[:32]}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

# Statuses of a run that has not ended: it holds a trigger key's
# single-flight, cannot be deleted and is kept by the retention cleanup.
UNFINISHED_STATUSES = ("pending", "running", "waiting")

# How often, at most, a running run's step results are written to its
# record so that someone who opens it sees progress (seconds).
PROGRESS_SAVE_INTERVAL = 1.0

RUN_INTERRUPTED_ERROR = (
    "The server stopped while this run was in progress, so it could not finish. "
    "Start the run again."
)
RUN_NOT_RESUMABLE_ERROR = (
    "The run was waiting for a decision but what it needs to continue could not be read."
)


class RunMode(StrEnum):
    """Entry point that started a run; stored in ``WorkflowRun.mode``."""

    INTERACTIVE = "interactive"
    BATCH = "batch"
    TRIGGER = "trigger"
    SCHEDULE = "schedule"
    TOOL = "tool"
    TEST_NODE = "test_node"


# Unattended entry points run as the workflow's author; the rest as the caller.
AUTHOR_MODES = frozenset({RunMode.TRIGGER, RunMode.SCHEDULE})
# Entry points that only run workflows the author has switched on.
ACTIVE_ONLY_MODES = frozenset({RunMode.TRIGGER, RunMode.SCHEDULE, RunMode.TOOL})
# Entry points with no way to wait for a human decision.
NO_APPROVAL_MODES = frozenset({RunMode.TOOL})
# Entry points whose runs wait for a concurrent-run slot instead of being
# refused when the user is at the limit, and always leave one slot free.
QUEUED_MODES = frozenset({RunMode.BATCH})


class RunRefusedError(AssemblyRefusal):
    """A run was refused before it started.

    ``error_code`` and ``status_code`` follow the API's ``AppError``
    conventions so HTTP entry points can re-raise them unchanged.
    """


# What a run is recorded as in call records and confirmation requests.
_ENTRY: dict[RunMode, Entry] = {
    RunMode.TRIGGER: "trigger",
    RunMode.SCHEDULE: "schedule",
    RunMode.TOOL: "run_workflow",
}
# How the identity of a run was established, by entry point; needed again
# when a suspended run is continued.
_AUTH: dict[RunMode, AuthMethod] = {
    RunMode.TRIGGER: AuthMethod.TRIGGER_KEY,
    RunMode.SCHEDULE: AuthMethod.SCHEDULE,
}


def run_identity(workflow: Any, caller_id: str | None, mode: RunMode) -> tuple[str, AuthMethod]:
    """Whose run this is: ``(user id, how that identity was established)``.

    Trigger keys and schedules have no caller and run as the author. Every
    other entry point runs as the caller, including a shared workflow run by
    someone who is not its author (decision 1, 2026-11-04).
    """
    if mode == RunMode.TRIGGER:
        return workflow.user_id, AuthMethod.TRIGGER_KEY
    if mode == RunMode.SCHEDULE:
        return workflow.user_id, AuthMethod.SCHEDULE
    if not caller_id:
        raise RunRefusedError(
            "run_identity_missing", 403, "A workflow run needs a user identity"
        )
    return caller_id, AuthMethod.SESSION


def workflow_binding(workflow: Any) -> Binding:
    """The workflow as the binding its steps use resources through."""
    return Binding(
        kind="workflow",
        id=workflow.id,
        owner_id=workflow.user_id,
        org_id=getattr(workflow, "org_id", None),
    )


def runs_as_author(ctx: CallContext) -> bool:
    """Whether the run executes as the workflow's author."""
    return ctx.binding is not None and ctx.principal.user_id == ctx.binding.owner_id


def author_env_vars(workflow: Any, ctx: CallContext) -> dict[str, str]:
    """Decrypted workflow env vars, for runs that execute as the author only.

    A run as anyone else gets none: the author's secrets would otherwise be
    interpolated into outputs that person reads.
    """
    if not runs_as_author(ctx):
        return {}
    return decrypt_workflow_env(workflow)


def decrypt_workflow_env(workflow: Any) -> dict[str, str]:
    """Decrypt ``workflow.env_vars_blob``; ``{}`` when unset or unreadable."""
    blob = getattr(workflow, "env_vars_blob", None)
    if not blob:
        return {}
    try:
        from fim_one.core.security.encryption import decrypt_credential

        return dict(decrypt_credential(blob))
    except Exception:
        logger.warning("Failed to decrypt workflow env vars for %s", workflow.id)
        return {}


def run_timeout_ms(workflow: Any) -> int:
    """Whole-run time limit: the workflow's own setting, else 10 minutes."""
    seconds = getattr(workflow, "max_run_duration_seconds", None)
    return (seconds or DEFAULT_RUN_TIMEOUT_SECONDS) * 1000


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


class RunGate(Protocol):
    """Decides whether *principal* may start a run through *binding*.

    Raises :class:`~fim_one.core.assembly.errors.AssemblyRefusal` to refuse.
    The production implementation is the assembly layer's
    :class:`~fim_one.core.assembly.quota.AssemblyRunGate`.
    """

    async def check(self, principal: Principal, binding: Binding | None = None) -> None: ...


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

_NODE_EVENTS = frozenset(
    {
        "node_started",
        "node_resumed",
        "node_completed",
        "node_failed",
        "node_skipped",
        "node_waiting",
    }
)


@dataclass
class RunOutcome:
    """Final state of a run, as persisted."""

    run_id: str
    status: str = "completed"
    outputs: dict[str, Any] = field(default_factory=dict)
    node_results: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    # Why a run that did not complete stopped, as a stable code:
    # ``workflow_step_failed``, ``workflow_run_timed_out``,
    # ``workflow_run_cancelled`` or ``workflow_run_failed``. ``error`` is the
    # full text for the run record; it can name steps and quote exceptions.
    error_code: str | None = None
    duration_ms: int = 0
    total_tokens: int = 0
    # With status ``waiting``: the steps waiting for a decision, as
    # ``{node_id, confirmation_id, kind, expires_at}``.
    waiting: list[dict[str, Any]] = field(default_factory=list)


class StartedRun:
    """A run that passed every gate and has a ``WorkflowRun`` record.

    The engine runs in a background task, so the run finishes, persists and
    releases its slot whether or not anyone is reading :meth:`events`.

    The run may end ``waiting`` instead: suspended at a step that needs a
    human's decision. This object is then done; the continuation is a new
    one (:meth:`WorkflowRunService.resume_run`).
    """

    _END: tuple[str, dict[str, Any]] = ("__end__", {})

    def __init__(
        self,
        service: WorkflowRunService,
        *,
        run_id: str,
        workflow: Any,
        call_context: CallContext,
        mode: RunMode,
        parsed: Any,
        inputs: dict[str, Any],
        context: ExecutionContext,
        engine: Any,
        cancel_event: asyncio.Event,
        holds_slot: bool,
        resume_state: dict[str, Any] | None = None,
        prior_node_results: dict[str, Any] | None = None,
        prior_duration_ms: int = 0,
    ) -> None:
        self.run_id = run_id
        self.workflow = workflow
        self.call_context = call_context
        self.principal = call_context.principal
        self.mode = mode
        self.cancel_event = cancel_event
        self.outcome = RunOutcome(run_id=run_id)
        # Continuing a suspended run: the engine's stored state, and what
        # the run had recorded before it waited.
        self._resume_state = resume_state
        self._prior_duration_ms = prior_duration_ms
        if resume_state is not None:
            self.outcome.node_results = dict(prior_node_results or {})
            self.outcome.total_tokens = int(resume_state.get("total_tokens") or 0)
        self._suspend_state: dict[str, Any] | None = None
        self._progress_saved_at = 0.0
        self._service = service
        self._parsed = parsed
        self._inputs = inputs
        self._context = context
        self._engine = engine
        self._holds_slot = holds_slot
        self._queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self._driver: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._driver is None:
            self._driver = asyncio.create_task(self._drive())

    @property
    def done(self) -> bool:
        """True once the run has been persisted and its slot released."""
        return self._driver is not None and self._driver.done()

    def cancel(self) -> None:
        """Ask the engine to stop; the run is persisted as cancelled."""
        self.cancel_event.set()

    async def wait(self) -> RunOutcome:
        """Run to the end without reading events; return the outcome."""
        self.start()
        assert self._driver is not None
        await asyncio.shield(self._driver)
        return self.outcome

    async def events(
        self, keepalive: float | None = None
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Engine events as ``(name, data)``.

        With *keepalive* set, yields ``("keepalive", {})`` after that many
        seconds without an event, so a stream can keep its connection open.
        """
        self.start()
        while True:
            try:
                if keepalive is None:
                    item = await self._queue.get()
                else:
                    item = await asyncio.wait_for(self._queue.get(), timeout=keepalive)
            except TimeoutError:
                yield "keepalive", {}
                continue
            if item[0] == self._END[0]:
                return
            yield item

    async def _drive(self) -> None:
        start = time.time()
        out = self.outcome
        try:
            if self._resume_state is not None:
                stream = self._engine.execute_streaming(
                    self._parsed,
                    self._inputs,
                    context=self._context,
                    resume_state=self._resume_state,
                )
            else:
                # Called as before P5, so engines that know nothing of
                # resuming (test doubles) still fit.
                stream = self._engine.execute_streaming(
                    self._parsed, self._inputs, context=self._context
                )
            async for name, data in stream:
                if name in _NODE_EVENTS:
                    nid = data.get("node_id", "")
                    out.node_results[nid] = {**(out.node_results.get(nid) or {}), **data}
                    if name == "node_resumed":
                        out.node_results[nid]["status"] = "running"
                    await self._save_progress()
                elif name == "run_suspended":
                    out.status = "waiting"
                    out.error = None
                    out.error_code = None
                    out.total_tokens = int(data.get("total_tokens", 0) or 0)
                    out.waiting = list(data.get("waiting") or [])
                    # Through JSON here, so what is stored is what a later
                    # read gives back; values JSON has no form for become text.
                    self._suspend_state = json.loads(
                        json.dumps(data.get("state") or {}, default=str)
                    )
                    # Readers of the stream learn that the run waits and on
                    # what, not the run's variables.
                    name, data = "run_waiting", {
                        "run_id": self.run_id,
                        "status": "waiting",
                        "waiting": out.waiting,
                        "ts": data.get("ts"),
                    }
                elif name == "run_completed":
                    out.outputs = data.get("outputs", {}) or {}
                    out.status = data.get("status", "completed")
                    out.error = data.get("error") if out.status != "completed" else None
                    if out.status == "completed":
                        out.error_code = None
                    elif out.status == "cancelled":
                        out.error_code = "workflow_run_cancelled"
                    else:
                        out.error_code = data.get("error_code") or "workflow_run_failed"
                    out.total_tokens = int(data.get("total_tokens", 0) or 0)
                elif name == "run_failed":
                    out.status = "failed"
                    out.error = data.get("error")
                    out.error_code = data.get("error_code") or "workflow_run_failed"
                    out.total_tokens = int(data.get("total_tokens", 0) or 0)
                await self._queue.put((name, data))
        except asyncio.CancelledError:
            out.status = "cancelled"
            out.error = "Execution cancelled"
            out.error_code = "workflow_run_cancelled"
        except Exception as exc:
            out.status = "failed"
            out.error = f"{type(exc).__name__}: {exc}"
            out.error_code = "workflow_run_failed"
            logger.exception("Workflow run %s failed", self.run_id)
            await self._queue.put(
                ("run_failed", {"run_id": self.run_id, "status": "failed", "error": out.error})
            )
        finally:
            out.duration_ms = self._prior_duration_ms + int((time.time() - start) * 1000)
            suspended = out.status == "waiting" and self._suspend_state is not None
            # Each step runs even if an earlier one raises; the end marker
            # always goes out last so no reader of events() blocks forever.
            try:
                try:
                    if self._holds_slot:
                        # A waiting run holds no concurrent-run slot.
                        await self._service.rate_limiter.record_run_end(
                            self.principal.user_id, self.run_id
                        )
                finally:
                    try:
                        await self._service._persist(
                            out,
                            # A continued run keeps the start time it has.
                            started_at=start if self._resume_state is None else None,
                            suspend_state=self._suspend_state if suspended else None,
                        )
                    finally:
                        self._service._active_runs.pop(self.run_id, None)
                        if suspended:
                            # The run is not over: its files stay, no webhook
                            # yet. A decision made while other branches were
                            # still running found the run busy; pick it up.
                            try:
                                await self._service._resume_if_decided(
                                    self.run_id, self._suspend_state or {}
                                )
                            except Exception:
                                logger.exception(
                                    "Could not check the decisions of run %s", self.run_id
                                )
                        else:
                            # The files the run's Agent steps wrote go with the run.
                            await release_sandbox(self._context.sandbox_id)
                            try:
                                self._service._fire_webhook(
                                    self.workflow, out, self.mode, self.call_context
                                )
                            except Exception:
                                logger.exception("Webhook dispatch failed for %s", self.run_id)
            finally:
                self._queue.put_nowait(self._END)

    async def _save_progress(self) -> None:
        """Write the step results so far to the run record, at most once a
        :data:`PROGRESS_SAVE_INTERVAL`: whoever opens the run meanwhile
        (another tab, the run history) sees where it is."""
        now = time.monotonic()
        if now - self._progress_saved_at < PROGRESS_SAVE_INTERVAL:
            return
        self._progress_saved_at = now
        await self._service._save_progress(self.run_id, self.outcome.node_results)


class WorkflowRunService:
    """Starts workflow runs for every entry point. See the module docstring."""

    def __init__(
        self,
        *,
        gate: RunGate | None = None,
        rate_limiter: WorkflowRateLimiter | None = None,
        engine_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.gate: RunGate = gate or AssemblyRunGate()
        self.rate_limiter = rate_limiter or WorkflowRateLimiter()
        self._engine_factory = engine_factory
        # Runs executing in this process, by run id, so any entry point's run
        # can be cancelled (the engine stops; the run is persisted cancelled).
        self._active_runs: dict[str, StartedRun] = {}
        # One lock per workflow with a single-flight start in progress; an
        # entry goes away once nobody holds or waits for its lock.
        self._single_flight_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    def cancel_run(self, run_id: str) -> bool:
        """Signal a run executing in this process to stop; False if not here."""
        started = self._active_runs.get(run_id)
        if started is None:
            return False
        started.cancel()
        return True

    def active_run_ids(self) -> list[str]:
        """Runs executing in this process right now."""
        return list(self._active_runs)

    async def cancel_stored_run(self, run_id: str) -> bool:
        """Cancel a run that is not executing in this process, on its record.

        A run waiting for a decision is closed here and now: its open
        requests are closed with it and its files removed. A run another
        server process is executing is marked cancelled; that process sees
        the mark at its next heartbeat and stops. False when the run had
        already ended (or does not exist).
        """
        ended = await self._end_stored_run(
            run_id, ("pending", "running", "waiting"), "cancelled", "Execution cancelled"
        )
        return ended is not None

    async def _end_stored_run(
        self,
        run_id: str,
        from_statuses: tuple[str, ...],
        status: str,
        error: str,
        *,
        stale_before: datetime | None = None,
    ) -> str | None:
        """Close a run on its record alone; the status it had, or ``None``.

        One conditional update, so of several callers (two workers sweeping,
        a cancel beside a decision) one closes the run. With *stale_before*
        only a run whose heartbeat is older than that is closed. The run's
        open confirmation requests are closed in the same transaction; a
        run that was not executing anywhere also loses its sandbox.
        """
        from sqlalchemy import CursorResult, func, select, update

        from fim_one.core.assembly.confirmation import close_requests_of_runs
        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        now = datetime.now(UTC)
        async with create_session() as db:
            previous = (
                await db.execute(select(WorkflowRun.status).where(WorkflowRun.id == run_id))
            ).scalar_one_or_none()
            if previous is None or previous not in from_statuses:
                return None
            conditions = [WorkflowRun.id == run_id, WorkflowRun.status == previous]
            if stale_before is not None:
                conditions.append(
                    func.coalesce(
                        WorkflowRun.heartbeat_at, WorkflowRun.started_at, WorkflowRun.created_at
                    )
                    < stale_before
                )
            result = await db.execute(
                update(WorkflowRun)
                .where(*conditions)
                .values(status=status, error=error, completed_at=now, suspend_state=None)
                .execution_options(synchronize_session=False)
            )
            if (cast(CursorResult[Any], result).rowcount or 0) != 1:
                await db.rollback()
                return None
            await close_requests_of_runs(db, [run_id])
            await db.commit()
        if previous == "waiting" or stale_before is not None:
            # Nothing is executing it: nobody else will remove its files.
            await release_sandbox(workflow_run_sandbox_id(run_id))
        return str(previous)

    # -- checks ---------------------------------------------------------------

    async def authorize(
        self,
        workflow: Any,
        caller_id: str | None,
        mode: RunMode,
        *,
        admit_batch: bool = False,
    ) -> tuple[CallContext, Any]:
        """Run every pre-start check; return ``(call_context, parsed_blueprint)``.

        The call context names the principal the run executes as and the
        workflow as its binding.

        *admit_batch* is for a batch, which is checked once for all its
        items: the batch counts as one start against the caller's
        runs-per-minute limit. Its items then each take a concurrent-run
        slot in :meth:`start_run`.

        Raises :class:`RunRefusedError` on the first failed check.
        """
        user_id, auth = run_identity(workflow, caller_id, mode)
        if mode in ACTIVE_ONLY_MODES and not workflow.is_active:
            raise RunRefusedError("workflow_inactive", 403, "Workflow is inactive")
        blueprint = workflow.blueprint
        if not blueprint or not blueprint.get("nodes"):
            raise RunRefusedError("blueprint_empty", 400)

        ctx = await self._admit(workflow, user_id, auth, mode)

        from .parser import BlueprintValidationError, parse_blueprint

        try:
            parsed = parse_blueprint(blueprint)
        except BlueprintValidationError as exc:
            raise RunRefusedError(f"invalid_blueprint: {exc}", 400) from None

        if has_disabled_node(parsed.nodes):
            # Stored before the switch was turned off: refuse the whole run
            # rather than skip the step.
            raise RunRefusedError(
                HUMAN_INTERVENTION_DISABLED, 409, HUMAN_INTERVENTION_DISABLED_DETAIL
            )

        if mode in NO_APPROVAL_MODES and any(
            node.type == NodeType.HUMAN_INTERVENTION for node in parsed.nodes
        ):
            raise RunRefusedError(
                "workflow_has_approval_step",
                409,
                "The workflow contains a human-approval step and cannot run here",
            )

        if admit_batch:
            allowed, rate_error = await self.rate_limiter.admit_batch(ctx.principal.user_id)
            if not allowed:
                raise RunRefusedError("workflow_rate_limited", 429, rate_error)
        return ctx, parsed

    async def _admit(
        self, workflow: Any, user_id: str, auth: AuthMethod, mode: RunMode
    ) -> CallContext:
        """Pass the gate; the call context of a run of *workflow* as *user_id*."""
        # The gate checks both accounts; steps that read more of the
        # principal than its id (an approval's organizations) resolve it.
        principal = provisional_principal(user_id, auth=auth)
        binding = workflow_binding(workflow)
        try:
            await self.gate.check(principal, binding)
        except RunRefusedError:
            raise
        except AssemblyRefusal as refusal:
            raise RunRefusedError(
                refusal.error_code, refusal.status_code, refusal.detail, refusal.detail_args
            ) from None
        return CallContext(
            principal=principal,
            binding=binding,
            entry=_ENTRY.get(mode, "workflow_node"),
        )

    async def would_refuse(
        self, workflow: Any, caller_id: str | None, mode: RunMode
    ) -> RunRefusedError | None:
        """The refusal a run started now would get from :meth:`authorize`.

        ``None`` when it would pass. Read-only: the same identity, account,
        quota, plan and blueprint checks, with no run created and no
        rate-limit budget used. A run can therefore still be refused for
        what is only decided when it starts: the concurrent-run and
        runs-per-minute limits, and a trigger key's single-flight rule.
        """
        try:
            await self.authorize(workflow, caller_id, mode)
        except RunRefusedError as exc:
            return exc
        return None

    # -- runs -----------------------------------------------------------------

    async def start_run(
        self,
        workflow: Any,
        *,
        caller_id: str | None,
        inputs: dict[str, Any] | None,
        mode: RunMode,
        cancel_event: asyncio.Event | None = None,
        depth: int = 0,
        authorized: tuple[CallContext, Any] | None = None,
        acquire_slot: bool = True,
        single_flight: bool = False,
        record_refusal: bool = False,
    ) -> StartedRun:
        """Check, record and start a run; the engine starts immediately.

        *authorized* skips the checks when the caller already ran
        :meth:`authorize` (a batch checks once for all its items).
        Every run takes one of the principal's concurrent-run slots unless
        *acquire_slot* is off: a batch item waits for one, any other run is
        refused when none is free.
        *single_flight* refuses while another run of the workflow is pending
        or running; that is decided before a slot is taken, so a refused
        request costs the principal nothing. *record_refusal* writes a failed
        ``WorkflowRun`` when a check refuses, for entry points with nobody
        to tell (schedules).
        """
        inputs = inputs or {}
        try:
            ctx, parsed = authorized or await self.authorize(workflow, caller_id, mode)
            if single_flight:
                # Early answer without taking the workflow's lock. Requests
                # that arrive together all pass it; the check that decides
                # is made with the insert, in _insert_single_flight_run.
                await self._refuse_if_running(workflow.id)
        except RunRefusedError as exc:
            if record_refusal:
                await self._record_refusal(workflow, caller_id, mode, inputs, exc)
            raise

        principal = ctx.principal
        run_id = str(uuid.uuid4())
        fields = self._run_fields(run_id, workflow, principal, mode, inputs)
        record_created = False
        try:
            if single_flight:
                await self._insert_single_flight_run(
                    workflow.id,
                    fields,
                    take_slot=(
                        (lambda: self._acquire_slot(principal.user_id, run_id, mode))
                        if acquire_slot
                        else None
                    ),
                )
            else:
                if acquire_slot:
                    await self._acquire_slot(principal.user_id, run_id, mode)
                await self._insert_run(**fields)
            record_created = True

            from fim_one.db import create_session

            cancel_event = cancel_event or asyncio.Event()
            env_vars = author_env_vars(workflow, ctx)
            context = ExecutionContext(
                run_id=run_id,
                user_id=principal.user_id,
                workflow_id=workflow.id,
                env_vars=env_vars,
                db_session_factory=create_session,
                depth=depth,
                cancel_event=cancel_event,
                call_context=dataclasses.replace(ctx, workflow_run_id=run_id),
                sandbox_id=workflow_run_sandbox_id(run_id),
                # An agent calling the workflow as a tool waits for its
                # result and has nobody to ask.
                can_suspend=mode not in NO_APPROVAL_MODES,
            )
            engine = self._make_engine(
                max_concurrency=5,
                cancel_event=cancel_event,
                env_vars=env_vars,
                run_id=run_id,
                user_id=principal.user_id,
                workflow_id=workflow.id,
                workflow_timeout_ms=run_timeout_ms(workflow),
            )
            started = StartedRun(
                self,
                run_id=run_id,
                workflow=workflow,
                call_context=ctx,
                mode=mode,
                parsed=parsed,
                inputs=inputs,
                context=context,
                engine=engine,
                cancel_event=cancel_event,
                holds_slot=acquire_slot,
            )
            self._active_runs[run_id] = started
            # Start now, not on first read: the run must persist and release
            # its slot even if the caller goes away before reading events.
            started.start()
            return started
        except BaseException:
            if acquire_slot:
                # Also reached when no slot was taken (refused, or lost the
                # single-flight race); releasing is then a no-op.
                await asyncio.shield(
                    self.rate_limiter.record_run_end(principal.user_id, run_id)
                )
            if record_created:
                await asyncio.shield(self._mark_failed(run_id, "Run could not start"))
            raise

    async def run_to_completion(self, workflow: Any, **kwargs: Any) -> RunOutcome:
        """:meth:`start_run`, then wait for the outcome.

        The outcome's status is ``waiting`` when the run stopped at a step
        that needs a human's decision; it is continued once that is made.
        """
        started = await self.start_run(workflow, **kwargs)
        return await started.wait()

    # -- continuing a suspended run -------------------------------------------

    async def resume_run(self, run_id: str) -> StartedRun | None:
        """Continue a run that is waiting for a decision; ``None`` if not ours to continue.

        Safe to call from anywhere, any number of times: the run is
        claimed by one conditional update of its record (``waiting`` to
        ``running``), so of all callers, in this process or others, one
        continues it and the rest get ``None``. The steps that were
        waiting are run again and read their requests: one decided or
        expired ends its wait, one still open makes the run wait again.

        The run goes on as it was admitted: the same principal, the
        blueprint it started with, no second pass through the gate and no
        concurrent-run slot.
        """
        from sqlalchemy import CursorResult, select, update

        from fim_one.db import create_session
        from fim_one.db.models import Workflow, WorkflowRun

        async with create_session() as db:
            result = await db.execute(
                update(WorkflowRun)
                .where(WorkflowRun.id == run_id, WorkflowRun.status == "waiting")
                .values(status="running", worker_id=WORKER_ID, heartbeat_at=datetime.now(UTC))
                .execution_options(synchronize_session=False)
            )
            await db.commit()
            if (cast(CursorResult[Any], result).rowcount or 0) != 1:
                return None
            run = (
                await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            ).scalar_one()
            workflow = (
                await db.execute(select(Workflow).where(Workflow.id == run.workflow_id))
            ).scalar_one_or_none()
            if workflow is not None:
                db.expunge(workflow)
            db.expunge(run)

        try:
            if workflow is None:
                raise ValueError("the workflow no longer exists")
            started = self._continue(run, workflow)
        except Exception:
            logger.exception("Workflow run %s could not be continued", run_id)
            await self._end_stored_run(run_id, ("running",), "failed", RUN_NOT_RESUMABLE_ERROR)
            return None
        self._active_runs[run_id] = started
        started.start()
        return started

    def _continue(self, run: Any, workflow: Any) -> StartedRun:
        """The :class:`StartedRun` that goes on from *run*'s stored state."""
        from fim_one.db import create_session

        from .engine import SUSPEND_STATE_VERSION
        from .parser import parse_blueprint

        state = run.suspend_state
        if not isinstance(state, dict) or state.get("version") != SUSPEND_STATE_VERSION:
            raise ValueError("no usable suspend state")
        mode = RunMode(run.mode) if run.mode else RunMode.INTERACTIVE
        ctx = CallContext(
            principal=provisional_principal(run.user_id, auth=_AUTH.get(mode, AuthMethod.SESSION)),
            binding=workflow_binding(workflow),
            entry=_ENTRY.get(mode, "workflow_node"),
        )
        parsed = parse_blueprint(run.blueprint_snapshot)
        cancel_event = asyncio.Event()
        env_vars = author_env_vars(workflow, ctx)
        context = ExecutionContext(
            run_id=run.id,
            user_id=run.user_id,
            workflow_id=workflow.id,
            env_vars=env_vars,
            db_session_factory=create_session,
            cancel_event=cancel_event,
            call_context=dataclasses.replace(ctx, workflow_run_id=run.id),
            sandbox_id=workflow_run_sandbox_id(run.id),
            can_suspend=True,
        )
        engine = self._make_engine(
            max_concurrency=5,
            cancel_event=cancel_event,
            env_vars=env_vars,
            run_id=run.id,
            user_id=run.user_id,
            workflow_id=workflow.id,
            workflow_timeout_ms=run_timeout_ms(workflow),
        )
        return StartedRun(
            self,
            run_id=run.id,
            workflow=workflow,
            call_context=ctx,
            mode=mode,
            parsed=parsed,
            inputs=run.inputs or {},
            context=context,
            engine=engine,
            cancel_event=cancel_event,
            holds_slot=False,
            resume_state=state,
            prior_node_results=run.node_results if isinstance(run.node_results, dict) else None,
            prior_duration_ms=int(run.duration_ms or 0),
        )

    def schedule_resume(self, run_id: str) -> None:
        """Continue *run_id* in a background task of this process.

        The one seam between "a decision was made" and "the run goes on";
        a task queue would take its place here.
        """
        from fim_one.core.utils import spawn_background

        spawn_background(self._resume_logged(run_id))

    async def _resume_logged(self, run_id: str) -> None:
        try:
            await self.resume_run(run_id)
        except Exception:
            logger.exception("Continuing workflow run %s failed", run_id)

    async def _resume_if_decided(self, run_id: str, state: dict[str, Any]) -> None:
        if await requests_settled(state):
            self.schedule_resume(run_id)

    async def test_node(
        self,
        workflow: Any,
        *,
        caller_id: str,
        node: WorkflowNodeDef,
        variables: dict[str, Any],
        env_overrides: dict[str, str],
    ) -> tuple[NodeResult, dict[str, Any]]:
        """Execute one node in isolation; return its result and the variables.

        Same principal, gate and env rules as a full run. There is no
        ``db_session_factory``, so an approval node is auto-approved. Token
        usage is recorded as a ``test_node`` run so it counts toward quota.
        """
        from fim_one.core.model.usage import UsageTracker

        from .nodes import get_executor
        from .variable_store import VariableStore

        user_id, auth = run_identity(workflow, caller_id, RunMode.TEST_NODE)
        if has_disabled_node([node]):
            raise RunRefusedError(
                HUMAN_INTERVENTION_DISABLED, 409, HUMAN_INTERVENTION_DISABLED_DETAIL
            )
        ctx = await self._admit(workflow, user_id, auth, RunMode.TEST_NODE)
        principal = ctx.principal

        env_vars = {**author_env_vars(workflow, ctx), **env_overrides}
        store = VariableStore(env_vars=env_vars)
        for key, value in variables.items():
            await store.set(key, value)

        usage_tracker = UsageTracker()
        test_run_id = f"test-{uuid.uuid4()}"
        context = ExecutionContext(
            run_id=test_run_id,
            sandbox_id=workflow_run_sandbox_id(test_run_id),
            user_id=principal.user_id,
            workflow_id=workflow.id,
            env_vars=env_vars,
            usage_tracker=usage_tracker,
            # A single-step test has no run record for a call to point at.
            call_context=ctx,
        )
        executor = get_executor(node.type)

        t0 = time.time()
        try:
            result = await asyncio.wait_for(
                executor.execute(node, store, context), timeout=node.timeout_ms / 1000
            )
        except TimeoutError:
            result = NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"Node execution timed out after {node.timeout_ms / 1000:.0f} seconds",
                duration_ms=int((time.time() - t0) * 1000),
            )
        except Exception as exc:
            logger.warning(
                "Test-node execution error for node %s in workflow %s: %s",
                node.id,
                workflow.id,
                exc,
            )
            result = NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=int((time.time() - t0) * 1000),
            )
        finally:
            await release_sandbox(context.sandbox_id)

        total_tokens = int(usage_tracker.get_summary().total_tokens or 0)
        if total_tokens > 0:
            now = datetime.now(UTC)
            await self._insert_run(
                workflow_id=workflow.id,
                user_id=principal.user_id,
                blueprint_snapshot={"nodes": [], "edges": [], "test_node": node.id},
                inputs=None,
                status="completed" if result.status == NodeStatus.COMPLETED else "failed",
                mode=RunMode.TEST_NODE,
                started_at=now,
                completed_at=now,
                duration_ms=result.duration_ms,
                total_tokens=total_tokens,
            )
        return result, await store.snapshot()

    # -- internals ------------------------------------------------------------

    def _make_engine(self, **kwargs: Any) -> Any:
        if self._engine_factory is not None:
            return self._engine_factory(**kwargs)
        from .engine import WorkflowEngine

        return WorkflowEngine(**kwargs)

    async def _refuse_if_running(self, workflow_id: str, db: Any = None) -> None:
        """Refuse when the workflow has a pending or running run.

        With *db*, the check runs in that session (and its transaction).
        """
        from sqlalchemy import func, select

        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        # A run waiting for a decision is still in flight.
        query = select(func.count()).where(
            WorkflowRun.workflow_id == workflow_id,
            WorkflowRun.status.in_(UNFINISHED_STATUSES),
        )
        if db is not None:
            running = (await db.execute(query)).scalar_one()
        else:
            async with create_session() as own:
                running = (await own.execute(query)).scalar_one()
        if running > 0:
            raise RunRefusedError(
                "workflow_already_running",
                429,
                "Workflow already has a running execution. Please wait for it to complete.",
            )

    async def _insert_run(self, **fields: Any) -> None:
        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        async with create_session() as db:
            db.add(WorkflowRun(**fields))
            await db.commit()

    @staticmethod
    def _run_fields(
        run_id: str,
        workflow: Any,
        principal: Principal,
        mode: RunMode,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Column values of the ``WorkflowRun`` record for a run that starts."""
        return {
            "id": run_id,
            "workflow_id": workflow.id,
            "user_id": principal.user_id,
            "blueprint_snapshot": workflow.blueprint,
            "inputs": inputs,
            "status": "running",
            "mode": mode.value,
            "started_at": datetime.now(UTC),
            "worker_id": WORKER_ID,
            "heartbeat_at": datetime.now(UTC),
        }

    async def _acquire_slot(self, user_id: str, run_id: str, mode: RunMode) -> None:
        """Take one of the user's concurrent-run slots for *run_id*.

        A batch item waits for one; any other run is refused when none is
        free or the user is over the runs-per-minute limit.
        """
        if mode in QUEUED_MODES:
            await self.rate_limiter.acquire_when_free(user_id, run_id)
            return
        allowed, rate_error = await self.rate_limiter.acquire(user_id, run_id)
        if not allowed:
            raise RunRefusedError("workflow_rate_limited", 429, rate_error)

    async def _insert_single_flight_run(
        self,
        workflow_id: str,
        fields: dict[str, Any],
        *,
        take_slot: Callable[[], Awaitable[None]] | None,
    ) -> None:
        """Insert a run record unless the workflow already has a run in flight.

        The check and the insert are one step, so of several requests that
        arrive together exactly one gets a run. The others are refused with
        ``workflow_already_running`` before *take_slot* is called: only the
        request that will run takes a concurrent-run slot and counts
        against runs-per-minute. Two things make it one step:

        - a row lock on the workflow, held for the transaction that checks
          and inserts. It serialises the requests across server processes
          on PostgreSQL. SQLite has no row locks and ignores it;
        - a per-workflow lock in this process, which covers SQLite as long
          as one process serves the instance.
        """
        from sqlalchemy import select

        from fim_one.db import create_session
        from fim_one.db.models import Workflow, WorkflowRun

        lock = self._single_flight_locks.get(workflow_id)
        if lock is None:
            lock = self._single_flight_locks[workflow_id] = asyncio.Lock()
        async with lock, create_session() as db:
            # NO KEY UPDATE: does not block the foreign-key check of other
            # runs being inserted for this workflow, only other holders of
            # this same lock.
            await db.execute(
                select(Workflow.id)
                .where(Workflow.id == workflow_id)
                .with_for_update(key_share=True)
            )
            await self._refuse_if_running(workflow_id, db)
            if take_slot is not None:
                await take_slot()
            db.add(WorkflowRun(**fields))
            await db.commit()

    async def _mark_failed(self, run_id: str, error: str) -> None:
        await self._persist(
            RunOutcome(run_id=run_id, status="failed", error=error), started_at=time.time()
        )

    async def _record_refusal(
        self,
        workflow: Any,
        caller_id: str | None,
        mode: RunMode,
        inputs: dict[str, Any],
        exc: RunRefusedError,
    ) -> None:
        user_id = workflow.user_id if mode in AUTHOR_MODES else caller_id
        if not user_id:
            return
        now = datetime.now(UTC)
        try:
            await self._insert_run(
                workflow_id=workflow.id,
                user_id=user_id,
                blueprint_snapshot=workflow.blueprint or {},
                inputs=inputs,
                status="failed",
                mode=mode.value,
                started_at=now,
                completed_at=now,
                duration_ms=0,
                error=exc.error_code,
            )
        except Exception:
            logger.exception("Failed to record refused %s run of %s", mode, workflow.id)

    async def _persist(
        self,
        out: RunOutcome,
        *,
        started_at: float | None,
        suspend_state: dict[str, Any] | None = None,
    ) -> None:
        """Write how the run stands to its record.

        With *suspend_state* the run is waiting: the state is stored and
        the run is not completed. Otherwise the run has ended, and the
        requests it still had open are closed with it: nobody is asked to
        decide for a run that is over.

        Only a record still ``running`` is written. One that is not was
        closed by someone else meanwhile (cancelled from another process,
        or declared dead by the recovery sweep) and stays as they left it.
        """
        from sqlalchemy import select

        from fim_one.core.assembly.confirmation import close_requests_of_runs
        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        try:
            async with create_session() as db:
                run = (
                    await db.execute(select(WorkflowRun).where(WorkflowRun.id == out.run_id))
                ).scalar_one_or_none()
                if run is None:
                    return
                if run.status != "running":
                    logger.warning(
                        "Workflow run %s is already %s; its result (%s) is not recorded",
                        out.run_id,
                        run.status,
                        out.status,
                    )
                    await close_requests_of_runs(db, [out.run_id])
                    await db.commit()
                    return
                run.status = out.status
                run.outputs = out.outputs or None
                run.node_results = out.node_results or None
                if started_at is not None:
                    run.started_at = datetime.fromtimestamp(started_at, tz=UTC)
                run.duration_ms = out.duration_ms
                run.error = out.error
                run.total_tokens = out.total_tokens
                run.suspend_state = suspend_state
                if suspend_state is None:
                    run.completed_at = datetime.now(UTC)
                    await close_requests_of_runs(db, [out.run_id])
                await db.commit()
        except Exception:
            logger.exception("Failed to persist workflow run %s", out.run_id)

    async def _save_progress(self, run_id: str, node_results: dict[str, Any]) -> None:
        from sqlalchemy import update

        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        try:
            async with create_session() as db:
                await db.execute(
                    update(WorkflowRun)
                    .where(WorkflowRun.id == run_id, WorkflowRun.status == "running")
                    .values(node_results=dict(node_results), heartbeat_at=datetime.now(UTC))
                    .execution_options(synchronize_session=False)
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to save the progress of workflow run %s", run_id)

    def _fire_webhook(
        self, workflow: Any, out: RunOutcome, mode: RunMode, ctx: CallContext
    ) -> None:
        """POST the run result to the author's webhook.

        The webhook belongs to the author, so a run by anyone else is
        reported without its run id, outputs or error: only the event,
        workflow, status and duration (decision 2, 2026-11-04).
        """
        url = getattr(workflow, "webhook_url", None)
        if not url or out.status not in ("completed", "failed"):
            return
        payload: dict[str, Any] = {
            "event": "run_completed" if out.status == "completed" else "run_failed",
            "workflow_id": workflow.id,
            "status": out.status,
            "duration_ms": out.duration_ms,
        }
        if runs_as_author(ctx):
            payload.update(
                run_id=out.run_id,
                outputs=out.outputs or None,
                error=out.error,
                completed_at=datetime.now(UTC).isoformat(),
            )
        if mode == RunMode.SCHEDULE:
            payload["trigger"] = "scheduled"

        from fim_one.core.utils import spawn_background

        spawn_background(deliver_webhook(url, payload))


async def requests_settled(state: dict[str, Any]) -> bool:
    """Whether a waiting run has something to go on with.

    True when any request its steps wait on (*state* is the run's
    ``suspend_state``) is decided, closed, past its deadline or gone: the
    step waiting on it can then finish, one way or the other. False while
    every one of them is still open, and for a state that names none.
    """
    from sqlalchemy import select

    from fim_one.db import create_session
    from fim_one.db.models.channel import ConfirmationRequest

    waiting = state.get("waiting") if isinstance(state, dict) else None
    ids = [
        str(record.get("confirmation_id"))
        for record in (waiting or {}).values()
        if isinstance(record, dict) and record.get("confirmation_id")
    ]
    if not ids:
        # A waiting step that names no request can only fail; let it.
        return bool(waiting)
    async with create_session() as db:
        rows = (
            await db.execute(
                select(
                    ConfirmationRequest.id,
                    ConfirmationRequest.status,
                    ConfirmationRequest.expires_at,
                ).where(ConfirmationRequest.id.in_(ids))
            )
        ).all()
    if len(rows) < len(set(ids)):
        return True
    now = datetime.now(UTC)
    for _id, status, expires_at in rows:
        if status != "pending":
            return True
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                return True
    return False


async def deliver_webhook(webhook_url: str, payload: dict[str, Any]) -> None:
    """POST a run result to the workflow's webhook URL; log, never raise."""
    from fim_one.core.security import get_safe_async_client

    try:
        async with get_safe_async_client(timeout=10.0) as client:
            resp = await client.post(
                webhook_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Source": "fim-one",
                },
            )
            logger.info("Webhook delivered to %s — status %d", webhook_url, resp.status_code)
    except Exception:
        logger.exception("Webhook delivery failed for %s", webhook_url)


_default_service: WorkflowRunService | None = None


def get_run_service() -> WorkflowRunService:
    """The process-wide service; its rate limiter is shared by every entry point."""
    global _default_service
    if _default_service is None:
        _default_service = WorkflowRunService()
    return _default_service
