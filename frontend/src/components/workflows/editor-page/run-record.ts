/**
 * A stored workflow run, as the run panel shows it.
 *
 * A run is not tied to the stream that started it: it may wait for a
 * decision for hours and go on in the background, or the stream may drop.
 * The panel then follows the run by reading its record, and this is the
 * mapping from that record to what the panel shows.
 */
import type {
  NodeRunResult,
  NodeRunStatus,
  RunWaitingItem,
  WorkflowLogEvent,
  WorkflowLogEventType,
  WorkflowRunResponse,
  WorkflowRunStatus,
} from "@/types/workflow"
import { waitingItemsOf } from "./run-stream"

/** What the run panel shows of a run. */
export interface RunPanelState {
  status: WorkflowRunStatus
  /** The run has not ended: it runs, or it waits for a decision. */
  isActive: boolean
  nodeResults: Record<string, NodeRunResult>
  finalOutputs: Record<string, unknown> | null
  finalError: string | null
  runDuration: number | null
  /** What a waiting run waits on; empty otherwise. */
  waitingFor: RunWaitingItem[]
}

const RUN_STATUSES: readonly WorkflowRunStatus[] = [
  "pending",
  "running",
  "waiting",
  "completed",
  "failed",
  "cancelled",
]
const NODE_STATUSES: readonly NodeRunStatus[] = [
  "pending",
  "running",
  "waiting",
  "completed",
  "failed",
  "skipped",
  "retrying",
]
const ENDED_NODE_STATUSES: readonly NodeRunStatus[] = ["completed", "failed", "skipped"]

/** Whether a run in this status can still change. */
export function isActiveRunStatus(status: string | null | undefined): boolean {
  return status === "pending" || status === "running" || status === "waiting"
}

/**
 * One step of a run record as a node result. The record is written from the
 * run's events, so it has `output_preview`, not `output`, and a step that
 * has only started has no status yet: it is running.
 */
export function nodeResultOfRecord(entry: unknown): NodeRunResult {
  const data = (typeof entry === "object" && entry !== null ? entry : {}) as Record<string, unknown>
  const status = NODE_STATUSES.includes(data.status as NodeRunStatus)
    ? (data.status as NodeRunStatus)
    : "running"
  const error = data.error ?? data.previous_error
  return {
    status,
    output: data.output_preview ?? data.output ?? null,
    error: typeof error === "string" ? error : null,
    started_at: typeof data.started_at === "string" ? data.started_at : null,
    completed_at: typeof data.completed_at === "string" ? data.completed_at : null,
    duration_ms: typeof data.duration_ms === "number" ? data.duration_ms : null,
    input_preview: data.input_preview ?? undefined,
  }
}

/** What the run panel shows for a run record. */
export function panelStateOfRun(
  run: Pick<
    WorkflowRunResponse,
    "status" | "outputs" | "node_results" | "duration_ms" | "error" | "waiting_for"
  >,
): RunPanelState {
  const status = RUN_STATUSES.includes(run.status) ? run.status : "running"
  const nodeResults: Record<string, NodeRunResult> = {}
  for (const [nodeId, entry] of Object.entries(run.node_results ?? {})) {
    nodeResults[nodeId] = nodeResultOfRecord(entry)
  }
  const ended = !isActiveRunStatus(status)
  return {
    status,
    isActive: !ended,
    nodeResults,
    finalOutputs: status === "completed" ? (run.outputs ?? {}) : null,
    finalError: status === "failed" ? run.error || "Unknown error" : null,
    runDuration: ended ? (run.duration_ms ?? null) : null,
    waitingFor: status === "waiting" ? waitingItemsOf(run.waiting_for) : [],
  }
}

/**
 * Node results after reading the record, given what the panel had (from the
 * stream or an earlier read). The record is written about once a second, so
 * it can be a moment behind the stream: a step the panel already saw end is
 * not put back to running, and a step the record does not have yet stays.
 */
export function mergeNodeResults(
  shown: Record<string, NodeRunResult> | null,
  fromRecord: Record<string, NodeRunResult>,
): Record<string, NodeRunResult> {
  const merged: Record<string, NodeRunResult> = { ...(shown ?? {}) }
  for (const [nodeId, result] of Object.entries(fromRecord)) {
    const before = merged[nodeId]
    const behind =
      before !== undefined &&
      ENDED_NODE_STATUSES.includes(before.status) &&
      !ENDED_NODE_STATUSES.includes(result.status)
    if (!behind) merged[nodeId] = result
  }
  return merged
}

const LOG_EVENT_OF_STATUS: Partial<Record<NodeRunStatus, WorkflowLogEventType>> = {
  running: "node_started",
  retrying: "node_started",
  waiting: "node_waiting",
  completed: "node_completed",
  failed: "node_failed",
  skipped: "node_skipped",
}
const ENDED_LOG_EVENTS: readonly WorkflowLogEventType[] = [
  "node_completed",
  "node_failed",
  "node_skipped",
]

/**
 * The execution log of a run, from its record: one entry per step that has
 * started, in the state the record has it in, and a last entry for the run
 * itself once it has ended (a cancelled run ends as the stream reports it:
 * `run_completed` with status `cancelled`).
 *
 * The record keeps a step's latest state only, so a finished step is one
 * `node_completed` entry without the `node_started` before it. Entries are
 * ordered by `ts` (when the step's last event happened) when every step has
 * one; otherwise they keep the record's order. `now` is the time shown for
 * an entry whose step has no `ts`.
 */
export function logEventsOfRecord(
  run: Pick<WorkflowRunResponse, "status" | "node_results" | "duration_ms" | "error">,
  now: number,
): WorkflowLogEvent[] {
  const steps: { event: WorkflowLogEvent; ts: number | null }[] = []
  for (const [nodeId, entry] of Object.entries(run.node_results ?? {})) {
    const eventType = LOG_EVENT_OF_STATUS[nodeResultOfRecord(entry).status]
    if (!eventType) continue
    const data = (typeof entry === "object" && entry !== null ? entry : {}) as Record<string, unknown>
    const ts = typeof data.ts === "number" && Number.isFinite(data.ts) ? data.ts : null
    steps.push({
      ts,
      event: {
        timestamp: ts ?? now,
        eventType,
        nodeId,
        details: { ...data, node_id: nodeId },
      },
    })
  }
  if (steps.every((step) => step.ts !== null)) {
    // Array.prototype.sort is stable: steps of the same instant keep the record's order.
    steps.sort((a, b) => (a.ts as number) - (b.ts as number))
  }
  const events = steps.map((step) => step.event)

  const status = RUN_STATUSES.includes(run.status) ? run.status : "running"
  if (!isActiveRunStatus(status)) {
    const lastTs = steps.reduce<number | null>(
      (latest, step) => (step.ts !== null && (latest === null || step.ts > latest) ? step.ts : latest),
      null,
    )
    events.push({
      timestamp: lastTs ?? now,
      eventType: status === "failed" ? "run_failed" : "run_completed",
      nodeId: null,
      details: {
        status,
        duration_ms: run.duration_ms ?? null,
        ...(status === "failed" ? { error: run.error || "Unknown error" } : {}),
      },
    })
  }
  return events
}

/**
 * The execution log after reading the record, given what the panel had
 * (from the stream or an earlier read). Nothing the panel had is replaced
 * or dropped: an entry of the record is added, after those, only when the
 * panel has none for that step and event. As in {@link mergeNodeResults},
 * the record can be a moment behind: a step the panel already saw end does
 * not get a started or waiting entry after it.
 */
export function mergeLogEvents(
  shown: WorkflowLogEvent[],
  fromRecord: WorkflowLogEvent[],
): WorkflowLogEvent[] {
  const key = (event: WorkflowLogEvent) => `${event.eventType}\u0000${event.nodeId ?? ""}`
  const have = new Set(shown.map(key))
  const ended = new Set(
    shown.filter((event) => ENDED_LOG_EVENTS.includes(event.eventType)).map((event) => event.nodeId),
  )
  const added = fromRecord.filter((event) => {
    if (have.has(key(event))) return false
    const behind = !ENDED_LOG_EVENTS.includes(event.eventType) && event.nodeId !== null && ended.has(event.nodeId)
    return !behind
  })
  return added.length === 0 ? shown : [...shown, ...added]
}
