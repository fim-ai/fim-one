import { describe, expect, it } from "vitest"

import {
  createSseParser,
  logEventOf,
  nodeCountOfRunStarted,
  nodeResultOfEvent,
  nodeStatusOfEvent,
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
})
