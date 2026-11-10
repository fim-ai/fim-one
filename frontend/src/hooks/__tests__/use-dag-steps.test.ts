import { describe, it, expect } from "vitest"
import { renderHook } from "@testing-library/react"
import { useDagSteps } from "@/hooks/use-dag-steps"
import type { SSEMessage } from "@/hooks/use-sse"

let ts = 1_000_000
function msg(event: string, data: unknown): SSEMessage {
  ts += 1000
  return { event, data, timestamp: ts } as SSEMessage
}

function confirmation(id: string) {
  return {
    type: "awaiting_confirmation",
    confirmation_id: id,
    tool_name: "oa__pay",
    arguments: { amount: 5 },
    timeout_at: "2030-01-01T00:00:00+00:00",
    agent_id: "",
    mode: "inline",
    channel_label: "",
    approver_scope: "initiator",
  }
}

describe("useDagSteps approval requests", () => {
  it("collects awaiting_confirmation events so the DAG view can render their cards", () => {
    const messages: SSEMessage[] = [
      msg("phase", { name: "planning", status: "start" }),
      msg("awaiting_confirmation", confirmation("c-1")),
      msg("awaiting_confirmation", confirmation("c-2")),
    ]
    const { result } = renderHook(() => useDagSteps(messages, true))

    expect(result.current.confirmationEvents.map((c) => c.confirmation_id)).toEqual(["c-1", "c-2"])
    // The payload is passed through as the backend sent it.
    expect(result.current.confirmationEvents[0]).toEqual(confirmation("c-1"))
  })

  it("keeps one entry per request when a resumed stream replays the event", () => {
    const messages: SSEMessage[] = [
      msg("awaiting_confirmation", confirmation("c-1")),
      msg("resume_done", {}),
      msg("awaiting_confirmation", confirmation("c-1")),
    ]
    const { result } = renderHook(() => useDagSteps(messages, true))

    expect(result.current.confirmationEvents).toHaveLength(1)
  })

  it("has none in a turn that asked for nothing", () => {
    const { result } = renderHook(() =>
      useDagSteps([msg("phase", { name: "planning", status: "start" })], true),
    )
    expect(result.current.confirmationEvents).toEqual([])
  })
})
