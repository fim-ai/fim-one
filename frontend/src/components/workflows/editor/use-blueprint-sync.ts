import { useEffect, useRef } from "react"
import type { Edge, Node } from "@xyflow/react"

import { serializeBlueprint, type CanvasNode, type CanvasViewport } from "@/lib/workflow-blueprint"
import type { WorkflowBlueprint } from "@/types/workflow"

/** How long the canvas waits after its last change before handing it out. */
export const BLUEPRINT_SYNC_DELAY_MS = 300

/**
 * Hand the canvas to the page as a v2 blueprint, a moment after the last
 * change to nodes or edges.
 */
export function useBlueprintSync(
  nodes: Node[],
  edges: Edge[],
  viewport: CanvasViewport | undefined,
  onBlueprintChange: (blueprint: WorkflowBlueprint) => void,
) {
  // Sync blueprint out when nodes/edges change (debounced)
  // Use a ref for the callback so that callback identity changes (caused by
  // parent re-renders after save) don't re-trigger the sync effect and
  // spuriously mark the form dirty again.
  const onBlueprintChangeRef = useRef(onBlueprintChange)
  onBlueprintChangeRef.current = onBlueprintChange

  const syncTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (syncTimer.current) clearTimeout(syncTimer.current)
    syncTimer.current = setTimeout(() => {
      // v2 blueprint: run-time overlay fields are stripped by the serializer.
      onBlueprintChangeRef.current(
        serializeBlueprint(
          nodes as CanvasNode[],
          edges,
          viewport ?? { x: 0, y: 0, zoom: 1 },
        ) as unknown as WorkflowBlueprint,
      )
    }, BLUEPRINT_SYNC_DELAY_MS)
    return () => {
      if (syncTimer.current) clearTimeout(syncTimer.current)
    }
    // Only sync when nodes/edges actually change — NOT on callback identity change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges])
}
