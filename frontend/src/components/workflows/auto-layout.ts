import ELK from "elkjs/lib/elk.bundled.js"
import type { Node, Edge } from "@xyflow/react"

const elk = new ELK()

/** Default node dimensions (matches base-workflow-node.tsx w-[220px]) */
const DEFAULT_NODE_WIDTH = 220
const DEFAULT_NODE_HEIGHT = 70

/**
 * Auto-layout nodes using ELK.js layered algorithm (left-to-right).
 *
 * Takes React Flow nodes and edges, runs ELK layout, and returns
 * nodes with updated positions. Edges are returned unchanged.
 */
export async function getAutoLayoutedNodes(
  nodes: Node[],
  edges: Edge[],
): Promise<Node[]> {
  const elkGraph = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.spacing.nodeNode": "60",
      "elk.layered.spacing.nodeNodeBetweenLayers": "120",
    },
    children: nodes.map((node) => ({
      id: node.id,
      width: node.measured?.width ?? DEFAULT_NODE_WIDTH,
      height: node.measured?.height ?? DEFAULT_NODE_HEIGHT,
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      sources: [edge.source],
      targets: [edge.target],
    })),
  }

  const layoutedGraph = await elk.layout(elkGraph)

  const layoutedNodes = nodes.map((node) => {
    const elkNode = layoutedGraph.children?.find((n) => n.id === node.id)
    if (!elkNode) return node

    return {
      ...node,
      position: {
        x: elkNode.x ?? 0,
        y: elkNode.y ?? 0,
      },
    }
  })

  return layoutedNodes
}
