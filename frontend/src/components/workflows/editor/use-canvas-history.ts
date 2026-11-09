import { useCallback, useEffect, useRef } from "react"
import type { Dispatch, SetStateAction } from "react"
import type { Edge, Node } from "@xyflow/react"

import { useWorkflowHistory } from "@/hooks/use-workflow-history"
import { toSafeEdges } from "./canvas-model"

interface CanvasHistoryOptions {
  nodes: Node[]
  edges: Edge[]
  setNodes: Dispatch<SetStateAction<Node[]>>
  setEdges: Dispatch<SetStateAction<Edge[]>>
  /** The canvas as loaded: the first entry of the history. */
  initialNodes: Node[]
  initialEdges: Edge[]
}

/**
 * Undo / redo for the canvas: every change of nodes or edges is recorded,
 * and undo / redo put a recorded state back without recording it again.
 */
export function useCanvasHistory({
  nodes,
  edges,
  setNodes,
  setEdges,
  initialNodes,
  initialEdges,
}: CanvasHistoryOptions) {
  const { pushState, undo, redo, canUndo, canRedo } = useWorkflowHistory(initialNodes, initialEdges)

  // Track whether we are currently restoring from history to avoid re-pushing
  const isRestoringRef = useRef(false)

  // Push nodes/edges to history whenever they change (skipped during restore)
  useEffect(() => {
    if (isRestoringRef.current) {
      isRestoringRef.current = false
      return
    }
    pushState(nodes, edges)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges])

  const handleUndo = useCallback(() => {
    const snapshot = undo()
    if (!snapshot) return
    isRestoringRef.current = true
    setNodes(snapshot.nodes)
    setEdges(toSafeEdges(snapshot.edges))
  }, [undo, setNodes, setEdges])

  const handleRedo = useCallback(() => {
    const snapshot = redo()
    if (!snapshot) return
    isRestoringRef.current = true
    setNodes(snapshot.nodes)
    setEdges(toSafeEdges(snapshot.edges))
  }, [redo, setNodes, setEdges])

  return { undo: handleUndo, redo: handleRedo, canUndo, canRedo }
}
