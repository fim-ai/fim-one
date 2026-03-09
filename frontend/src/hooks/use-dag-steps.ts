import { useMemo } from "react"
import type { SSEMessage } from "@/hooks/use-sse"
import type {
  DagPhaseEvent,
  DagStepProgressEvent,
  DagDoneEvent,
  AnswerEvent,
} from "@/types/api"

export interface StepState {
  step_id: string
  task?: string
  status: "pending" | "running" | "completed" | "skipped"
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
    timestamp?: number
    duration?: number
    content_type?: string
    artifacts?: Array<{ name: string; url: string; mime_type: string; size: number }>
  }>
}

export interface RoundSnapshot {
  round: number
  stepStates: StepState[]
  analysisPhase: DagPhaseEvent | null
  planSteps: DagPhaseEvent["steps"]
}

export interface DagStepsResult {
  planSteps: DagPhaseEvent["steps"]
  stepStates: StepState[]
  analysisPhase: DagPhaseEvent | null
  doneEvent: DagDoneEvent | null
  currentPhase: string | null
  currentRound: number
  previousRounds: RoundSnapshot[]
  injectEvents: Array<{ content: string; phase?: string; timestamp: number }>
  /** Accumulated answer text from streaming answer events. */
  streamingAnswer: string
  /** True when all answer chunks have been received. */
  answerDone: boolean
  /** Suggested follow-up questions (from async `suggestions` event or done payload). */
  suggestions: string[]
  /** Auto-generated conversation title (from async `title` event or done payload). */
  title: string | null
}

export function useDagSteps(messages: SSEMessage[], isRunning: boolean): DagStepsResult {
  return useMemo(() => {
    let planSteps: DagPhaseEvent["steps"] = undefined
    const stepMap = new Map<string, StepState>()
    let analysisPhase: DagPhaseEvent | null = null
    let doneEvent: DagDoneEvent | null = null
    let currentPhase: string | null = null
    let currentRound = 1
    const previousRounds: RoundSnapshot[] = []
    const injectEvents: Array<{ content: string; phase?: string; timestamp: number }> = []
    let streamingAnswer = ""
    let answerDone = false
    let suggestions: string[] = []
    let title: string | null = null

    for (const msg of messages) {
      // Handle answer events (streamed before done)
      if (msg.event === "answer") {
        const ev = msg.data as AnswerEvent
        if (ev.status === "start") {
          streamingAnswer = ""
          answerDone = false
        } else if (ev.status === "delta" && ev.content) {
          streamingAnswer += ev.content
        } else if (ev.status === "done") {
          answerDone = true
        }
        continue
      }
      // Handle suggestions event (new async flow)
      if (msg.event === "suggestions") {
        suggestions = (msg.data as { items: string[] }).items
        continue
      }
      // Handle title event (new async flow)
      if (msg.event === "title") {
        title = (msg.data as { title: string }).title
        continue
      }
      // Skip end event — it's a stream terminator, not a data event
      if (msg.event === "end") {
        continue
      }
      if (msg.event === "phase") {
        const phase = msg.data as DagPhaseEvent

        // Track round number from any phase event that includes it
        if (phase.round != null) {
          currentRound = phase.round
        }

        if (phase.name === "planning" && phase.status === "done" && phase.steps) {
          // On re-plan (round > 1), snapshot current round then clear for new round
          if (currentRound > 1) {
            previousRounds.push({
              round: currentRound - 1,
              stepStates: Array.from(stepMap.values()),
              analysisPhase,
              planSteps,
            })
            stepMap.clear()
            analysisPhase = null
          }
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
        if (phase.name === "replanning") {
          currentPhase = "replanning"
          // Keep analysisPhase alive so the snapshot captures it —
          // it gets reset to null after snapshotting in the planning-done handler.
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
          state.status = sp.status === "skipped" ? "skipped" : "completed"
          if (sp.result) state.result = sp.result
          if (sp.duration) state.duration = sp.duration
          if (sp.started_at != null) state.started_at = sp.started_at
        } else if (sp.event === "iteration") {
          // Backend now skips thinking shims and __selecting_tools__ in executor,
          // but guard here too for backward compat with older event streams.
          if (sp.type === "thinking" || sp.tool_name === "__selecting_tools__") continue

          if (sp.tool_name && !state.tools_used.includes(sp.tool_name)) {
            state.tools_used.push(sp.tool_name)
          }
          const isStart = sp.status === "start"
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
              timestamp: msg.timestamp,
              content_type: sp.content_type,
              artifacts: sp.artifacts,
            })
          } else {
            const matchIdx = state.iterations.findIndex(iter =>
              iter.loading
              && iter.tool_name === sp.tool_name
              && iter.iteration === sp.iteration
            )
            if (matchIdx !== -1) {
              const startTs = state.iterations[matchIdx].timestamp
              const clientDuration = startTs != null ? (msg.timestamp - startTs) / 1000 : undefined
              state.iterations[matchIdx] = {
                type: sp.type,
                iteration: sp.iteration,
                tool_name: sp.tool_name,
                tool_args: sp.tool_args,
                reasoning: sp.reasoning ?? state.iterations[matchIdx].reasoning,
                observation: sp.observation,
                error: sp.error,
                loading: false,
                duration: sp.iter_elapsed ?? clientDuration,
                content_type: sp.content_type ?? state.iterations[matchIdx].content_type,
                artifacts: sp.artifacts ?? state.iterations[matchIdx].artifacts,
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
                content_type: sp.content_type,
                artifacts: sp.artifacts,
              })
            }
          }
        }
      }

      if (msg.event === "done") {
        doneEvent = msg.data as DagDoneEvent
        // Backward compat: read from done payload if separate events didn't arrive
        if (!suggestions.length && doneEvent.suggestions?.length) {
          suggestions = doneEvent.suggestions
        }
        if (title === null && doneEvent.title) {
          title = doneEvent.title
        }
      }

      if (msg.event === "inject") {
        const data = msg.data as { content: string; phase?: string }
        injectEvents.push({ ...data, timestamp: msg.timestamp })
      }
    }

    // When aborted (not running, no done event), clean up all loading states
    // so spinners and "Executing..." / "Planning..." indicators stop immediately.
    if (!isRunning && !doneEvent) {
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
      currentRound,
      previousRounds,
      injectEvents,
      streamingAnswer,
      answerDone,
      suggestions,
      title,
    }
  }, [messages, isRunning])
}
