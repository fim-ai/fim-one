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

  describe("only a change of the graph is a step", () => {
    it("a click that selects a node is not recorded", () => {
      const { result } = renderHook(() => useWorkflowHistory([node("a"), node("b")], []))
      // What React Flow hands back after a click on "a": the same graph,
      // with the node marked selected and its measured size filled in.
      act(() =>
        result.current.pushState(
          [{ ...node("a"), selected: true, measured: { width: 240, height: 80 } }, node("b")],
          [],
        ),
      )
      expect(result.current.pending).toBe(true)
      act(() => vi.advanceTimersByTime(300))
      expect(result.current.pending).toBe(false)
      expect(result.current.revision).toBe(0)
      expect(result.current.canUndo).toBe(false)
      let snapshot: ReturnType<typeof result.current.undo> = null
      act(() => {
        snapshot = result.current.undo()
      })
      expect(snapshot).toBeNull()
    })

    it("a click on the canvas after an undo keeps what can be redone", () => {
      const { result } = renderHook(() => useWorkflowHistory([node("a")], []))
      act(() => result.current.pushState([node("a"), node("b")], []))
      act(() => vi.advanceTimersByTime(300))
      act(() => {
        result.current.undo()
      })
      expect(result.current.canRedo).toBe(true)
      act(() => result.current.pushState([{ ...node("a"), selected: true }], []))
      act(() => vi.advanceTimersByTime(300))
      expect(result.current.canRedo).toBe(true)
    })

    it("run and validation overlays stamped on a node are not recorded", () => {
      const { result } = renderHook(() => useWorkflowHistory([node("a")], []))
      act(() =>
        result.current.pushState(
          [{ ...node("a"), data: { _validationState: { errors: ["x"] }, _runOverlay: {}, runStatus: "completed" } }],
          [],
        ),
      )
      act(() => vi.advanceTimersByTime(300))
      expect(result.current.canUndo).toBe(false)
    })

    it("moving a node, changing its settings and connecting two nodes are each a step", () => {
      const { result } = renderHook(() => useWorkflowHistory([node("a"), node("b")], []))
      act(() => result.current.pushState([{ ...node("a"), position: { x: 16, y: 0 } }, node("b")], []))
      act(() => vi.advanceTimersByTime(300))
      expect(result.current.revision).toBe(1)
      const moved = { ...node("a"), position: { x: 16, y: 0 }, data: { prompt_template: "hi" } }
      act(() => result.current.pushState([moved, node("b")], []))
      act(() => vi.advanceTimersByTime(300))
      expect(result.current.revision).toBe(2)
      act(() => result.current.pushState([moved, node("b")], [{ id: "e1", source: "a", target: "b" }]))
      act(() => vi.advanceTimersByTime(300))
      expect(result.current.revision).toBe(3)
    })

    it("a selection change made together with an edit is recorded with it", () => {
      const { result } = renderHook(() => useWorkflowHistory([node("a")], []))
      act(() => result.current.pushState([node("a"), node("b")], []))
      act(() => vi.advanceTimersByTime(100))
      act(() => result.current.pushState([node("a"), { ...node("b"), selected: true }], []))
      act(() => vi.advanceTimersByTime(300))
      expect(result.current.revision).toBe(1)
      expect(result.current.canUndo).toBe(true)
    })
  })
})
