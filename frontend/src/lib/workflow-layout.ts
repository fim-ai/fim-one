import type { Node, Edge } from "@xyflow/react"

/** Horizontal distance between layers (left-to-right) */
const HORIZONTAL_SPACING = 300

/** Vertical distance between nodes within the same layer */
const VERTICAL_SPACING = 150

/** Assumed node width for centering calculations */
const NODE_WIDTH = 240

/** Assumed node height for centering calculations */
const NODE_HEIGHT = 80

/**
 * Auto-layout workflow nodes using a simple layered/hierarchical (BFS) algorithm.
 *
 * 1. Find the Start node (type === "start") as layer 0.
 * 2. BFS from Start through edges to assign each node a depth (layer).
 * 3. Within each layer, sort nodes alphabetically by id for determinism.
 * 4. Position: x = layer * HORIZONTAL_SPACING, y = indexInLayer * VERTICAL_SPACING.
 * 5. Center each layer vertically relative to the tallest layer.
 * 6. Disconnected nodes (unreachable from Start) are placed in additional layers at the end.
 *
 * Returns a new array of nodes with updated positions. Original nodes are not mutated.
 */
export function autoLayoutWorkflow(
  nodes: Node[],
  edges: Edge[],
): Node[] {
  if (nodes.length === 0) return []

  // Build adjacency list (source -> targets)
  const adjacency = new Map<string, string[]>()
  for (const edge of edges) {
    const targets = adjacency.get(edge.source)
    if (targets) {
      targets.push(edge.target)
    } else {
      adjacency.set(edge.source, [edge.target])
    }
  }

  // BFS to assign layers (depth)
  const layerMap = new Map<string, number>() // nodeId -> layer index
  const startNode = nodes.find((n) => n.type === "start")

  if (startNode) {
    // BFS from start
    const queue: Array<{ id: string; depth: number }> = [{ id: startNode.id, depth: 0 }]
    layerMap.set(startNode.id, 0)

    while (queue.length > 0) {
      const { id, depth } = queue.shift()!
      const neighbors = adjacency.get(id) ?? []
      for (const neighborId of neighbors) {
        // Only assign if not yet visited, or if this path gives a deeper layer
        // (use max depth to ensure proper layering for convergent paths)
        const existingDepth = layerMap.get(neighborId)
        const newDepth = depth + 1
        if (existingDepth === undefined || newDepth > existingDepth) {
          layerMap.set(neighborId, newDepth)
          queue.push({ id: neighborId, depth: newDepth })
        }
      }
    }
  }

  // Collect disconnected nodes (not reached by BFS)
  const disconnectedNodes: Node[] = []
  for (const node of nodes) {
    if (!layerMap.has(node.id)) {
      disconnectedNodes.push(node)
    }
  }

  // Assign disconnected nodes to layers after the last BFS layer
  const maxBfsLayer = layerMap.size > 0
    ? Math.max(...layerMap.values())
    : -1

  if (disconnectedNodes.length > 0) {
    // Sort disconnected nodes alphabetically for determinism
    disconnectedNodes.sort((a, b) => a.id.localeCompare(b.id))
    const disconnectedStartLayer = maxBfsLayer + 2 // leave a gap
    for (let i = 0; i < disconnectedNodes.length; i++) {
      layerMap.set(disconnectedNodes[i].id, disconnectedStartLayer + i)
    }
  }

  // Group nodes by layer
  const layers = new Map<number, Node[]>()
  for (const node of nodes) {
    const layer = layerMap.get(node.id) ?? 0
    const group = layers.get(layer)
    if (group) {
      group.push(node)
    } else {
      layers.set(layer, [node])
    }
  }

  // Sort nodes within each layer alphabetically by id for determinism
  for (const group of layers.values()) {
    group.sort((a, b) => a.id.localeCompare(b.id))
  }

  // Find the tallest layer (most nodes) for vertical centering
  let maxLayerSize = 0
  for (const group of layers.values()) {
    if (group.length > maxLayerSize) {
      maxLayerSize = group.length
    }
  }
  const maxLayerHeight = maxLayerSize * VERTICAL_SPACING

  // Build position map: nodeId -> { x, y }
  const positionMap = new Map<string, { x: number; y: number }>()

  for (const [layerIndex, group] of layers) {
    const layerHeight = group.length * VERTICAL_SPACING
    // Center this layer vertically relative to the tallest layer
    const yOffset = (maxLayerHeight - layerHeight) / 2

    for (let i = 0; i < group.length; i++) {
      const x = layerIndex * HORIZONTAL_SPACING
      const y = yOffset + i * VERTICAL_SPACING
      positionMap.set(group[i].id, { x, y })
    }
  }

  // Return new nodes with updated positions
  return nodes.map((node) => {
    const newPosition = positionMap.get(node.id)
    if (!newPosition) return node
    return {
      ...node,
      position: newPosition,
    }
  })
}
