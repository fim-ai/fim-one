import type { Node, Edge } from "@xyflow/react"
import { autoLayoutWorkflow } from "@/lib/workflow-layout"

/**
 * Auto-layout nodes using a simple layered/hierarchical BFS algorithm.
 *
 * Takes React Flow nodes and edges, runs the layout, and returns
 * nodes with updated positions. Edges are returned unchanged.
 *
 * This is a thin async wrapper around the pure `autoLayoutWorkflow` function
 * to maintain backward compatibility with the editor's existing async call site.
 */
export async function getAutoLayoutedNodes(
  nodes: Node[],
  edges: Edge[],
): Promise<Node[]> {
  return autoLayoutWorkflow(nodes, edges)
}
