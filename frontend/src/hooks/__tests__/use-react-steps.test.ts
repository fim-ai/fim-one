import { describe, it, expect } from "vitest"
import { renderHook } from "@testing-library/react"
import { useReactSteps } from "@/hooks/use-react-steps"
import type { SSEMessage } from "@/hooks/use-sse"
import type { ReactStepEvent } from "@/types/api"

let ts = 1_000_000
function msg(event: string, data: unknown): SSEMessage {
  ts += 1000
  return { event, data, timestamp: ts } as SSEMessage
}

function stepsOf(items: { event: string; data: unknown }[]) {
  return items
    .filter(i => i.event === "step")
    .map(i => i.data as ReactStepEvent)
}

describe("useReactSteps dangling thinking rounds", () => {
  it("closes a gate round's thinking card when the next round starts", () => {
    // Gate turn: iteration 2 gets a thinking start but no done (the loop
    // continued — finish checklist / background wait), then iteration 3
    // starts. Iteration 2's card must not stay in "start" (spinner) state.
    const messages: SSEMessage[] = [
      msg("step", { type: "thinking", status: "start", iteration: 1 }),
      msg("step", { type: "thinking", status: "done", iteration: 1 }),
      msg("step", {
        type: "iteration",
        status: "start",
        iteration: 1,
        tool_name: "echo",
      }),
      msg("step", {
        type: "iteration",
        status: "done",
        iteration: 1,
        tool_name: "echo",
      }),
      msg("step", { type: "thinking", status: "start", iteration: 2 }),
      msg("step", { type: "thinking", status: "delta", content: "checking" }),
      // no thinking done for iteration 2 — gate turn
      msg("step", { type: "thinking", status: "start", iteration: 3 }),
    ]
    const { result } = renderHook(() => useReactSteps(messages, true))

    const thinking = stepsOf(result.current.items).filter(
      s => s.type === "thinking",
    )
    const iter2 = thinking.find(s => s.iteration === 2)
    const iter3 = thinking.find(s => s.iteration === 3)
    expect(iter2?.status).toBe("done")
    expect(iter2?.reasoning).toBe("checking")
    // The new round is the live one.
    expect(iter3?.status).toBe("start")
  })

  it("closes a dangling thinking card when the answer starts", () => {
    const messages: SSEMessage[] = [
      msg("step", { type: "thinking", status: "start", iteration: 1 }),
      // no done — handoff turn
      msg("step", { type: "answer", status: "start" }),
    ]
    const { result } = renderHook(() => useReactSteps(messages, true))

    const thinking = stepsOf(result.current.items).filter(
      s => s.type === "thinking",
    )
    expect(thinking[0]?.status).toBe("done")
  })

  it("leaves normally merged rounds untouched", () => {
    const messages: SSEMessage[] = [
      msg("step", { type: "thinking", status: "start", iteration: 1 }),
      msg("step", {
        type: "thinking",
        status: "done",
        iteration: 1,
        reasoning: "planned",
      }),
      msg("step", { type: "thinking", status: "start", iteration: 2 }),
    ]
    const { result } = renderHook(() => useReactSteps(messages, true))

    const thinking = stepsOf(result.current.items).filter(
      s => s.type === "thinking",
    )
    expect(thinking).toHaveLength(2)
    expect(thinking[0]?.status).toBe("done")
    expect(thinking[0]?.reasoning).toBe("planned")
    expect(thinking[1]?.status).toBe("start")
  })
})
