import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, renderHook } from "@testing-library/react"
import type { Node } from "@xyflow/react"

import { useWorkflowHistory } from "@/hooks/use-workflow-history"

const node = (id: string): Node => ({ id, type: "llm", position: { x: 0, y: 0 }, data: {} })

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("useWorkflowHistory", () => {
  it("records a change after the debounce and says while one is waiting", () => {
    const { result } = renderHook(() => useWorkflowHistory([node("a")], []))
    expect(result.current.pending).toBe(false)
    expect(result.current.revision).toBe(0)
    expect(result.current.canUndo).toBe(false)

    act(() => result.current.pushState([node("a"), node("b")], []))
    expect(result.current.pending).toBe(true)
    expect(result.current.revision).toBe(0)

    act(() => vi.advanceTimersByTime(300))
    expect(result.current.pending).toBe(false)
    expect(result.current.revision).toBe(1)
    expect(result.current.canUndo).toBe(true)
  })

  it("merges changes that follow each other within the debounce into one step", () => {
    const { result } = renderHook(() => useWorkflowHistory([node("a")], []))
    act(() => result.current.pushState([node("a"), node("b")], []))
    act(() => vi.advanceTimersByTime(200))
    act(() => result.current.pushState([node("a"), node("b"), node("c")], []))
    act(() => vi.advanceTimersByTime(300))
    expect(result.current.revision).toBe(1)
    let snapshot: ReturnType<typeof result.current.undo> = null
    act(() => {
      snapshot = result.current.undo()
    })
    expect(snapshot!.nodes.map((n) => n.id)).toEqual(["a"])
  })

  it("undo and redo move through the steps and count as changes of the stack", () => {
    const { result } = renderHook(() => useWorkflowHistory([node("a")], []))
    act(() => result.current.pushState([node("a"), node("b")], []))
    act(() => vi.advanceTimersByTime(300))
    act(() => {
      result.current.undo()
    })
    expect(result.current.revision).toBe(2)
    expect(result.current.canRedo).toBe(true)
    let snapshot: ReturnType<typeof result.current.redo> = null
    act(() => {
      snapshot = result.current.redo()
    })
    expect(snapshot!.nodes.map((n) => n.id)).toEqual(["a", "b"])
    expect(result.current.revision).toBe(3)
    expect(result.current.pending).toBe(false)
  })

  it("undo drops a change that was still waiting", () => {
    const { result } = renderHook(() => useWorkflowHistory([node("a")], []))
    act(() => result.current.pushState([node("a"), node("b")], []))
    act(() => vi.advanceTimersByTime(300))
    act(() => result.current.pushState([node("a"), node("b"), node("c")], []))
    expect(result.current.pending).toBe(true)
    act(() => {
      result.current.undo()
    })
    expect(result.current.pending).toBe(false)
    act(() => vi.advanceTimersByTime(1000))
    // The waiting change was not recorded afterwards.
    expect(result.current.canRedo).toBe(true)
    let snapshot: ReturnType<typeof result.current.redo> = null
    act(() => {
      snapshot = result.current.redo()
    })
    expect(snapshot!.nodes.map((n) => n.id)).toEqual(["a", "b"])
  })
})
