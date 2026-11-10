import { describe, expect, it } from "vitest"

import {
  isActiveRunStatus,
  mergeNodeResults,
  nodeResultOfRecord,
  panelStateOfRun,
} from "@/components/workflows/editor-page/run-record"
import type { NodeRunResult, WorkflowRunResponse } from "@/types/workflow"

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
