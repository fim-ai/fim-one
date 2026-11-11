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
from fim_one.core.usage.meter import OpenMeter, UsageScope, open_meter

from . import run_audit
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

def db_now(dialect: str) -> Any:
    """The database's clock, as an SQL expression.

    Heartbeats are stamped and judged by it alone, so the clocks of the
    hosts the server processes run on do not have to agree.
    """
    from sqlalchemy import func

    if dialect == "sqlite":
        # With milliseconds; CURRENT_TIMESTAMP has whole seconds only.
        return func.strftime("%Y-%m-%d %H:%M:%f", "now")
    # The moment the statement runs, not the moment its transaction began:
    # a stamp that waited behind a lock must not come out old.
    return func.clock_timestamp()


def db_seconds_ago(dialect: str, seconds: float) -> Any:
    """The database's clock *seconds* ago, as an SQL expression."""
    from sqlalchemy import func

    if dialect == "sqlite":
        return func.strftime("%Y-%m-%d %H:%M:%f", "now", f"-{seconds:.3f} seconds")
    return func.clock_timestamp() - func.make_interval(0, 0, 0, 0, 0, 0, seconds)


def _dialect_of(db: Any) -> str:
    return str(db.get_bind().dialect.name)


RUN_INTERRUPTED_ERROR = (
    "The server stopped while this run was in progress, so it could not finish. "
    "Start the run again."
)
RUN_STATE_TOO_LARGE = "workflow_suspend_state_too_large"
RUN_WAITING_LIMIT = "workflow_waiting_limit_reached"
RUN_SLOT_TIMEOUT = "workflow_resume_slot_timeout"


def resume_slot_wait_seconds() -> float:
    """How long a continued run may queue for a concurrent-run slot
    (``WORKFLOW_RESUME_SLOT_WAIT_SECONDS``)."""
    return float(os.getenv("WORKFLOW_RESUME_SLOT_WAIT_SECONDS", "3600"))


def waiting_runs_limit() -> int:
    """How many of one user's runs may wait for a decision at once
    (``WORKFLOW_MAX_WAITING_RUNS_PER_USER``; 0 = no limit)."""
    return int(os.getenv("WORKFLOW_MAX_WAITING_RUNS_PER_USER", "20"))


def suspend_state_max_bytes() -> int:
    """How large a waiting run's stored state may be (``WORKFLOW_SUSPEND_STATE_MAX_BYTES``)."""
    return int(os.getenv("WORKFLOW_SUSPEND_STATE_MAX_BYTES", str(5 * 1024 * 1024)))


class SuspendStateTooLargeError(Exception):
    def __init__(self, size: int, limit: int) -> None:
        super().__init__(
            f"The run cannot wait here: what it would have to keep to go on later "
            f"({size:,} bytes of step outputs and variables) is over the limit of "
            f"{limit:,} bytes. Make the steps before the approval produce less."
        )


def seal_state(state: dict[str, Any]) -> dict[str, Any]:
    """The engine's state as stored on a waiting run's record.

    The state holds the run's variables: step outputs, which can be
    connector responses. It is stored encrypted with the credential key
    (``sealed``). What the sweep and the API read without the key (which
    request each waiting step waits on) is kept in a column of its own,
    ``WorkflowRun.waiting_on``.
    Through JSON first, so what is stored is what a later read gives back;
    values JSON has no form for become text. Raises
    :class:`SuspendStateTooLargeError` over the limit. CPU-bound: call it off the
    event loop.
    """
    from fim_one.core.security.encryption import encrypt_credential

    text = json.dumps(state, default=str, ensure_ascii=False)
    size, limit = len(text.encode("utf-8")), suspend_state_max_bytes()
    if size > limit:
        raise SuspendStateTooLargeError(size, limit)
    plain = json.loads(text)
    return {"version": plain.get("version"), "sealed": encrypt_credential(plain)}


def unseal_state(stored: Any) -> dict[str, Any]:
    """The engine's state from a waiting run's record; ``ValueError`` if unreadable."""
    from fim_one.core.security.encryption import decrypt_credential

    if not isinstance(stored, dict) or not isinstance(stored.get("sealed"), str):
        raise ValueError("no usable suspend state")
    try:
        state = decrypt_credential(stored["sealed"])
    except Exception as exc:
        raise ValueError(f"the suspend state cannot be decrypted: {exc}") from None
    if not isinstance(state, dict):
        raise ValueError("no usable suspend state")
    return state


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


class _NoSlotInTimeError(Exception):
    """A continued run queued for a concurrent-run slot for too long."""


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


# The status a step is in after each of these events (the others carry it).
_EVENT_STATUS = {
    "node_started": "running",
    "node_resumed": "running",
    "node_failed": "failed",
    "node_skipped": "skipped",
}


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
        queue_for_slot: bool = False,
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
        self._queue_for_slot = queue_for_slot
        self._suspend_state: dict[str, Any] | None = None
        self._waiting_on: dict[str, Any] = {}
        self._progress_saved_at = 0.0
        self._service = service
        self._parsed = parsed
        self._inputs = inputs
        self._context = context
        self._engine = engine
        self._holds_slot = holds_slot
        self._queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self._driver: asyncio.Task[None] | None = None
        # Who asked first to stop it, and from where; recorded if it is
        # stored as cancelled (the driver runs in the context of whoever
        # started the run, not of the request that cancels it).
        self.cancelled_by: Any = None
        self._cancel_request: Any = None

    def start(self) -> None:
        if self._driver is None:
            self._driver = asyncio.create_task(self._drive())

    @property
    def done(self) -> bool:
        """True once the run has been persisted and its slot released."""
        return self._driver is not None and self._driver.done()

    def cancel(self, *, by: Any = None) -> None:
        """Ask the engine to stop; the run is persisted as cancelled.

        *by* is the audit actor asking; the first one is kept.
        """
        if by is not None and self.cancelled_by is None:
            from fim_one.core.audit.context import current_request_meta

            self.cancelled_by = by
            self._cancel_request = current_request_meta() or run_audit.NO_REQUEST
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

    def _usage_scope(self) -> UsageScope:
        """Whose usage the run's model calls are: the run's principal (the
        author, for a trigger key or a schedule), recorded on the run."""
        return UsageScope(
            user_id=self.principal.user_id,
            org_id=getattr(self.workflow, "org_id", None),
            source_type="workflow_run",
            source_id=self.run_id,
            entry=self.mode.value,
            workflow_id=getattr(self.workflow, "id", None),
        )

    async def _drive(self) -> None:
        # The run's own metering scope, in the task that drives it: the
        # model calls of its steps are recorded on the run and nowhere else
        # (not on the conversation of an agent that started it as a tool).
        # A run that waits and is continued is driven again, by a new
        # object with a scope of its own: each part is recorded once.
        usage = open_meter(self._usage_scope())
        try:
            await self._drive_metered()
        finally:
            # After the run is stored, and whoever stored it: a run closed
            # under this process has still consumed what it consumed.
            await usage.close()

    async def _drive_metered(self) -> None:
        start = time.time()
        out = self.outcome
        try:
            if self._queue_for_slot and not await self._take_slot_when_free():
                raise _NoSlotInTimeError(
                    "The run was approved but could not continue: it waited "
                    f"{resume_slot_wait_seconds():.0f} seconds for one of this user's "
                    "concurrent-run slots and none came free. Run the workflow again."
                )
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
                    # Where the step stands now. Not every event says, and
                    # a step that waited and then failed must not keep the
                    # status of its wait.
                    if name in _EVENT_STATUS:
                        out.node_results[nid]["status"] = _EVENT_STATUS[name]
                    await self._save_progress()
                elif name == "run_suspended":
                    out.status = "waiting"
                    out.error = None
                    out.error_code = None
                    out.total_tokens = int(data.get("total_tokens", 0) or 0)
                    out.waiting = list(data.get("waiting") or [])
                    self._waiting_on = json.loads(
                        json.dumps((data.get("state") or {}).get("waiting") or {}, default=str)
                    )
                    cannot_wait: tuple[str, str] | None = None
                    # A run that waited before keeps its place.
                    if not await self._service._reserve_wait(
                        self.run_id,
                        self.principal.user_id,
                        self._waiting_on,
                        always=self._resume_state is not None,
                    ):
                        cannot_wait = (
                            RUN_WAITING_LIMIT,
                            f"The run cannot wait here: {waiting_runs_limit()} of this "
                            "user's runs are already waiting for a decision. Decide or "
                            "cancel some of them, then run the workflow again.",
                        )
                    else:
                        try:
                            # Serialised and encrypted in a thread: the state
                            # can be megabytes, and other runs share this loop.
                            self._suspend_state = await asyncio.to_thread(
                                seal_state, data.get("state") or {}
                            )
                        except SuspendStateTooLargeError as too_large:
                            cannot_wait = (RUN_STATE_TOO_LARGE, str(too_large))
                    if cannot_wait is not None:
                        # Not stored: the run ends here instead of waiting.
                        out.status = "failed"
                        out.error_code, out.error = cannot_wait
                        out.waiting = []
                        name, data = "run_failed", {
                            "run_id": self.run_id,
                            "status": "failed",
                            "error": out.error,
                            "error_code": out.error_code,
                            "ts": data.get("ts"),
                        }
                    else:
                        # Readers of the stream learn that the run waits and
                        # on what, not the run's variables.
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
        except _NoSlotInTimeError as no_slot:
            out.status = "failed"
            out.error = str(no_slot)
            out.error_code = RUN_SLOT_TIMEOUT
            await self._queue.put(
                ("run_failed", {"run_id": self.run_id, "status": "failed", "error": out.error})
            )
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
            # The engine's own count, in case it ended without a last event
            # to report it (cancelled mid-step, or it raised).
            out.total_tokens = max(out.total_tokens, self._tokens_so_far())
            suspended = out.status == "waiting" and self._suspend_state is not None
            closed_elsewhere = False
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
                        closed_as = await self._service._persist(
                            out,
                            # A continued run keeps the start time it has.
                            started_at=start if self._resume_state is None else None,
                            suspend_state=self._suspend_state if suspended else None,
                            waiting_on=self._waiting_on if suspended else None,
                        )
                        if closed_as is not None:
                            # Someone else closed the run while it executed
                            # here. How they closed it is how it ended:
                            # nobody is told it completed.
                            out.status, out.error = closed_as
                            out.error_code = (
                                "workflow_run_cancelled"
                                if out.status == "cancelled"
                                else "workflow_run_failed"
                            )
                            out.outputs = {}
                            out.waiting = []
                            suspended = False
                            closed_elsewhere = True
                        elif out.status == "cancelled" and self.cancelled_by is not None:
                            # Stored as cancelled here, once: who asked.
                            await run_audit.record_cancelled(
                                self.run_id,
                                actor=self.cancelled_by,
                                was="running",
                                workflow=self.workflow,
                                request=self._cancel_request,
                            )
                    finally:
                        # Only this object's registration: by now the run may
                        # have been continued, by a new StartedRun under the
                        # same id (a decision made the moment it was stored).
                        if self._service._active_runs.get(self.run_id) is self:
                            del self._service._active_runs[self.run_id]
                            self._service._closed_elsewhere.discard(self.run_id)
                        if suspended:
                            # The run is not over: its files stay, no webhook
                            # yet. A decision made while other branches were
                            # still running found the run busy; pick it up.
                            try:
                                await self._service._resume_if_decided(
                                    self.run_id, {"waiting": self._waiting_on}
                                )
                            except Exception:
                                logger.exception(
                                    "Could not check the decisions of run %s", self.run_id
                                )
                        else:
                            # The files the run's Agent steps wrote go with the run.
                            await release_sandbox(self._context.sandbox_id)
                            try:
                                # A run closed elsewhere is not announced
                                # from here: this process's view of it lost.
                                if not closed_elsewhere:
                                    self._service._fire_webhook(
                                        self.workflow, out, self.mode, self.call_context
                                    )
                            except Exception:
                                logger.exception("Webhook dispatch failed for %s", self.run_id)
            finally:
                self._queue.put_nowait(self._END)

    def _tokens_so_far(self) -> int:
        tracker = getattr(self._context, "usage_tracker", None)
        if tracker is None:
            return 0
        try:
            spent = int(tracker.get_summary().total_tokens or 0)
        except Exception:
            return 0
        before = int((self._resume_state or {}).get("total_tokens") or 0)
        return before + spent

    async def _take_slot_when_free(self) -> bool:
        """Queue for one of the principal's concurrent-run slots (a continued run).

        Ahead of the user's new runs, for at most
        ``WORKFLOW_RESUME_SLOT_WAIT_SECONDS``; False when none came free in
        that time. The run's record says it is queued while this lasts.
        Cancelling the run ends the wait; the engine then finds the cancel
        and ends the run as cancelled.
        """
        limiter = self._service.rate_limiter
        taking = asyncio.ensure_future(
            limiter.acquire_first(self.principal.user_id, self.run_id, resume_slot_wait_seconds())
        )
        cancelled = asyncio.ensure_future(self.cancel_event.wait())
        try:
            await asyncio.wait({taking, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for pending in (taking, cancelled):
                if not pending.done():
                    pending.cancel()
            results = await asyncio.gather(taking, cancelled, return_exceptions=True)
        got_slot = results[0] is True
        if got_slot or self.cancel_event.is_set():
            await self._service._set_queued(self.run_id, False)
            return True
        return False

    async def _save_progress(self) -> None:
        """Write the step results so far to the run record, at most once a
        :data:`PROGRESS_SAVE_INTERVAL`: whoever opens the run meanwhile
        (another tab, the run history) sees where it is."""
        now = time.monotonic()
        if now - self._progress_saved_at < PROGRESS_SAVE_INTERVAL:
            return
        self._progress_saved_at = now
        await self._service._save_progress(self.run_id, self.outcome.node_results)


_WAITING_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def _waiting_lock() -> asyncio.Lock:
    """One lock per process (per event loop) for taking a place among a
    user's waiting runs: SQLite has no lock a second connection would wait
    on, and one process is all it serves."""
    loop = asyncio.get_running_loop()
    lock = _WAITING_LOCKS.get(loop)
    if lock is None:
        lock = _WAITING_LOCKS[loop] = asyncio.Lock()
    return lock


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
        # Runs of this process whose record someone else closed; emptied
        # as each run's driver ends.
        self._closed_elsewhere: set[str] = set()
        # One lock per workflow with a single-flight start in progress; an
        # entry goes away once nobody holds or waits for its lock.
        self._single_flight_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    def cancel_run(self, run_id: str, *, by: Any = None) -> bool:
        """Signal a run executing in this process to stop; False if not here.

        *by* (an audit actor) is recorded with ``workflow.run_cancelled``
        when the run is then stored as cancelled; a run that ends some other
        way first is not recorded as cancelled.
        """
        started = self._active_runs.get(run_id)
        if started is None:
            return False
        started.cancel(by=by)
        return True

    @staticmethod
    async def _waiting_count(db: Any, user_id: str, *, besides: str | None = None) -> int:
        """How many of *user_id*'s runs wait for a decision or are about to.

        A run counts from the moment it has reserved its place
        (``waiting_on`` set while still ``running``), so two runs that
        reach their approvals together cannot both take the last place.
        """
        from sqlalchemy import Text, and_, func, or_, select
        from sqlalchemy import cast as sql_cast

        from fim_one.db.models import WorkflowRun

        reserved = and_(
            WorkflowRun.status == "running",
            # Longer than JSON "null": something is in it.
            func.coalesce(func.length(sql_cast(WorkflowRun.waiting_on, Text)), 0) > 4,
        )
        query = select(func.count()).where(
            WorkflowRun.user_id == user_id, or_(WorkflowRun.status == "waiting", reserved)
        )
        if besides is not None:
            query = query.where(WorkflowRun.id != besides)
        return int((await db.execute(query)).scalar_one())

    async def _reserve_wait(
        self, run_id: str, user_id: str, waiting_on: dict[str, Any], *, always: bool = False
    ) -> bool:
        """Take one of *user_id*'s places for waiting runs; False when none is left.

        A waiting run costs nothing to keep, which is why their number is
        capped. Counting and taking are one step: under a lock on the user
        (a PostgreSQL advisory lock for the transaction, across server
        processes; a lock in this process, which is all SQLite has), the
        other waiting runs are counted and, if there is room, this run's
        ``waiting_on`` is written, which makes it count. A run that gets no
        place fails at the step; runs already waiting are not affected.
        """
        from sqlalchemy import text, update

        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        limit = waiting_runs_limit()
        async with _waiting_lock(), create_session() as db:
            if limit > 0 and not always:
                if _dialect_of(db) == "postgresql":
                    await db.execute(
                        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                        {"key": f"workflow-waiting:{user_id}"},
                    )
                if await self._waiting_count(db, user_id, besides=run_id) >= limit:
                    await db.rollback()
                    return False
            await db.execute(
                update(WorkflowRun)
                .where(WorkflowRun.id == run_id, WorkflowRun.status == "running")
                .values(waiting_on=waiting_on)
                .execution_options(synchronize_session=False)
            )
            await db.commit()
        return True

    async def waiting_room(self, parsed: Any, user_id: str, mode: RunMode) -> int | None:
        """How many more of *user_id*'s runs may start to wait; ``None`` = any number.

        ``None`` too for a workflow none of whose steps can wait: no
        approval step, and no connector step whose action requires
        confirmation (judged from the blueprint and the actions as they
        are now).
        """
        from sqlalchemy import func, select

        from fim_one.db import create_session
        from fim_one.db.models.connector import ConnectorAction

        limit = waiting_runs_limit()
        if limit <= 0 or mode in NO_APPROVAL_MODES:
            return None
        can_wait = any(node.type == NodeType.HUMAN_INTERVENTION for node in parsed.nodes)
        action_ids = [
            str(node.data.get("action_id"))
            for node in parsed.nodes
            if node.type == NodeType.CONNECTOR and node.data.get("action_id")
        ]
        async with create_session() as db:
            if not can_wait and action_ids:
                flagged = (
                    await db.execute(
                        select(func.count()).where(
                            ConnectorAction.id.in_(action_ids),
                            ConnectorAction.requires_confirmation.is_(True),
                        )
                    )
                ).scalar_one()
                can_wait = bool(flagged)
            if not can_wait:
                return None
            return max(0, limit - await self._waiting_count(db, user_id))

    async def refuse_if_cannot_wait(self, parsed: Any, user_id: str, mode: RunMode) -> None:
        """Refuse a run that would have to wait when its user has no place left.

        At the start, so that the steps before the approval are not run
        for nothing (and their side effects repeated on the next try).
        """
        if await self.waiting_room(parsed, user_id, mode) == 0:
            raise RunRefusedError(
                RUN_WAITING_LIMIT,
                429,
                f"{waiting_runs_limit()} of this user's runs are already waiting for a "
                "decision, and this workflow has a step that waits for one. Decide or "
                "cancel some of them, then run the workflow again.",
            )

    def note_closed_elsewhere(self, run_id: str) -> None:
        """Remember that *run_id*'s record was closed by someone else.

        Called by the heartbeat (from its own thread: a set insert is
        atomic) so the engine stops before its next step without asking
        the database again.
        """
        self._closed_elsewhere.add(run_id)

    async def owns(self, run_id: str) -> bool:
        """Whether *run_id* is still this process's to execute.

        Its record must be ``running`` under this process's name. Asked by
        the engine before every step: a run cancelled from another process,
        or declared dead while this process could not stamp it, does not
        run another step here.
        """
        from sqlalchemy import select

        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        if run_id in self._closed_elsewhere:
            return False
        async with create_session() as db:
            row = (
                await db.execute(
                    select(WorkflowRun.status, WorkflowRun.worker_id).where(
                        WorkflowRun.id == run_id
                    )
                )
            ).first()
        if row is None or row[0] != "running" or row[1] != WORKER_ID:
            self._closed_elsewhere.add(run_id)
            return False
        return True

    def active_run_ids(self) -> list[str]:
        """Runs executing in this process right now."""
        return list(self._active_runs)

    async def cancel_stored_run(self, run_id: str, *, by: Any = None) -> bool:
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
        if ended is not None and by is not None:
            # This call closed it: of several, one gets here.
            await run_audit.record_cancelled(run_id, actor=by, was=ended)
        return ended is not None

    async def _end_stored_run(
        self,
        run_id: str,
        from_statuses: tuple[str, ...],
        status: str,
        error: str,
        *,
        stale_seconds: float | None = None,
        closed_because: str | None = None,
    ) -> str | None:
        """Close a run on its record alone; the status it had, or ``None``.

        The status is not read first and then written: each try is one
        ``UPDATE … WHERE status = <one of from_statuses>``, so a run that
        changes hands in between (a waiting run claimed for continuation
        the moment it is cancelled) is still closed, under the status it
        has by then, or not at all. Of several callers (two workers
        sweeping, a cancel beside a decision) one closes the run. ``None``
        means the run was in none of *from_statuses*: it has ended, or does
        not exist.

        With *stale_seconds* only a run whose heartbeat is older than that,
        by the database's clock, is closed, and only if nobody holds a
        lock on its row (``None`` then, as for a run that is not stale:
        whoever is writing it is alive). The run's open confirmation requests are closed in the
        same transaction. A run that was waiting loses its sandbox here; a
        run that was executing somewhere keeps it until that process, or
        the sweep's later pass over unused directories, removes it.

        *closed_because* is for the platform closing the run on its own:
        the caller that closes it records ``workflow.run_closed`` with it.
        """
        from sqlalchemy import CursorResult, func, select, update

        from fim_one.core.assembly.confirmation import close_requests_of_runs
        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        now = datetime.now(UTC)
        previous: str | None = None
        # Twice: a run moving between two of the statuses while they are
        # tried in turn is caught on the second pass.
        for _ in range(2):
            for candidate in from_statuses:
                conditions = [WorkflowRun.id == run_id, WorkflowRun.status == candidate]
                async with create_session() as db:
                    if stale_seconds is not None:
                        conditions.append(
                            func.coalesce(
                                WorkflowRun.heartbeat_at,
                                WorkflowRun.started_at,
                                WorkflowRun.created_at,
                            )
                            < db_seconds_ago(_dialect_of(db), stale_seconds)
                        )
                        # A row someone holds a lock on is being written
                        # by a live process (a progress save not yet
                        # committed, by a loop that is busy): it is left
                        # for the next sweep instead of waited for and
                        # then closed. PostgreSQL; SQLite has no row locks
                        # and renders none.
                        conditions.append(
                            WorkflowRun.id.in_(
                                select(WorkflowRun.id)
                                .where(WorkflowRun.id == run_id)
                                .with_for_update(skip_locked=True)
                            )
                        )
                    result = await db.execute(
                        update(WorkflowRun)
                        .where(*conditions)
                        .values(
                            status=status,
                            error=error,
                            completed_at=now,
                            suspend_state=None,
                            waiting_on=None,
                            queued_at=None,
                        )
                        .execution_options(synchronize_session=False)
                    )
                    if (cast(CursorResult[Any], result).rowcount or 0) == 1:
                        await close_requests_of_runs(db, [run_id])
                        await db.commit()
                        previous = candidate
                        break
                    await db.rollback()
            if previous is not None:
                break
        if previous == "waiting":
            # Nothing was executing it: nobody else will remove its files.
            await release_sandbox(workflow_run_sandbox_id(run_id))
        if previous is not None and closed_because:
            # After the run's own clean-up: the record waits for it, not
            # the other way round.
            await run_audit.record_closed(run_id, closed_because)
        if previous is not None and status == "failed":
            # The one caller that closed the run tells the owner, once.
            try:
                await self._announce_closed(run_id, error)
            except Exception:
                from fim_one.core.utils import cancel_requested

                if cancel_requested():
                    raise
                logger.exception("Could not announce that run %s failed", run_id)
        return previous

    async def _announce_closed(self, run_id: str, error: str) -> None:
        """The run-failed webhook of a run closed on its record.

        No driver ends such a run (it could not continue after its
        approval, its state was unreadable, or its process died), so the
        webhook a driver would send is sent from here.
        """
        from sqlalchemy import select

        from fim_one.db import create_session
        from fim_one.db.models import Workflow, WorkflowRun

        async with create_session() as db:
            run = (
                await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            ).scalar_one_or_none()
            workflow = (
                (
                    await db.execute(select(Workflow).where(Workflow.id == run.workflow_id))
                ).scalar_one_or_none()
                if run is not None
                else None
            )
        if run is None or workflow is None:
            return
        mode = RunMode(run.mode) if run.mode else RunMode.INTERACTIVE
        ctx = CallContext(
            principal=provisional_principal(run.user_id, auth=_AUTH.get(mode, AuthMethod.SESSION)),
            binding=workflow_binding(workflow),
            entry=_ENTRY.get(mode, "workflow_node"),
        )
        outcome = RunOutcome(
            run_id=run_id,
            status="failed",
            error=error,
            error_code="workflow_run_failed",
            duration_ms=int(run.duration_ms or 0),
            total_tokens=int(run.total_tokens or 0),
        )
        self._fire_webhook(workflow, outcome, mode, ctx)

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

        await self.refuse_if_cannot_wait(parsed, ctx.principal.user_id, mode)

        if admit_batch:
            allowed, rate_error = await self.rate_limiter.admit_batch(ctx.principal.user_id)
            if not allowed:
                raise RunRefusedError("workflow_rate_limited", 429, rate_error)
        return ctx, parsed

    @staticmethod
    def check_inputs(parsed: Any, inputs: dict[str, Any] | None) -> dict[str, Any]:
        """*inputs* checked against the Start step and put in their declared types.

        Raises :class:`RunRefusedError` ``workflow_input_required`` or
        ``workflow_input_invalid`` (422) naming the input. A workflow whose
        Start step does not ask for the check gets its inputs back as given.
        """
        from .run_inputs import InputRefusal, checked_inputs

        try:
            return checked_inputs(parsed, inputs)
        except InputRefusal as refusal:
            raise RunRefusedError(
                refusal.error_code, refusal.status_code, refusal.detail, refusal.detail_args
            ) from None

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

    async def admit_batch(
        self, workflow: Any, caller_id: str, items: list[dict[str, Any]]
    ) -> tuple[tuple[CallContext, Any], list[dict[str, Any]]]:
        """Admit a batch once for all its items; ``(authorized, checked inputs)``.

        The batch counts as one start against runs-per-minute
        (:meth:`authorize` with *admit_batch*), and every item's inputs are
        checked before any starts: a batch with one bad row is refused
        whole, naming the row. A refused batch is one refusal record.
        """
        try:
            authorized = await self.authorize(
                workflow, caller_id, RunMode.BATCH, admit_batch=True
            )
            checked: list[dict[str, Any]] = []
            for index, item in enumerate(items):
                try:
                    checked.append(self.check_inputs(authorized[1], item))
                except RunRefusedError as exc:
                    raise RunRefusedError(
                        exc.error_code,
                        exc.status_code,
                        f"Item {index + 1}: {exc.detail}",
                        {**(exc.detail_args or {}), "index": index},
                    ) from None
        except RunRefusedError as exc:
            await self.record_refused(workflow, caller_id, RunMode.BATCH, exc)
            raise
        return authorized, checked

    async def record_refused(
        self,
        workflow: Any,
        caller_id: str | None,
        mode: RunMode,
        exc: RunRefusedError,
        *,
        run_id: str | None = None,
        request: Any = None,
    ) -> None:
        """Record that a run through *mode* was refused; never raises.

        Also for refusals decided before the service is reached (a trigger
        key whose author is switched off), so that every refused run is
        recorded the same way.
        """
        user_id = workflow.user_id if mode in AUTHOR_MODES else caller_id
        await run_audit.record_refused(
            workflow,
            user_id=user_id,
            mode=mode.value,
            error_code=exc.error_code,
            run_id=run_id,
            request=request,
        )

    async def record_items_not_started(
        self, workflow: Any, caller_id: str, reasons: dict[str, int], total: int
    ) -> None:
        """One refusal record per reason for the items of a batch not started."""
        for code, count in reasons.items():
            if count:
                await run_audit.record_refused(
                    workflow,
                    user_id=caller_id,
                    mode=RunMode.BATCH.value,
                    error_code=code,
                    not_started=(count, total),
                )

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
        inputs_checked: bool = False,
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
        to tell (schedules). *inputs_checked* says the caller already passed
        *inputs* through :meth:`check_inputs` (a batch checks every item
        before any starts).
        """
        inputs = inputs or {}
        try:
            ctx, parsed = authorized or await self.authorize(workflow, caller_id, mode)
            # Against the Start step's declaration, before anything runs:
            # the steps get each input in its declared type.
            # Once: a value already put in its type does not always pass
            # again (JSON text "\"a\"" becomes the text a, which is not JSON).
            if not inputs_checked:
                inputs = self.check_inputs(parsed, inputs)
            if single_flight:
                # Early answer without taking the workflow's lock. Requests
                # that arrive together all pass it; the check that decides
                # is made with the insert, in _insert_single_flight_run.
                await self._refuse_if_running(workflow.id)
        except RunRefusedError as exc:
            await self.record_refused(workflow, caller_id, mode, exc)
            if record_refusal:
                await self._record_refusal(workflow, caller_id, mode, inputs, exc)
            raise

        principal = ctx.principal
        run_id = str(uuid.uuid4())
        fields = self._run_fields(run_id, workflow, principal, mode, inputs)
        record_created = False

        async def announce(db: Any) -> None:
            # In the transaction that inserts the run record.
            await run_audit.record_started(
                workflow, user_id=principal.user_id, mode=mode.value, run_id=run_id, db=db
            )

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
                    started=announce,
                )
            else:
                if acquire_slot:
                    await self._acquire_slot(principal.user_id, run_id, mode)
                await self._insert_run(started=announce, **fields)
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
                still_owned=lambda: self.owns(run_id),
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
        except BaseException as exc:
            if isinstance(exc, RunRefusedError):
                # No slot free, over runs-per-minute, or another run of a
                # single-flight workflow in flight: decided as it starts.
                await asyncio.shield(self.record_refused(workflow, caller_id, mode, exc))
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

        The run goes on as the same principal, on the blueprint it started
        with, but it is admitted again first (:meth:`_readmit`): what a run
        starting now would be refused for, a continuation is refused for
        too, and the run then ends as failed with that refusal. It also
        takes one of the principal's concurrent-run slots before any step
        runs. A continuation is never dropped for lack of one: like a
        batch item it waits for a free slot, registered and with its
        heartbeat going, so it shows as running and is not taken for dead.
        """
        from sqlalchemy import CursorResult, select, update

        from fim_one.db import create_session
        from fim_one.db.models import Workflow, WorkflowRun

        async with create_session() as db:
            result = await db.execute(
                update(WorkflowRun)
                .where(WorkflowRun.id == run_id, WorkflowRun.status == "waiting")
                .values(
                    status="running",
                    worker_id=WORKER_ID,
                    heartbeat_at=db_now(_dialect_of(db)),
                    # Running again: it waits on nothing until it says so.
                    waiting_on=None,
                    # Queued for a slot until its first step may run.
                    queued_at=db_now(_dialect_of(db)),
                )
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
            mode = RunMode(run.mode) if run.mode else RunMode.INTERACTIVE
            ctx, parsed = await self._readmit(workflow, run, mode)
            started = self._continue(run, workflow, ctx, parsed, mode)
        except RunRefusedError as refused:
            logger.warning(
                "Workflow run %s is not continued: %s", run_id, refused.error_code
            )
            await self.record_refused(
                workflow,
                run.user_id,
                mode,
                refused,
                run_id=run_id,
                request=run_audit.NO_REQUEST,
            )
            await self._end_stored_run(
                run_id,
                ("running",),
                "failed",
                f"The run could not continue after its approval: {refused.detail} "
                f"({refused.error_code})",
            )
            return None
        except Exception:
            from fim_one.core.utils import cancel_requested

            if cancel_requested():
                # Cancelled (shutdown), not the run's own failure. It stays
                # claimed and unstamped; the recovery sweep closes it as
                # interrupted, as it does any run whose process stopped.
                raise
            logger.exception("Workflow run %s could not be continued", run_id)
            await self._end_stored_run(
                run_id,
                ("running",),
                "failed",
                RUN_NOT_RESUMABLE_ERROR,
                closed_because="not_resumable",
            )
            return None
        self._active_runs[run_id] = started
        started.start()
        return started

    async def _readmit(self, workflow: Any, run: Any, mode: RunMode) -> tuple[CallContext, Any]:
        """The checks a run starting now passes, for a run about to continue.

        The principal's account, quota and plan and the owner's account
        (the gate); for a trigger key or schedule run, that the workflow
        is still switched on; that the run's steps are still allowed (the
        Human Intervention switch). Hours can pass between a run's start
        and its approval. Raises :class:`RunRefusedError`.
        """
        from .parser import parse_blueprint

        if mode in ACTIVE_ONLY_MODES and not workflow.is_active:
            raise RunRefusedError("workflow_inactive", 403, "Workflow is inactive")
        ctx = await self._admit(
            workflow, run.user_id, _AUTH.get(mode, AuthMethod.SESSION), mode
        )
        parsed = parse_blueprint(run.blueprint_snapshot)
        if has_disabled_node(parsed.nodes):
            raise RunRefusedError(
                HUMAN_INTERVENTION_DISABLED, 409, HUMAN_INTERVENTION_DISABLED_DETAIL
            )
        return ctx, parsed

    def _continue(
        self, run: Any, workflow: Any, ctx: CallContext, parsed: Any, mode: RunMode
    ) -> StartedRun:
        """The :class:`StartedRun` that goes on from *run*'s stored state."""
        from fim_one.db import create_session

        from .engine import SUSPEND_STATE_VERSION

        state = unseal_state(run.suspend_state)
        if state.get("version") != SUSPEND_STATE_VERSION:
            raise ValueError("no usable suspend state")
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
            still_owned=lambda: self.owns(run.id),
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
            # Taken by the run itself, before its first step.
            holds_slot=True,
            queue_for_slot=True,
            resume_state=state,
            prior_node_results=run.node_results if isinstance(run.node_results, dict) else None,
            prior_duration_ms=int(run.duration_ms or 0),
        )

    def schedule_resume(self, run_id: str) -> None:
        """Continue *run_id* in a background task of this process.

        The one seam between "a decision was made" and "the run goes on";
        a task queue would take its place here.
        """
        import contextvars

        from fim_one.core.utils import spawn_background

        # Not in the context of the request that made the decision: the run
        # is its runner's, and the approver's address, sign-in, rate-limit
        # identity and meters are not its own.
        spawn_background(self._resume_logged(run_id), context=contextvars.Context())

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
        ``db_session_factory``, so an approval node is auto-approved. What
        the step consumes is recorded in the usage ledger (entry
        ``test_node``) and counts toward the quota; no run record is made.
        """
        from fim_one.core.model.usage import UsageTracker

        from .nodes import get_executor
        from .variable_store import VariableStore

        try:
            user_id, auth = run_identity(workflow, caller_id, RunMode.TEST_NODE)
            if has_disabled_node([node]):
                raise RunRefusedError(
                    HUMAN_INTERVENTION_DISABLED, 409, HUMAN_INTERVENTION_DISABLED_DETAIL
                )
            ctx = await self._admit(workflow, user_id, auth, RunMode.TEST_NODE)
        except RunRefusedError as exc:
            await self.record_refused(workflow, caller_id, RunMode.TEST_NODE, exc)
            raise
        principal = ctx.principal
        # There is no run record to commit it with.
        await run_audit.record_started(
            workflow, user_id=principal.user_id, mode=RunMode.TEST_NODE.value, run_id=None
        )

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
        usage = OpenMeter(
            UsageScope(
                user_id=principal.user_id,
                org_id=getattr(workflow, "org_id", None),
                source_type="workflow_run",
                # There is no run record: the id says which test it was.
                source_id=test_run_id,
                entry=RunMode.TEST_NODE.value,
                workflow_id=workflow.id,
            )
        ).enter()
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
            usage.leave()
            try:
                await release_sandbox(context.sandbox_id)
            finally:
                await usage.close()

        # No run record is written for a test. One used to be, with nothing
        # in it but the tokens, so that the test counted toward the quota:
        # the quota reads the usage ledger now, where the scope above has
        # put what the step consumed.
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

    async def _insert_run(
        self, *, started: Callable[[Any], Awaitable[None]] | None = None, **fields: Any
    ) -> None:
        """Insert a run record; *started* adds the record of its start to the same commit."""
        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        async with create_session() as db:
            if started is not None:
                # Before the run row is added: its look-ups must not flush
                # the row early (an error of the insert would surface there).
                await started(db)
            db.add(WorkflowRun(**self._stamped(fields, db)))
            await db.commit()

    @staticmethod
    def _stamped(fields: dict[str, Any], db: Any) -> dict[str, Any]:
        """*fields* with the first heartbeat of a run that starts running,
        taken from the database's clock like every later one."""
        if fields.get("status") == "running" and fields.get("worker_id"):
            return {**fields, "heartbeat_at": db_now(_dialect_of(db))}
        return fields

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
        started: Callable[[Any], Awaitable[None]] | None = None,
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
            if started is not None:
                await started(db)  # before the run row: see _insert_run
            db.add(WorkflowRun(**self._stamped(fields, db)))
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
                # The code; for an input that does not fit, also which one.
                error=(
                    f"{exc.error_code}: {exc.detail}"
                    if exc.error_code.startswith("workflow_input_")
                    else exc.error_code
                ),
            )
        except Exception:
            logger.exception("Failed to record refused %s run of %s", mode, workflow.id)

    async def _persist(
        self,
        out: RunOutcome,
        *,
        started_at: float | None,
        suspend_state: dict[str, Any] | None = None,
        waiting_on: dict[str, Any] | None = None,
    ) -> tuple[str, str | None] | None:
        """Write how the run stands to its record.

        With *suspend_state* the run is waiting: the state is stored and
        the run is not completed. Otherwise the run has ended, and the
        requests it still had open are closed with it: nobody is asked to
        decide for a run that is over.

        One ``UPDATE … WHERE status = 'running'``: a record that is no
        longer ``running`` was closed by someone else meanwhile (cancelled
        from another process, or declared dead by the recovery sweep) and
        is not written, however the two writes interleave. Returns ``None``
        when the record was written, else ``(status, error)`` as the
        record has them: that, not *out*, is then how the run ended.
        """
        from sqlalchemy import CursorResult, func, select, update

        from fim_one.core.assembly.confirmation import close_requests_of_runs
        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        values: dict[str, Any] = {
            "status": out.status,
            "outputs": out.outputs or None,
            "node_results": out.node_results or None,
            "duration_ms": out.duration_ms,
            "error": out.error,
            "total_tokens": out.total_tokens,
            "suspend_state": suspend_state,
            "waiting_on": waiting_on if suspend_state is not None else None,
            "queued_at": None,
        }
        if started_at is not None:
            values["started_at"] = datetime.fromtimestamp(started_at, tz=UTC)
        if suspend_state is None:
            values["completed_at"] = datetime.now(UTC)
        try:
            async with create_session() as db:
                result = await db.execute(
                    update(WorkflowRun)
                    .where(WorkflowRun.id == out.run_id, WorkflowRun.status == "running")
                    .values(**values)
                    .execution_options(synchronize_session=False)
                )
                written = (cast(CursorResult[Any], result).rowcount or 0) == 1
                if written and suspend_state is not None:
                    await db.commit()
                    return None
                # Ended here, or closed elsewhere: either way nobody is
                # asked to decide for it any more.
                left_open = await close_requests_of_runs(db, [out.run_id])
                if not written:
                    # How the run ended is someone else's entry; what it
                    # spent here is still spent, and counts toward quota.
                    await db.execute(
                        update(WorkflowRun)
                        .where(WorkflowRun.id == out.run_id)
                        .values(
                            total_tokens=func.max(WorkflowRun.total_tokens, out.total_tokens)
                            if _dialect_of(db) == "sqlite"
                            else func.greatest(WorkflowRun.total_tokens, out.total_tokens),
                            duration_ms=out.duration_ms,
                        )
                        .execution_options(synchronize_session=False)
                    )
                await db.commit()
                if written:
                    if left_open and out.status != "cancelled":
                        # Whoever was asked to decide no longer sees the
                        # request. A cancel is recorded by who cancelled.
                        await run_audit.record_closed(
                            out.run_id,
                            f"ended {out.status}; {left_open} open approval request(s) closed",
                        )
                    return None
                stored = (
                    await db.execute(
                        select(WorkflowRun.status, WorkflowRun.error).where(
                            WorkflowRun.id == out.run_id
                        )
                    )
                ).first()
            if stored is None:
                return None  # deleted meanwhile: nothing to disagree with
            logger.warning(
                "Workflow run %s is already %s; its result (%s) is not recorded",
                out.run_id,
                stored[0],
                out.status,
            )
            return str(stored[0]), stored[1]
        except Exception:
            logger.exception("Failed to persist workflow run %s", out.run_id)
            return None

    async def _set_queued(self, run_id: str, queued: bool) -> None:
        from sqlalchemy import update

        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        try:
            async with create_session() as db:
                await db.execute(
                    update(WorkflowRun)
                    .where(WorkflowRun.id == run_id)
                    .values(queued_at=db_now(_dialect_of(db)) if queued else None)
                    .execution_options(synchronize_session=False)
                )
                await db.commit()
        except Exception:
            logger.exception("Could not update the queue mark of run %s", run_id)

    async def _save_progress(self, run_id: str, node_results: dict[str, Any]) -> None:
        from sqlalchemy import update

        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        try:
            async with create_session() as db:
                await db.execute(
                    update(WorkflowRun)
                    .where(WorkflowRun.id == run_id, WorkflowRun.status == "running")
                    .values(
                        node_results=dict(node_results), heartbeat_at=db_now(_dialect_of(db))
                    )
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


def waited_request_ids(state: Any) -> list[str]:
    """The ids of the requests a waiting run's steps wait on.

    *state* is ``{"waiting": <the run's waiting_on>}``.
    """
    waiting = state.get("waiting") if isinstance(state, dict) else None
    return [
        str(record.get("confirmation_id"))
        for record in (waiting or {}).values()
        if isinstance(record, dict) and record.get("confirmation_id")
    ]


def settled_given(state: Any, requests: dict[str, tuple[str, datetime | None]]) -> bool:
    """:func:`requests_settled` for a caller that has the request rows.

    *requests* maps a request id to its ``(status, expires_at)``; an id not
    in it is a request that is gone.
    """
    waiting = state.get("waiting") if isinstance(state, dict) else None
    ids = waited_request_ids(state)
    if not ids:
        # A waiting step that names no request can only fail; let it.
        return bool(waiting)
    now = datetime.now(UTC)
    for request_id in ids:
        if request_id not in requests:
            return True
        status, expires_at = requests[request_id]
        if status != "pending":
            return True
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                return True
    return False


async def request_states(ids: list[str]) -> dict[str, tuple[str, datetime | None]]:
    """``(status, expires_at)`` of each of the requests *ids*, in one query."""
    from sqlalchemy import select

    from fim_one.db import create_session
    from fim_one.db.models.channel import ConfirmationRequest

    if not ids:
        return {}
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
    return {str(row[0]): (str(row[1]), row[2]) for row in rows}


async def requests_settled(state: dict[str, Any]) -> bool:
    """Whether a waiting run has something to go on with.

    True when any request its steps wait on (*state* is the run's
    ``suspend_state``) is decided, closed, past its deadline or gone: the
    step waiting on it can then finish, one way or the other. False while
    every one of them is still open, and for a state that names none.
    """
    return settled_given(state, await request_states(waited_request_ids(state)))


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
