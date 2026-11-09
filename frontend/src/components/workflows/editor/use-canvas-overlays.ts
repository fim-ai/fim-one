import { useCallback, useState } from "react"
import type { Dispatch, SetStateAction } from "react"
import type { Node } from "@xyflow/react"

import type { ValidationResult } from "@/components/workflows/workflow-toolbar"
import type { NodeRunResult } from "@/types/workflow"
import {
  validationByNode,
  withRunOverlay,
  withValidationState,
  withoutRunOverlay,
  withoutValidationState,
} from "./canvas-model"

/**
 * What the canvas shows on top of the graph without it being part of the
 * workflow: the results of a past run, and validation badges. Both live in
 * node data under keys the blueprint serializer drops.
 */
export function useCanvasOverlays(setNodes: Dispatch<SetStateAction<Node[]>>) {
  // --- Run overlay state (for viewing past runs on canvas) ---
  const [hasRunOverlay, setHasRunOverlay] = useState(false)

  const applyRunOverlay = useCallback(
    (overlayResults: Record<string, NodeRunResult>) => {
      setNodes((prevNodes) => withRunOverlay(prevNodes, overlayResults))
      setHasRunOverlay(true)
    },
    [setNodes],
  )

  const clearRunOverlay = useCallback(() => {
    setNodes((prevNodes) => withoutRunOverlay(prevNodes))
    setHasRunOverlay(false)
  }, [setNodes])

  // --- Validation overlay handlers ---

  const applyValidationOverlay = useCallback(
    (result: ValidationResult) => {
      const overlay = validationByNode(result)
      setNodes((currentNodes) => withValidationState(currentNodes, overlay))
    },
    [setNodes],
  )

  const clearValidationOverlay = useCallback(() => {
    setNodes((currentNodes) => withoutValidationState(currentNodes))
  }, [setNodes])

  return {
    hasRunOverlay,
    applyRunOverlay,
    clearRunOverlay,
    applyValidationOverlay,
    clearValidationOverlay,
  }
}
