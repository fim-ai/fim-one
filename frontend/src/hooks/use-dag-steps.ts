import { useMemo } from "react"
import type { SSEMessage } from "@/hooks/use-sse"
import type {
  DagPhaseEvent,
  DagStepProgressEvent,
  DagDoneEvent,
} from "@/types/api"

export interface StepState {
  step_id: string
  task?: string
  status: "pending" | "running" | "completed"
  result?: string
  duration?: number
  started_at?: number
  tools_used: string[]
  iterations: Array<{
    type?: string
    iteration?: number
    tool_name?: string
    tool_args?: Record<string, unknown>
    reasoning?: string
    observation?: string
    error?: string
    loading?: boolean
  }>
}

export interface DagStepsResult {
  planSteps: DagPhaseEvent["steps"]
  stepStates: StepState[]
  analysisPhase: DagPhaseEvent | null
  doneEvent: DagDoneEvent | null
  currentPhase: string | null
}

export function useDagSteps(messages: SSEMessage[], isRunning: boolean): DagStepsResult {
  return useMemo(() => {
    let planSteps: DagPhaseEvent["steps"] = undefined
    const stepMap = new Map<string, StepState>()
    let analysisPhase: DagPhaseEvent | null = null
    let doneEvent: DagDoneEvent | null = null
    let currentPhase: string | null = null

    for (const msg of messages) {
      if (msg.event === "phase") {
        const phase = msg.data as DagPhaseEvent
        if (phase.name === "planning" && phase.status === "done" && phase.steps) {
          planSteps = phase.steps
          for (const s of phase.steps) {
            stepMap.set(s.id, {
              step_id: s.id,
              task: s.task,
              status: "pending",
              tools_used: [],
              iterations: [],
            })
          }
        }
        if (phase.name === "executing") {
          currentPhase = "executing"
        }
        if (phase.name === "analyzing") {
          currentPhase = "analyzing"
          if (phase.status === "done") {
            analysisPhase = phase
          }
        }
        if (phase.name === "planning" && phase.status === "start") {
          currentPhase = "planning"
        }
      }

      if (msg.event === "step_progress") {
        const sp = msg.data as DagStepProgressEvent
        const existing = stepMap.get(sp.step_id)
        if (!existing) {
          stepMap.set(sp.step_id, {
            step_id: sp.step_id,
            task: sp.task,
            status: "pending",
            tools_used: [],
            iterations: [],
          })
        }
        const state = stepMap.get(sp.step_id)!

        if (sp.task) state.task = sp.task

        if (sp.event === "started") {
          state.status = "running"
          if (sp.started_at != null) state.started_at = sp.started_at
        } else if (sp.event === "completed") {
          state.status = "completed"
          if (sp.result) state.result = sp.result
          if (sp.duration) state.duration = sp.duration
          if (sp.started_at != null) state.started_at = sp.started_at
        } else if (sp.event === "iteration") {
          if (sp.tool_name && !state.tools_used.includes(sp.tool_name)) {
            state.tools_used.push(sp.tool_name)
          }
          const isStart = sp.type === "tool_call" && sp.observation == null && sp.error == null
          if (isStart) {
            state.iterations.push({
              type: sp.type,
              iteration: sp.iteration,
              tool_name: sp.tool_name,
              tool_args: sp.tool_args,
              reasoning: sp.reasoning,
              observation: undefined,
              error: undefined,
              loading: true,
            })
          } else {
            const matchIdx = state.iterations.findIndex(iter =>
              iter.loading
              && iter.tool_name === sp.tool_name
              && iter.iteration === sp.iteration
            )
            if (matchIdx !== -1) {
              state.iterations[matchIdx] = {
                type: sp.type,
                iteration: sp.iteration,
                tool_name: sp.tool_name,
                tool_args: sp.tool_args,
                reasoning: sp.reasoning ?? state.iterations[matchIdx].reasoning,
                observation: sp.observation,
                error: sp.error,
                loading: false,
              }
            } else {
              state.iterations.push({
                type: sp.type,
                iteration: sp.iteration,
                tool_name: sp.tool_name,
                tool_args: sp.tool_args,
                reasoning: sp.reasoning,
                observation: sp.observation,
                error: sp.error,
                loading: false,
              })
            }
          }
        }
      }

      if (msg.event === "done") {
        doneEvent = msg.data as DagDoneEvent
      }
    }

    // When aborted (not running, no done event), clean up all loading states
    // so spinners and "Executing..." indicators stop immediately.
    if (!isRunning && !doneEvent && stepMap.size > 0) {
      for (const state of stepMap.values()) {
        if (state.status === "running") state.status = "pending"
        for (const iter of state.iterations) {
          iter.loading = false
        }
      }
      currentPhase = null
    }

    return {
      planSteps,
      stepStates: Array.from(stepMap.values()),
      analysisPhase,
      doneEvent,
      currentPhase,
    }
  }, [messages, isRunning])
}
