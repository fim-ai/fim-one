import { useMemo } from "react"
import dagre from "dagre"
import type { Edge } from "@xyflow/react"
import { MarkerType } from "@xyflow/react"
import type { DagPhaseEvent } from "@/types/api"
import type { StepState } from "@/hooks/use-dag-steps"
import type { StepFlowNode, StepNodeData } from "./types"

const NODE_WIDTH = 200
const NODE_HEIGHT = 80

interface UseDagLayoutArgs {
  planSteps: DagPhaseEvent["steps"]
  stepStates: StepState[]
}

export function useDagLayout({
  planSteps,
  stepStates,
}: UseDagLayoutArgs): { nodes: StepFlowNode[]; edges: Edge[] } {
  return useMemo(() => {
    if (!planSteps || planSteps.length === 0) {
      return { nodes: [], edges: [] }
    }

    const stateMap = new Map<string, StepState>()
    for (const s of stepStates) {
      stateMap.set(s.step_id, s)
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
          style: { stroke: "rgba(113, 113, 122, 0.4)", strokeWidth: 1.5 },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: "rgba(113, 113, 122, 0.4)",
            width: 16,
            height: 16,
          },
        })
      }
    }

    dagre.layout(g)

    const nodes: StepFlowNode[] = planSteps.map((step) => {
      const nodeWithPosition = g.node(step.id)
      const state = stateMap.get(step.id)

      const nodeData: StepNodeData = {
        step_id: step.id,
        task: step.task,
        status: (state?.status as StepNodeData["status"]) ?? "pending",
        tool_hint: step.tool_hint,
        duration: state?.duration,
        state: state ?? {
          step_id: step.id,
          task: step.task,
          status: "pending",
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

    return { nodes, edges: edgeList }
  }, [planSteps, stepStates])
}
