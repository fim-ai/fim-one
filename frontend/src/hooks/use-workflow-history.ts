import { useCallback, useRef, useState } from "react"
import type { Node, Edge } from "@xyflow/react"

import { graphSignature, type CanvasNode } from "@/lib/workflow-blueprint"

interface HistorySnapshot {
  nodes: Node[]
  edges: Edge[]
}

/** A recorded step: the canvas state plus the graph it holds, as text. */
interface HistoryEntry extends HistorySnapshot {
  signature: string
}

function entryOf(nodes: Node[], edges: Edge[]): HistoryEntry {
  return {
    nodes: structuredClone(nodes),
    edges: structuredClone(edges),
    signature: graphSignature(nodes as CanvasNode[], edges),
  }
}

const MAX_HISTORY = 50
const DEBOUNCE_MS = 300

/**
 * Custom hook for undo/redo in the workflow editor.
 *
 * Maintains a stack of { nodes, edges } snapshots with debounced pushState
 * to avoid flooding on drag operations. Max 50 entries.
 *
 * Only a change of the graph is a step: nodes added, removed, moved or
 * configured, edges added or removed. React Flow keeps selection, dragging
 * and measured sizes on the node objects too, and the editor stamps run and
 * validation overlays into node data; a state that differs from the current
 * step only in those is not recorded.
 */
export function useWorkflowHistory(initialNodes: Node[], initialEdges: Edge[]) {
  // History stack and pointer. We store snapshots in a ref to avoid
  // re-renders on every push; only canUndo/canRedo trigger re-renders.
  const historyRef = useRef<HistoryEntry[] | null>(null)
  if (historyRef.current === null) historyRef.current = [entryOf(initialNodes, initialEdges)]
  const pointerRef = useRef(0)
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Reactive booleans for the UI
  const [canUndo, setCanUndo] = useState(false)
  const [canRedo, setCanRedo] = useState(false)
  // How many times the stack changed, and whether a change is waiting out
  // the debounce. Exposed so a test can wait for "recorded" instead of
  // sleeping longer than the debounce.
  const [revision, setRevision] = useState(0)
  const [pending, setPending] = useState(false)

  const updateFlags = useCallback(() => {
    setCanUndo(pointerRef.current > 0)
    setCanRedo(pointerRef.current < historyRef.current!.length - 1)
    setRevision((n) => n + 1)
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
      setPending(true)

      debounceTimerRef.current = setTimeout(() => {
        debounceTimerRef.current = null
        setPending(false)
        const history = historyRef.current!
        const snapshot = entryOf(nodes, edges)
        // The same graph as the current step (a click, a selection change,
        // an overlay): nothing to undo, and redo entries stay.
        if (snapshot.signature === history[pointerRef.current]?.signature) return

        // Truncate any forward history (redo entries)
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
    setPending(false)

    if (pointerRef.current <= 0) return null
    pointerRef.current -= 1
    const snapshot = historyRef.current![pointerRef.current]
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
    if (pointerRef.current >= historyRef.current!.length - 1) return null
    pointerRef.current += 1
    const snapshot = historyRef.current![pointerRef.current]
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
      setPending(false)
      historyRef.current = [entryOf(nodes, edges)]
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
    revision,
    pending,
    resetHistory,
  }
}
