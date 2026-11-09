/**
 * The server-sent event stream of a workflow run, without React: a parser
 * for the wire format and the mapping from run events to what the run
 * panel shows.
 */
import type { NodeRunResult, WorkflowLogEvent, WorkflowLogEventType } from "@/types/workflow"

/**
 * Incremental SSE parser. Feed it decoded text as it arrives; it calls
 * `onEvent(event, data)` once per complete event. Multi-line `data:` fields
 * are joined with a newline, comment lines (keepalives) are skipped, and an
 * event without data is dropped.
 */
export function createSseParser(onEvent: (event: string, data: string) => void) {
  let buffer = ""
  let currentEvent = "message"
  let currentData = ""

  return (chunk: string): void => {
    buffer += chunk
    const lines = buffer.split("\n")
    buffer = lines.pop() ?? ""
    for (const line of lines) {
      if (line.startsWith("event:")) {
        currentEvent = line.slice(6).trim()
      } else if (line.startsWith("data:")) {
        // SSE spec allows multi-line data — append with newline separator
        currentData = currentData
          ? currentData + "\n" + line.slice(5).trim()
          : line.slice(5).trim()
      } else if (line.startsWith(":")) {
        // SSE comment (keepalive) — ignore
      } else if (line === "") {
        if (currentData) onEvent(currentEvent, currentData)
        currentEvent = "message"
        currentData = ""
      }
    }
  }
}

const LOG_EVENTS: readonly WorkflowLogEventType[] = [
  "node_started",
  "node_completed",
  "node_failed",
  "node_skipped",
  "node_retrying",
  "run_completed",
  "run_failed",
]

/** The execution-log entry for an event, or null for events the log does not show. */
export function logEventOf(
  eventType: string,
  data: Record<string, unknown>,
  now: number,
): WorkflowLogEvent | null {
  if (!LOG_EVENTS.includes(eventType as WorkflowLogEventType)) return null
  return {
    timestamp: (data.ts as number) ?? now,
    eventType: eventType as WorkflowLogEventType,
    nodeId: (data.node_id as string) ?? null,
    details: data,
  }
}

/**
 * How many steps the run executes, from its `run_started` event. The run
 * executes the stored blueprint, which can differ from the canvas (unsaved
 * steps), so progress is counted against this number and nothing else.
 */
export function nodeCountOfRunStarted(eventType: string, data: Record<string, unknown>): number | null {
  if (eventType !== "run_started") return null
  const count = data.node_count
  return typeof count === "number" && Number.isFinite(count) && count >= 0 ? count : null
}

const NODE_EVENT_STATUS = new Map<string, NodeRunResult["status"]>([
  ["node_started", "running"],
  ["node_completed", "completed"],
  ["node_failed", "failed"],
  ["node_retrying", "retrying"],
  ["node_skipped", "skipped"],
])

/** The status a node event puts its node in, or null when it is not a node event. */
export function nodeStatusOfEvent(eventType: string): NodeRunResult["status"] | null {
  return NODE_EVENT_STATUS.get(eventType) ?? null
}

/**
 * A node's result after a node event. Output, duration and input preview
 * carry over from the previous event when the new one does not bring them.
 */
export function nodeResultOfEvent(
  status: NodeRunResult["status"],
  data: Record<string, unknown>,
  existing: NodeRunResult | undefined,
): NodeRunResult {
  return {
    status,
    output: data.output_preview ?? existing?.output ?? null,
    error: ((data.error ?? data.previous_error) as string) ?? null,
    started_at: null,
    completed_at: null,
    duration_ms: (data.duration_ms as number) ?? existing?.duration_ms ?? null,
    input_preview: data.input_preview ?? existing?.input_preview ?? undefined,
    retryAttempt: (data.attempt as number) ?? undefined,
    maxRetries: (data.max_retries as number) ?? undefined,
  }
}
