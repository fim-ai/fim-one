import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, renderHook } from "@testing-library/react"
import type { Edge, Node } from "@xyflow/react"

import { useBlueprintSync, BLUEPRINT_SYNC_DELAY_MS } from "@/components/workflows/editor/use-blueprint-sync"
import { savedViewportOf, useCanvasViewport } from "@/components/workflows/editor/use-canvas-viewport"
import type { CanvasViewport } from "@/lib/workflow-blueprint"
import type { WorkflowBlueprint } from "@/types/workflow"

const gesture = new Event("mouseup")

afterEach(cleanup)

describe("savedViewportOf", () => {
  it("is the pan and zoom a blueprint was saved with", () => {
    expect(savedViewportOf({ x: -120, y: 48.5, zoom: 0.75 })).toEqual({ x: -120, y: 48.5, zoom: 0.75 })
  })

  it("is nothing for the viewport every blueprint starts with, a missing one and a broken one", () => {
    expect(savedViewportOf({ x: 0, y: 0, zoom: 1 })).toBeNull()
    expect(savedViewportOf(undefined)).toBeNull()
    expect(savedViewportOf(null)).toBeNull()
    expect(savedViewportOf({ x: 1, y: 2 })).toBeNull()
    expect(savedViewportOf({ x: 1, y: 2, zoom: 0 })).toBeNull()
    expect(savedViewportOf({ x: Number.NaN, y: 2, zoom: 1 })).toBeNull()
  })
})

describe("useCanvasViewport", () => {
  it("opens with the saved pan and zoom", () => {
    const { result } = renderHook(() => useCanvasViewport({ x: -120, y: 48, zoom: 0.75 }))
    expect(result.current.initial).toEqual({ x: -120, y: 48, zoom: 0.75 })
    expect(result.current.viewport).toEqual({ x: -120, y: 48, zoom: 0.75 })
  })

  it("fits the graph into view when none was saved, and that fit does not change the blueprint", () => {
    const { result } = renderHook(() => useCanvasViewport({ x: 0, y: 0, zoom: 1 }))
    expect(result.current.initial).toBeNull()
    // React Flow reports the opening fit as a move with no event behind it.
    act(() => result.current.onMoveEnd(null, { x: 310, y: 95, zoom: 0.8 }))
    expect(result.current.viewport).toEqual({ x: 0, y: 0, zoom: 1 })
  })

  it("follows a pan or zoom the user made", () => {
    const { result } = renderHook(() => useCanvasViewport({ x: 0, y: 0, zoom: 1 }))
    act(() => result.current.onMoveEnd(null, { x: 310, y: 95, zoom: 0.8 }))
    act(() => result.current.onMoveEnd(gesture, { x: 250.004, y: 95, zoom: 0.8 }))
    expect(result.current.viewport).toEqual({ x: 250, y: 95, zoom: 0.8 })
    // The zoom buttons and Fit view move the canvas without an event.
    act(() => result.current.onMoveEnd(null, { x: 200, y: 60, zoom: 1.2 }))
    expect(result.current.viewport).toEqual({ x: 200, y: 60, zoom: 1.2 })
    // What it opened with stays what it opened with.
    expect(result.current.initial).toBeNull()
  })

  it("takes a pan made before the opening fit was reported", () => {
    const { result } = renderHook(() => useCanvasViewport(undefined))
    act(() => result.current.onMoveEnd(gesture, { x: 40, y: 40, zoom: 1 }))
    expect(result.current.viewport).toEqual({ x: 40, y: 40, zoom: 1 })
  })

  it("on a canvas with a saved viewport, takes a move without an event too", () => {
    const { result } = renderHook(() => useCanvasViewport({ x: -120, y: 48, zoom: 0.75 }))
    act(() => result.current.onMoveEnd(null, { x: 0, y: 0, zoom: 1.5 }))
    expect(result.current.viewport).toEqual({ x: 0, y: 0, zoom: 1.5 })
  })

  it("keeps the same object when the canvas ends up where it was", () => {
    const { result } = renderHook(() => useCanvasViewport({ x: -120, y: 48, zoom: 0.75 }))
    const before = result.current.viewport
    act(() => result.current.onMoveEnd(gesture, { x: -120.001, y: 48, zoom: 0.75 }))
    expect(result.current.viewport).toBe(before)
  })
})

describe("useBlueprintSync", () => {
  const nodes: Node[] = [{ id: "start_1", type: "start", position: { x: 0, y: 0 }, data: { variables: [] } }]
  const edges: Edge[] = []

  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  function mount(viewport: CanvasViewport) {
    const onBlueprintChange = vi.fn<(blueprint: WorkflowBlueprint) => void>()
    const hook = renderHook((props: { viewport: CanvasViewport }) => useBlueprintSync(nodes, edges, props.viewport, onBlueprintChange), {
      initialProps: { viewport },
    })
    // The report of the canvas as loaded.
    act(() => vi.advanceTimersByTime(BLUEPRINT_SYNC_DELAY_MS))
    onBlueprintChange.mockClear()
    return { ...hook, onBlueprintChange }
  }

  it("reports a pan or zoom, a moment after it, as the blueprint's viewport", () => {
    const { rerender, result, onBlueprintChange } = mount({ x: 0, y: 0, zoom: 1 })
    rerender({ viewport: { x: -80, y: 32, zoom: 0.5 } })
    expect(result.current.pending).toBe(true)
    // Read directly (Save, Run), the canvas already has it.
    expect(result.current.serialize().viewport).toEqual({ x: -80, y: 32, zoom: 0.5 })
    expect(onBlueprintChange).not.toHaveBeenCalled()
    act(() => vi.advanceTimersByTime(BLUEPRINT_SYNC_DELAY_MS))
    expect(onBlueprintChange).toHaveBeenCalledTimes(1)
    expect(onBlueprintChange.mock.calls[0][0].viewport).toEqual({ x: -80, y: 32, zoom: 0.5 })
    expect(result.current.pending).toBe(false)
  })

  it("does not report again for the same pan and zoom in a new object", () => {
    const { rerender, onBlueprintChange } = mount({ x: -80, y: 32, zoom: 0.5 })
    rerender({ viewport: { x: -80, y: 32, zoom: 0.5 } })
    act(() => vi.advanceTimersByTime(BLUEPRINT_SYNC_DELAY_MS))
    expect(onBlueprintChange).not.toHaveBeenCalled()
  })
})
