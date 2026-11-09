import { useCallback, useEffect, useRef, useState } from "react"
import type { Edge, Node } from "@xyflow/react"

import { serializeBlueprint, type CanvasNode, type CanvasViewport } from "@/lib/workflow-blueprint"
import type { WorkflowBlueprint } from "@/types/workflow"

/** How long the canvas waits after its last change before handing it out. */
export const BLUEPRINT_SYNC_DELAY_MS = 300

/**
 * Hand the canvas to the page as a v2 blueprint, a moment after the last
 * change to nodes or edges. Returns `serialize`, which gives the canvas as
 * it is right now for a caller that cannot wait for that moment (Save), and
 * `pending` / `reports`: whether a report is waiting out the pause and how
 * many were made, so a test can wait for "reported" instead of sleeping.
 */
export function useBlueprintSync(
  nodes: Node[],
  edges: Edge[],
  viewport: CanvasViewport | undefined,
  onBlueprintChange: (blueprint: WorkflowBlueprint) => void,
) {
  // v2 blueprint: run-time overlay fields are stripped by the serializer.
  const serialize = useCallback(
    () =>
      serializeBlueprint(
        nodes as CanvasNode[],
        edges,
        viewport ?? { x: 0, y: 0, zoom: 1 },
      ) as unknown as WorkflowBlueprint,
    [nodes, edges, viewport],
  )
  const serializeRef = useRef(serialize)
  serializeRef.current = serialize

  // Sync blueprint out when nodes/edges change (debounced)
  // Use a ref for the callback so that callback identity changes (caused by
  // parent re-renders after save) don't re-trigger the sync effect and
  // spuriously mark the form dirty again.
  const onBlueprintChangeRef = useRef(onBlueprintChange)
  onBlueprintChangeRef.current = onBlueprintChange

  const [pending, setPending] = useState(false)
  const [reports, setReports] = useState(0)
  const syncTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (syncTimer.current) clearTimeout(syncTimer.current)
    setPending(true)
    syncTimer.current = setTimeout(() => {
      onBlueprintChangeRef.current(serializeRef.current())
      setPending(false)
      setReports((n) => n + 1)
    }, BLUEPRINT_SYNC_DELAY_MS)
    return () => {
      if (syncTimer.current) clearTimeout(syncTimer.current)
    }
    // Only sync when nodes/edges actually change — NOT on callback identity change
  }, [nodes, edges])

  return { serialize, pending, reports }
}
