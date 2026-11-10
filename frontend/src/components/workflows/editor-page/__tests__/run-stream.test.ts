import { describe, expect, it } from "vitest"

import {
  createSseParser,
  logEventOf,
  nodeCountOfRunStarted,
  nodeResultOfEvent,
  nodeStatusOfEvent,
  runIdOfRunStarted,
  waitingItemsOf,
  waitingOfRunWaiting,
} from "@/components/workflows/editor-page/run-stream"

function collect() {
  const events: Array<[string, string]> = []
  const feed = createSseParser((event, data) => events.push([event, data]))
  return { events, feed }
}

describe("createSseParser", () => {
  it("emits one event per blank-line-terminated block", () => {
    const { events, feed } = collect()
    feed('event: node_started\ndata: {"node_id":"a"}\n\nevent: end\ndata: {}\n\n')
    expect(events).toEqual([
      ["node_started", '{"node_id":"a"}'],
      ["end", "{}"],
    ])
  })

  it("joins chunks that split a line or an event", () => {
    const { events, feed } = collect()
    feed("event: node_comp")
    feed('leted\ndata: {"node_id"')
    feed(':"a"}\n')
    expect(events).toEqual([])
    feed("\n")
    expect(events).toEqual([["node_completed", '{"node_id":"a"}']])
  })

  it("defaults the event name to message and resets it after each event", () => {
    const { events, feed } = collect()
    feed("event: run_started\ndata: 1\n\ndata: 2\n\n")
    expect(events).toEqual([
      ["run_started", "1"],
      ["message", "2"],
    ])
  })

  it("joins multi-line data with a newline", () => {
    const { events, feed } = collect()
    feed("data: first\ndata: second\n\n")
    expect(events).toEqual([["message", "first\nsecond"]])
  })

  it("skips keepalive comments and events without data", () => {
    const { events, feed } = collect()
    feed(": keepalive\n\nevent: ping\n\nevent: end\ndata: {}\n\n")
    expect(events).toEqual([["end", "{}"]])
  })
})

describe("run events", () => {
  it("maps node events to a node status", () => {
    expect(nodeStatusOfEvent("node_started")).toBe("running")
    expect(nodeStatusOfEvent("node_completed")).toBe("completed")
    expect(nodeStatusOfEvent("node_failed")).toBe("failed")
    expect(nodeStatusOfEvent("node_retrying")).toBe("retrying")
    expect(nodeStatusOfEvent("node_skipped")).toBe("skipped")
    expect(nodeStatusOfEvent("run_completed")).toBeNull()
    expect(nodeStatusOfEvent("toString")).toBeNull()
  })

  it("logs node and run outcomes, not bookkeeping events", () => {
    expect(logEventOf("node_started", { node_id: "a", ts: 5 }, 99)).toEqual({
      timestamp: 5,
      eventType: "node_started",
      nodeId: "a",
      details: { node_id: "a", ts: 5 },
    })
    expect(logEventOf("run_completed", {}, 99)).toMatchObject({ timestamp: 99, nodeId: null })
    expect(logEventOf("run_started", { ts: 1 }, 99)).toBeNull()
    expect(logEventOf("end", {}, 99)).toBeNull()
  })

  it("builds a node result from a completion event", () => {
    expect(
      nodeResultOfEvent(
        "completed",
        { output_preview: "hi", duration_ms: 12, input_preview: "{}" },
        undefined,
      ),
    ).toMatchObject({
      status: "completed",
      output: "hi",
      error: null,
      duration_ms: 12,
      input_preview: "{}",
    })
  })

  it("keeps the earlier input preview, output and duration when an event omits them", () => {
    const started = nodeResultOfEvent("running", { input_preview: "{in}" }, undefined)
    const retrying = nodeResultOfEvent(
      "retrying",
      { previous_error: "timeout", attempt: 2, max_retries: 3 },
      { ...started, output: "partial", duration_ms: 7 },
    )
    expect(retrying).toMatchObject({
      status: "retrying",
      input_preview: "{in}",
      output: "partial",
      duration_ms: 7,
      error: "timeout",
      retryAttempt: 2,
      maxRetries: 3,
    })
  })

  it("reads how many steps the run executes from run_started", () => {
    // The stream opens with two run_started events; only the second has the count.
    expect(nodeCountOfRunStarted("run_started", { run_id: "r", status: "running" })).toBeNull()
    expect(nodeCountOfRunStarted("run_started", { run_id: "r", workflow_id: "w", node_count: 8 })).toBe(8)
    expect(nodeCountOfRunStarted("run_started", { node_count: "8" })).toBeNull()
    expect(nodeCountOfRunStarted("node_started", { node_count: 8 })).toBeNull()
  })

  it("takes the error of a failed node", () => {
    expect(nodeResultOfEvent("failed", { error: "boom" }, undefined).error).toBe("boom")
  })

  // A run that stops for a decision reports the step as waiting and then
  // says what it waits on; neither used to be understood.
  it("puts a step that waits for a decision in the waiting status", () => {
    expect(nodeStatusOfEvent("node_waiting")).toBe("waiting")
    expect(
      nodeResultOfEvent("waiting", { node_id: "h_1", input_preview: "in" }, undefined),
    ).toMatchObject({ status: "waiting", input_preview: "in", error: null })
    expect(logEventOf("node_waiting", { node_id: "h_1" }, 7)).toMatchObject({
      eventType: "node_waiting",
      nodeId: "h_1",
    })
  })

  it("reads the run id from either run_started event", () => {
    expect(runIdOfRunStarted("run_started", { run_id: "r1", status: "running" })).toBe("r1")
    expect(runIdOfRunStarted("run_started", { run_id: "r1", workflow_id: "w", node_count: 2 })).toBe("r1")
    expect(runIdOfRunStarted("run_started", { node_count: 2 })).toBeNull()
    expect(runIdOfRunStarted("node_started", { run_id: "r1" })).toBeNull()
  })

  it("reads what a waiting run waits on from run_waiting", () => {
    const waiting = [
      { node_id: "h_1", confirmation_id: "c1", kind: "workflow_approval", expires_at: "2026-01-02T00:00:00" },
    ]
    expect(waitingOfRunWaiting("run_waiting", { run_id: "r1", status: "waiting", waiting })).toEqual(waiting)
    expect(waitingOfRunWaiting("run_completed", { waiting })).toBeNull()
    expect(waitingOfRunWaiting("run_waiting", {})).toEqual([])
  })

  it("drops waiting entries that name no request", () => {
    expect(waitingItemsOf([{ node_id: "a" }, null, "x", { confirmation_id: "c2" }])).toEqual([
      { node_id: "", confirmation_id: "c2", kind: null, expires_at: null },
    ])
    expect(waitingItemsOf(null)).toEqual([])
  })
})
