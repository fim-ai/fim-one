import type { StepState } from "@/hooks/use-dag-steps"

export interface StepNodeData {
  step_id: string
  task: string
  status: "pending" | "running" | "completed" | "failed"
  tool_hint?: string
  duration?: number
  state: StepState
  [key: string]: unknown // ReactFlow requires index signature
}

export type StepFlowNode = {
  id: string
  type: "step"
  position: { x: number; y: number }
  data: StepNodeData
}
