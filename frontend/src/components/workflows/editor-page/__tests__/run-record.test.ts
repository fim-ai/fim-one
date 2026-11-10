import { describe, expect, it } from "vitest"

import {
  isActiveRunStatus,
  logEventsOfRecord,
  mergeLogEvents,
  mergeNodeResults,
  nodeResultOfRecord,
  panelStateOfRun,
} from "@/components/workflows/editor-page/run-record"
import type { NodeRunResult, WorkflowLogEvent, WorkflowRunResponse } from "@/types/workflow"

type Record_ = Parameters<typeof panelStateOfRun>[0]

const record = (over: Partial<WorkflowRunResponse>): Record_ => ({
  status: "running",
  outputs: null,
  node_results: null,
  duration_ms: null,
  error: null,
  waiting_for: null,
  ...over,
})

const result = (status: NodeRunResult["status"], over: Partial<NodeRunResult> = {}): NodeRunResult => ({
  status,
  output: null,
  error: null,
  started_at: null,
  completed_at: null,
  duration_ms: null,
  ...over,
})

describe("isActiveRunStatus", () => {
  it("is true while a run can still change", () => {
    expect(["pending", "running", "waiting"].map(isActiveRunStatus)).toEqual([true, true, true])
    expect(["completed", "failed", "cancelled", null, undefined].map(isActiveRunStatus)).toEqual([
      false,
      false,
      false,
      false,
      false,
    ])
  })
})

describe("nodeResultOfRecord", () => {
  it("reads a step as the run's events recorded it", () => {
    expect(
      nodeResultOfRecord({
        node_id: "llm_1",
        status: "completed",
        output_preview: "hi",
        duration_ms: 20,
        input_preview: "in",
      }),
    ).toEqual(result("completed", { output: "hi", duration_ms: 20, input_preview: "in" }))
  })

  it("takes a step without a status (recorded before statuses were stored) as running", () => {
    expect(nodeResultOfRecord({ node_id: "llm_1", input_preview: "in" }).status).toBe("running")
    expect(nodeResultOfRecord({ status: "nonsense" }).status).toBe("running")
    expect(nodeResultOfRecord(null).status).toBe("running")
  })

  it("keeps the error of a failed step and the waiting status", () => {
    expect(nodeResultOfRecord({ status: "failed", error: "boom" })).toMatchObject({
      status: "failed",
      error: "boom",
    })
    expect(nodeResultOfRecord({ status: "waiting" }).status).toBe("waiting")
  })
})

describe("panelStateOfRun", () => {
  it("shows a waiting run as active, with what it waits on and no outcome", () => {
    const state = panelStateOfRun(
      record({
        status: "waiting",
        node_results: {
          start_1: { status: "completed", duration_ms: 1 },
          h_1: { status: "waiting" },
        } as unknown as WorkflowRunResponse["node_results"],
        waiting_for: [{ node_id: "h_1", confirmation_id: "c1", kind: "workflow_approval", expires_at: null }],
      }),
    )
    expect(state.status).toBe("waiting")
    expect(state.isActive).toBe(true)
    expect(state.waitingFor.map((w) => w.confirmation_id)).toEqual(["c1"])
    expect(state.nodeResults.h_1.status).toBe("waiting")
    expect(state.finalOutputs).toBeNull()
    expect(state.finalError).toBeNull()
    expect(state.runDuration).toBeNull()
  })

  it("shows the outputs and duration of a completed run", () => {
    const state = panelStateOfRun(
      record({ status: "completed", outputs: { out: "hi" }, duration_ms: 1200 }),
    )
    expect(state).toMatchObject({
      status: "completed",
      isActive: false,
      finalOutputs: { out: "hi" },
      finalError: null,
      runDuration: 1200,
      waitingFor: [],
    })
  })

  it("shows the error of a failed run and no outputs", () => {
    const state = panelStateOfRun(
      record({ status: "failed", error: "Step failed", outputs: { partial: 1 }, duration_ms: 5 }),
    )
    expect(state).toMatchObject({
      status: "failed",
      isActive: false,
      finalOutputs: null,
      finalError: "Step failed",
      runDuration: 5,
    })
  })

  it("shows a cancelled run as ended, without an error", () => {
    const state = panelStateOfRun(record({ status: "cancelled", waiting_for: [{ node_id: "h", confirmation_id: "c" }] }))
    expect(state).toMatchObject({ status: "cancelled", isActive: false, finalError: null, waitingFor: [] })
  })
})

describe("mergeNodeResults", () => {
  it("takes the record's steps and keeps steps the record does not have yet", () => {
    const merged = mergeNodeResults(
      { a: result("completed"), b: result("running") },
      { b: result("completed", { output: "x" }), c: result("running") },
    )
    expect(merged.a.status).toBe("completed")
    expect(merged.b).toMatchObject({ status: "completed", output: "x" })
    expect(merged.c.status).toBe("running")
  })

  it("does not put a step that ended back to running when the record is behind", () => {
    const merged = mergeNodeResults({ a: result("completed", { output: "done" }) }, { a: result("running") })
    expect(merged.a).toMatchObject({ status: "completed", output: "done" })
  })

  it("moves a waiting step on once the record says so", () => {
    expect(mergeNodeResults({ h: result("waiting") }, { h: result("completed") }).h.status).toBe("completed")
    expect(mergeNodeResults(null, { h: result("waiting") }).h.status).toBe("waiting")
  })
})

// The Logs tab was filled from the stream only, so a run followed by its
// record (after it waited, after the stream dropped, or attached on
// reopening the editor) had an empty log.
describe("logEventsOfRecord", () => {
  const NOW = 9_000
  const steps = (entries: Record<string, unknown>) => entries as unknown as WorkflowRunResponse["node_results"]
  const brief = (events: WorkflowLogEvent[]) => events.map((e) => `${e.eventType}:${e.nodeId ?? "-"}`)

  it("has one entry per step, of the type of the step's state", () => {
    const events = logEventsOfRecord(
      record({
        status: "waiting",
        node_results: steps({
          a: { node_id: "a", status: "completed", ts: 1, duration_ms: 12, output_preview: "out" },
          b: { node_id: "b", status: "failed", ts: 2, error: "boom" },
          c: { node_id: "c", status: "skipped", ts: 3, reason: "branch not taken" },
          d: { node_id: "d", status: "waiting", ts: 4 },
          e: { node_id: "e", status: "running", ts: 5 },
          f: { node_id: "f", ts: 6 },
          g: { node_id: "g", status: "pending" },
        }),
      }),
      NOW,
    )
    expect(brief(events)).toEqual([
      "node_completed:a",
      "node_failed:b",
      "node_skipped:c",
      "node_waiting:d",
      "node_started:e",
      "node_started:f",
    ])
    expect(events[0]).toEqual({
      timestamp: 1,
      eventType: "node_completed",
      nodeId: "a",
      details: { node_id: "a", status: "completed", ts: 1, duration_ms: 12, output_preview: "out" },
    })
    expect(events[1].details.error).toBe("boom")
    expect(events[2].details.reason).toBe("branch not taken")
  })

  it("orders the steps by when their last event happened", () => {
    const events = logEventsOfRecord(
      record({
        node_results: steps({
          end_1: { status: "running", ts: 30 },
          start_1: { status: "completed", ts: 10 },
          llm_1: { status: "completed", ts: 20 },
        }),
      }),
      NOW,
    )
    expect(brief(events)).toEqual(["node_completed:start_1", "node_completed:llm_1", "node_started:end_1"])
    expect(events.map((e) => e.timestamp)).toEqual([10, 20, 30])
  })

  it("keeps the record's order, at the given time, when a step has no time", () => {
    const events = logEventsOfRecord(
      record({
        node_results: steps({
          b: { status: "completed", ts: 20 },
          a: { status: "completed" },
        }),
      }),
      NOW,
    )
    expect(brief(events)).toEqual(["node_completed:b", "node_completed:a"])
    expect(events.map((e) => e.timestamp)).toEqual([20, NOW])
  })

  it("ends with the run's own entry once the run has ended", () => {
    const done = logEventsOfRecord(
      record({ status: "completed", duration_ms: 40, node_results: steps({ a: { status: "completed", ts: 7 } }) }),
      NOW,
    )
    expect(brief(done)).toEqual(["node_completed:a", "run_completed:-"])
    expect(done[1]).toEqual({
      timestamp: 7,
      eventType: "run_completed",
      nodeId: null,
      details: { status: "completed", duration_ms: 40 },
    })

    const failed = logEventsOfRecord(record({ status: "failed", error: "boom", duration_ms: 3 }), NOW)
    expect(failed).toEqual([
      { timestamp: NOW, eventType: "run_failed", nodeId: null, details: { status: "failed", duration_ms: 3, error: "boom" } },
    ])

    const cancelled = logEventsOfRecord(record({ status: "cancelled" }), NOW)
    expect(cancelled.map((e) => [e.eventType, e.details.status])).toEqual([["run_completed", "cancelled"]])
  })

  it("has no entry for the run while it runs or waits", () => {
    expect(logEventsOfRecord(record({ status: "running" }), NOW)).toEqual([])
    expect(logEventsOfRecord(record({ status: "waiting", node_results: null }), NOW)).toEqual([])
  })
})

describe("mergeLogEvents", () => {
  const entry = (eventType: WorkflowLogEvent["eventType"], nodeId: string | null, over: Partial<WorkflowLogEvent> = {}): WorkflowLogEvent => ({
    timestamp: 1,
    eventType,
    nodeId,
    details: {},
    ...over,
  })

  it("adds what the record has and the panel does not, after what the panel has", () => {
    const shown = [entry("node_started", "a"), entry("node_completed", "a")]
    const merged = mergeLogEvents(shown, [
      entry("node_completed", "a"),
      entry("node_completed", "b"),
      entry("run_completed", null),
    ])
    expect(merged.map((e) => `${e.eventType}:${e.nodeId ?? "-"}`)).toEqual([
      "node_started:a",
      "node_completed:a",
      "node_completed:b",
      "run_completed:-",
    ])
  })

  it("keeps the panel's entry when both have one for a step and event", () => {
    const fromStream = entry("node_completed", "a", { timestamp: 5, details: { output_preview: "full", input_preview: "in" } })
    const merged = mergeLogEvents([fromStream], [entry("node_completed", "a", { timestamp: 6, details: {} })])
    expect(merged).toEqual([fromStream])
  })

  it("keeps a step's earlier entry when the record has moved on", () => {
    const merged = mergeLogEvents([entry("node_waiting", "h")], [entry("node_completed", "h")])
    expect(merged.map((e) => e.eventType)).toEqual(["node_waiting", "node_completed"])
  })

  it("does not start a step again that the panel saw end", () => {
    const shown = [entry("node_completed", "a")]
    expect(mergeLogEvents(shown, [entry("node_started", "a"), entry("node_waiting", "a")])).toBe(shown)
  })

  it("is what the record has when the panel has nothing", () => {
    const fromRecord = [entry("node_completed", "a"), entry("run_completed", null)]
    expect(mergeLogEvents([], fromRecord)).toEqual(fromRecord)
  })
})
