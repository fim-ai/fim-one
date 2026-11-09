/**
 * Pure functions over the canvas state (React Flow nodes and edges).
 *
 * Nothing here touches React state or the DOM: the editor's hooks call
 * these and decide what to do with the result.
 */
import type { Connection, Edge, Node } from "@xyflow/react"

import { fieldOfLoc } from "@/lib/workflow-errors"
import type {
  NodeRunOverlayData,
  NodeRunResult,
  NodeValidationState,
  WorkflowBlueprint,
} from "@/types/workflow"
import type { ValidationResult } from "@/components/workflows/workflow-toolbar"

// ---------------------------------------------------------------------------
// Blueprint -> canvas
// ---------------------------------------------------------------------------

/** Nodes of a stored blueprint as React Flow nodes. */
export function blueprintNodes(blueprint: WorkflowBlueprint): Node[] {
  return blueprint.nodes.map(
    (n) =>
      ({
        id: n.id,
        type: n.type,
        position: n.position,
        data: n.data,
      }) as Node,
  )
}

/** Edges of a stored blueprint as React Flow edges (null handles become undefined). */
export function blueprintEdges(blueprint: WorkflowBlueprint): Edge[] {
  return blueprint.edges.map(
    (e) =>
      ({
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: e.sourceHandle ?? undefined,
        targetHandle: e.targetHandle ?? undefined,
      }) as Edge,
  )
}

/**
 * Normalize edges: convert null sourceHandle/targetHandle to undefined
 * (React Flow's Edge type allows null, useEdgesState infers stricter types).
 */
export function toSafeEdges(raw: Edge[]): Edge[] {
  return raw.map((e) => ({
    ...e,
    sourceHandle: e.sourceHandle ?? undefined,
    targetHandle: e.targetHandle ?? undefined,
  }))
}

/** Id of a new edge between two handles. */
export function edgeId(
  source: string | null,
  sourceHandle: string | null | undefined,
  target: string | null,
  targetHandle: string | null | undefined,
): string {
  return `e-${source}-${sourceHandle ?? "default"}-${target}-${targetHandle ?? "default"}`
}

// ---------------------------------------------------------------------------
// Connections
// ---------------------------------------------------------------------------

/**
 * Whether an edge may be drawn: no self-connections, nothing into Start,
 * nothing out of End, no duplicate of an existing edge.
 */
export function isConnectionAllowed(
  nodes: Node[],
  edges: Edge[],
  connection: Connection | { source: string; target: string },
): boolean {
  // No self-connections
  if (connection.source === connection.target) return false

  const targetNode = nodes.find((n) => n.id === connection.target)
  const sourceNode = nodes.find((n) => n.id === connection.source)
  // Can't connect TO a Start node or FROM an End node
  if (targetNode?.type === "start") return false
  if (sourceNode?.type === "end") return false

  // No duplicate edges (same source → target, same handles)
  const srcHandle = (connection as Connection).sourceHandle ?? null
  const tgtHandle = (connection as Connection).targetHandle ?? null
  const duplicate = edges.some(
    (e) =>
      e.source === connection.source &&
      e.target === connection.target &&
      (e.sourceHandle ?? null) === srcHandle &&
      (e.targetHandle ?? null) === tgtHandle,
  )
  if (duplicate) return false

  return true
}

// ---------------------------------------------------------------------------
// Copy / paste
// ---------------------------------------------------------------------------

export interface CanvasClipboard {
  nodes: Node[]
  edges: Edge[]
}

/** The selected nodes and the edges that run between two of them. */
export function copySelection(nodes: Node[], edges: Edge[]): CanvasClipboard | null {
  const selectedNodes = nodes.filter((n) => n.selected)
  if (selectedNodes.length === 0) return null

  const selectedIds = new Set(selectedNodes.map((n) => n.id))
  // Only copy edges that connect two selected nodes (internal edges)
  const internalEdges = edges.filter(
    (e) => selectedIds.has(e.source) && selectedIds.has(e.target),
  )

  return {
    nodes: selectedNodes.map((n) => ({ ...n, data: { ...n.data } })),
    edges: internalEdges.map((e) => ({ ...e })),
  }
}

/**
 * Copies of the clipboard's nodes under new ids, offset by 50px and selected,
 * plus the edges between them. Start and End are singletons and are left
 * out, together with the edges that touched them.
 */
export function pasteClipboard(clipboard: CanvasClipboard, now: number): CanvasClipboard {
  const { nodes: copiedNodes, edges: copiedEdges } = clipboard

  // Build old-id -> new-id mapping
  const idMap = new Map<string, string>()
  for (let i = 0; i < copiedNodes.length; i++) {
    const oldId = copiedNodes[i].id
    const nodeType = copiedNodes[i].type ?? "node"
    idMap.set(oldId, `${nodeType}_${now}_${i}`)
  }

  // Create new nodes with offset position; skip start/end (singleton constraint)
  const newNodes: Node[] = []
  for (const cn of copiedNodes) {
    if (cn.type === "start" || cn.type === "end") continue
    newNodes.push({
      ...cn,
      id: idMap.get(cn.id)!,
      position: { x: cn.position.x + 50, y: cn.position.y + 50 },
      data: { ...cn.data },
      selected: true,
    })
  }

  if (newNodes.length === 0) return { nodes: [], edges: [] }

  const newNodeIds = new Set(newNodes.map((n) => n.id))

  // Recreate internal edges with new IDs
  const newEdges: Edge[] = []
  for (const ce of copiedEdges) {
    const newSource = idMap.get(ce.source)
    const newTarget = idMap.get(ce.target)
    // Only create edge if both endpoints were pasted (start/end filtering)
    if (newSource && newTarget && newNodeIds.has(newSource) && newNodeIds.has(newTarget)) {
      newEdges.push({
        ...ce,
        id: edgeId(newSource, ce.sourceHandle, newTarget, ce.targetHandle),
        source: newSource,
        target: newTarget,
      })
    }
  }

  return { nodes: newNodes, edges: newEdges }
}

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

/** Keys the editor injects into node data at run time; never searched. */
const TRANSIENT_KEYS = new Set(["runStatus", "_runOverlay", "_validationState"])

/** Nodes whose type, id or text settings contain the query (case-insensitive). */
export function findSearchMatches(nodes: Node[], query: string): Node[] {
  if (!query.trim()) return []
  const q = query.toLowerCase()
  return nodes.filter((node) => {
    // Match against node type name (e.g. "llm", "start", "conditionBranch")
    const typeLabel = (node.type ?? "").toLowerCase()
    // Also check the node ID
    const nodeId = node.id.toLowerCase()

    // Search node data values (prompt_template, variable names, output_variable, etc.)
    const dataStr = node.data
      ? Object.entries(node.data)
          .filter(([key]) => !TRANSIENT_KEYS.has(key))
          .map(([, value]) => {
            if (typeof value === "string") return value
            if (Array.isArray(value)) {
              return value
                .map((item) => {
                  if (typeof item === "string") return item
                  if (item && typeof item === "object") {
                    return Object.values(item).filter((v) => typeof v === "string").join(" ")
                  }
                  return ""
                })
                .join(" ")
            }
            return ""
          })
          .join(" ")
          .toLowerCase()
      : ""

    return typeLabel.includes(q) || nodeId.includes(q) || dataStr.includes(q)
  })
}

/**
 * Highlight the nodes a search matches and dim the rest; with no active
 * search, remove any highlight left on the nodes.
 */
export function highlightSearch(
  nodes: Node[],
  search: { active: boolean; matchIds: ReadonlySet<string>; currentId: string | null },
): Node[] {
  if (search.active) {
    return nodes.map((node) => {
      const isMatch = search.matchIds.has(node.id)
      const isCurrent = node.id === search.currentId
      if (isCurrent) {
        return {
          ...node,
          className: "!opacity-100 [&>div]:ring-2 [&>div]:ring-primary [&>div]:ring-offset-1",
          style: { ...node.style, opacity: 1 },
        }
      }
      if (isMatch) {
        return {
          ...node,
          className: "!opacity-100 [&>div]:ring-2 [&>div]:ring-primary/40",
          style: { ...node.style, opacity: 1 },
        }
      }
      // Non-matching: dim
      return {
        ...node,
        className: "",
        style: { ...node.style, opacity: 0.25 },
      }
    })
  }
  // Clear any leftover search styles
  return nodes.map((node) => {
    if (node.className || (node.style && node.style.opacity != null)) {
      const style = { ...node.style }
      delete style.opacity
      return { ...node, className: undefined, style: Object.keys(style).length > 0 ? style : undefined }
    }
    return node
  })
}

// ---------------------------------------------------------------------------
// Run results on the canvas
// ---------------------------------------------------------------------------

function preview(v: unknown): string | null {
  if (v == null) return null
  const s = typeof v === "string" ? v : JSON.stringify(v)
  return s.length > 120 ? s.slice(0, 120) + "..." : s
}

/** What a node shows about one run: duration badge and tooltip previews. */
export function runOverlayOf(result: NodeRunResult): NodeRunOverlayData {
  return {
    durationMs: result.duration_ms ?? null,
    inputPreview: preview(result.input_preview),
    outputPreview: preview(result.output),
    runError: result.error ?? null,
  }
}

/** Stamp the results of a past run onto the nodes (the replay overlay). */
export function withRunOverlay(nodes: Node[], results: Record<string, NodeRunResult>): Node[] {
  return nodes.map((node) => {
    const result = results[node.id]
    if (!result) return node
    return {
      ...node,
      data: {
        ...node.data,
        runStatus: result.status,
        _runOverlay: runOverlayOf(result),
      },
    }
  })
}

/** Remove what {@link withRunOverlay} added. */
export function withoutRunOverlay(nodes: Node[]): Node[] {
  return nodes.map((node) => {
    if (!node.data.runStatus && !node.data._runOverlay) return node
    const data = { ...node.data } as Record<string, unknown>
    delete data.runStatus
    delete data._runOverlay
    return { ...node, data }
  })
}

/**
 * Nodes as displayed during a live run: status and overlay merged in.
 * A node whose status, duration and error did not change keeps its identity.
 */
export function withLiveRunStatus(nodes: Node[], results: Record<string, NodeRunResult>): Node[] {
  return nodes.map((node) => {
    const runResult = results[node.id]
    const newStatus = runResult?.status
    if (!newStatus) return node

    // Build overlay data for duration badge & tooltip
    const _runOverlay = runOverlayOf(runResult)

    // Skip update if status and overlay haven't changed
    const prev = node.data._runOverlay as NodeRunOverlayData | undefined
    if (
      node.data.runStatus === newStatus &&
      prev?.durationMs === _runOverlay.durationMs &&
      prev?.runError === _runOverlay.runError
    ) {
      return node
    }

    return { ...node, data: { ...node.data, runStatus: newStatus, _runOverlay } }
  })
}

/** Animate an edge while its source is done and its target is running. */
export function withRunAnimation(edges: Edge[], results: Record<string, NodeRunResult>): Edge[] {
  return edges.map((edge) => {
    const sourceStatus = results[edge.source]?.status
    const targetStatus = results[edge.target]?.status
    const shouldAnimate =
      sourceStatus === "completed" && (targetStatus === "running" || targetStatus === "retrying")
    if (!shouldAnimate && !edge.animated) return edge
    return { ...edge, animated: shouldAnimate }
  })
}

// ---------------------------------------------------------------------------
// Validation badges
// ---------------------------------------------------------------------------

/** Warnings and field errors of a validation result, grouped by node. */
export function validationByNode(result: ValidationResult): Record<string, NodeValidationState> {
  const overlay: Record<string, NodeValidationState> = {}

  // The error field is a global error (not node-specific), shown in toolbar
  // The warnings array has node_id that maps to specific nodes
  for (const w of result.warnings) {
    if (!w.node_id) continue
    if (!overlay[w.node_id]) {
      overlay[w.node_id] = { errors: [], warnings: [] }
    }
    overlay[w.node_id].warnings.push(w.message)
  }

  // Invalid settings name their step: mark it with the field and the reason.
  for (const e of result.field_errors ?? []) {
    if (!e.node_id) continue
    if (!overlay[e.node_id]) {
      overlay[e.node_id] = { errors: [], warnings: [] }
    }
    overlay[e.node_id].errors.push(`${fieldOfLoc(e.loc)}: ${e.message}`)
  }

  return overlay
}

/** Set each node's validation badge from `overlay`; nodes without issues lose theirs. */
export function withValidationState(
  nodes: Node[],
  overlay: Record<string, NodeValidationState>,
): Node[] {
  return nodes.map((node) => {
    const state = overlay[node.id]
    if (state) {
      return { ...node, data: { ...node.data, _validationState: state } }
    }
    // Clear any previous validation state for nodes with no issues
    if (node.data._validationState) {
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const { _validationState, ...rest } = node.data as Record<string, unknown>
      return { ...node, data: rest }
    }
    return node
  })
}

/** Remove every validation badge. */
export function withoutValidationState(nodes: Node[]): Node[] {
  return withValidationState(nodes, {})
}

/** Node types present on the canvas (the palette dims Start / End once placed). */
export function nodeTypesOnCanvas(nodes: Node[]): Set<string> {
  const types = new Set<string>()
  for (const n of nodes) {
    if (n.type) types.add(n.type)
  }
  return types
}
