import { useCallback, useRef, useState } from "react"
import type { Node, Edge } from "@xyflow/react"

interface HistorySnapshot {
  nodes: Node[]
  edges: Edge[]
}

const MAX_HISTORY = 50
const DEBOUNCE_MS = 300

/**
 * Custom hook for undo/redo in the workflow editor.
 *
 * Maintains a stack of { nodes, edges } snapshots with debounced pushState
 * to avoid flooding on drag operations. Max 50 entries.
 */
export function useWorkflowHistory(initialNodes: Node[], initialEdges: Edge[]) {
  // History stack and pointer. We store snapshots in a ref to avoid
  // re-renders on every push; only canUndo/canRedo trigger re-renders.
  const historyRef = useRef<HistorySnapshot[]>([
    { nodes: structuredClone(initialNodes), edges: structuredClone(initialEdges) },
  ])
  const pointerRef = useRef(0)
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Reactive booleans for the UI
  const [canUndo, setCanUndo] = useState(false)
  const [canRedo, setCanRedo] = useState(false)

  const updateFlags = useCallback(() => {
    setCanUndo(pointerRef.current > 0)
    setCanRedo(pointerRef.current < historyRef.current.length - 1)
  }, [])

  /**
   * Push a new snapshot onto the history stack (debounced).
   * Discards any redo entries beyond the current pointer.
   */
  const pushState = useCallback(
    (nodes: Node[], edges: Edge[]) => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }

      debounceTimerRef.current = setTimeout(() => {
        const snapshot: HistorySnapshot = {
          nodes: structuredClone(nodes),
          edges: structuredClone(edges),
        }

        // Truncate any forward history (redo entries)
        const history = historyRef.current
        history.splice(pointerRef.current + 1)

        // Push new entry
        history.push(snapshot)

        // Enforce max size — drop oldest entries
        if (history.length > MAX_HISTORY) {
          const excess = history.length - MAX_HISTORY
          history.splice(0, excess)
          pointerRef.current = history.length - 1
        } else {
          pointerRef.current = history.length - 1
        }

        updateFlags()
      }, DEBOUNCE_MS)
    },
    [updateFlags],
  )

  /**
   * Move back in history. Returns the previous snapshot, or null
   * if already at the beginning.
   */
  const undo = useCallback((): HistorySnapshot | null => {
    // Flush any pending debounced push so we don't lose the current state
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current)
      debounceTimerRef.current = null
    }

    if (pointerRef.current <= 0) return null
    pointerRef.current -= 1
    const snapshot = historyRef.current[pointerRef.current]
    updateFlags()
    return snapshot ? {
      nodes: structuredClone(snapshot.nodes),
      edges: structuredClone(snapshot.edges),
    } : null
  }, [updateFlags])

  /**
   * Move forward in history. Returns the next snapshot, or null
   * if already at the end.
   */
  const redo = useCallback((): HistorySnapshot | null => {
    if (pointerRef.current >= historyRef.current.length - 1) return null
    pointerRef.current += 1
    const snapshot = historyRef.current[pointerRef.current]
    updateFlags()
    return snapshot ? {
      nodes: structuredClone(snapshot.nodes),
      edges: structuredClone(snapshot.edges),
    } : null
  }, [updateFlags])

  /**
   * Reset the history stack with new initial state.
   * Useful when loading a different workflow.
   */
  const resetHistory = useCallback(
    (nodes: Node[], edges: Edge[]) => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
        debounceTimerRef.current = null
      }
      historyRef.current = [
        { nodes: structuredClone(nodes), edges: structuredClone(edges) },
      ]
      pointerRef.current = 0
      updateFlags()
    },
    [updateFlags],
  )

  return {
    pushState,
    undo,
    redo,
    canUndo,
    canRedo,
    resetHistory,
  }
}
