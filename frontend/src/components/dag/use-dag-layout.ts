import { useMemo } from "react"
import dagre from "dagre"
import type { Edge } from "@xyflow/react"
import { MarkerType } from "@xyflow/react"
import type { DagPhaseEvent } from "@/types/api"
import type { StepFlowNode, StepNodeData } from "./types"

const NODE_WIDTH = 200
const NODE_HEIGHT = 80

interface UseDagLayoutResult {
  nodes: StepFlowNode[]
  edges: Edge[]
  dagreCenters: Map<string, number>
}

/**
 * Compute Dagre layout from plan topology only.
 *
 * Nodes are initialised with "pending" status — live status/duration/tools
 * are merged by the component's useEffect so that position-stable updates
 * don't trigger a full layout recalculation.
 */
export function useDagLayout(
  planSteps: DagPhaseEvent["steps"],
): UseDagLayoutResult {
  return useMemo(() => {
    if (!planSteps || planSteps.length === 0) {
      return { nodes: [], edges: [], dagreCenters: new Map() }
    }

    const g = new dagre.graphlib.Graph()
    g.setDefaultEdgeLabel(() => ({}))
    g.setGraph({ rankdir: "LR", nodesep: 60, ranksep: 70 })

    for (const step of planSteps) {
      g.setNode(step.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
    }

    const edgeList: Edge[] = []

    for (const step of planSteps) {
      for (const dep of step.deps) {
        const edgeId = `${dep}->${step.id}`
        g.setEdge(dep, step.id)
        edgeList.push({
          id: edgeId,
          source: dep,
          target: step.id,
          type: "smoothstep",
          style: { stroke: "var(--border)", strokeWidth: 1.5 },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: "var(--border)",
            width: 16,
            height: 16,
          },
        })
      }
    }

    dagre.layout(g)

    const dagreCenters = new Map<string, number>()

    const nodes: StepFlowNode[] = planSteps.map((step) => {
      const nodeWithPosition = g.node(step.id)
      dagreCenters.set(step.id, nodeWithPosition.y)

      const nodeData: StepNodeData = {
        step_id: step.id,
        task: step.task,
        status: "pending",
        tool_hint: step.tool_hint,
        duration: undefined,
        started_at: undefined,
        tools_used: undefined,
        state: {
          step_id: step.id,
          task: step.task,
          status: "pending",
          tools_used: [],
          iterations: [],
        },
      }

      return {
        id: step.id,
        type: "step" as const,
        position: {
          x: nodeWithPosition.x - NODE_WIDTH / 2,
          y: nodeWithPosition.y - NODE_HEIGHT / 2,
        },
        data: nodeData,
      }
    })

    return { nodes, edges: edgeList, dagreCenters }
  }, [planSteps])
}
