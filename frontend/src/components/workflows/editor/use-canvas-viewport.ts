import { useCallback, useRef, useState } from "react"

import type { CanvasViewport } from "@/lib/workflow-blueprint"

/** The viewport of a blueprint whose canvas was never panned or zoomed. */
const UNSET_VIEWPORT: CanvasViewport = { x: 0, y: 0, zoom: 1 }

function isViewport(value: unknown): value is CanvasViewport {
  const v = value as CanvasViewport | null | undefined
  return !!v && Number.isFinite(v.x) && Number.isFinite(v.y) && Number.isFinite(v.zoom) && v.zoom > 0
}

/**
 * The pan and zoom a blueprint was saved with, or null when it has none: a
 * missing or malformed viewport, or the { 0, 0, 1 } every blueprint starts
 * with, which nobody chose. The canvas then fits the graph into view.
 */
export function savedViewportOf(stored: unknown): CanvasViewport | null {
  if (!isViewport(stored)) return null
  if (stored.x === UNSET_VIEWPORT.x && stored.y === UNSET_VIEWPORT.y && stored.zoom === UNSET_VIEWPORT.zoom) {
    return null
  }
  return { x: stored.x, y: stored.y, zoom: stored.zoom }
}

/** Sub-pixel noise does not make a different viewport. */
function rounded(viewport: CanvasViewport): CanvasViewport {
  return {
    x: Math.round(viewport.x * 100) / 100,
    y: Math.round(viewport.y * 100) / 100,
    zoom: Math.round(viewport.zoom * 10000) / 10000,
  }
}

/**
 * Pan and zoom of the canvas as part of the blueprint.
 *
 * `initial` is what the canvas opens with (null: fit the graph into view).
 * `viewport` is what a save stores; it follows `onMoveEnd`, React Flow's
 * report of a finished pan or zoom. It is not node or edge state, so a pan
 * or zoom is never an undo step.
 *
 * The fit a canvas without a saved viewport starts with is reported like a
 * pan, with no event behind it. That one report is not taken: opening a
 * workflow must not change it.
 */
export function useCanvasViewport(stored: unknown) {
  const [initial] = useState(() => savedViewportOf(stored))
  const [viewport, setViewport] = useState<CanvasViewport>(
    () => initial ?? (isViewport(stored) ? { x: stored.x, y: stored.y, zoom: stored.zoom } : UNSET_VIEWPORT),
  )
  const awaitingOpeningFitRef = useRef(initial === null)

  const onMoveEnd = useCallback((event: unknown, moved: CanvasViewport) => {
    if (awaitingOpeningFitRef.current) {
      awaitingOpeningFitRef.current = false
      // A gesture has an event; the opening fit has none.
      if (!event) return
    }
    if (!isViewport(moved)) return
    const next = rounded(moved)
    setViewport((prev) =>
      prev.x === next.x && prev.y === next.y && prev.zoom === next.zoom ? prev : next,
    )
  }, [])

  return { initial, viewport, onMoveEnd }
}
