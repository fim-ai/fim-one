import { useMemo } from "react"
import type { SSEMessage } from "@/hooks/use-sse"
import type { ReactStepEvent, ReactDoneEvent } from "@/types/api"

export interface StepItem {
  event: string
  data: unknown
  duration?: number
  displayIteration?: number
  timestamp?: number
}

/** Normalize V1/V2 legacy event formats to V3 (type + status). */
function normalizeStep(step: ReactStepEvent): ReactStepEvent {
  if (step.status) return step
  switch (step.type) {
    case "thinking":
      return { ...step, status: "start" }
    case "tool_start":
    case "start":
      return { ...step, type: "iteration", status: "start" }
    case "tool_call":
    case "done":
      return { ...step, type: "iteration", status: "done" }
    default:
      return step
  }
}

export function useReactSteps(messages: SSEMessage[], isRunning: boolean): StepItem[] {
  return useMemo(() => {
    const result: StepItem[] = []
    let iterCount = 0

    for (const msg of messages) {
      // Normalize step events for backward compat with stored sse_events
      const data = msg.event === "step"
        ? normalizeStep(msg.data as ReactStepEvent)
        : msg.data

      if (msg.event === "step") {
        const step = data as ReactStepEvent

        // Merge "done" into its matching "start" by (type, iteration, tool_name)
        if (step.status === "done") {
          const matchIdx = result.findIndex(item => {
            if (item.event !== "step") return false
            const d = item.data as ReactStepEvent
            return d.type === step.type
              && d.status === "start"
              && d.iteration === step.iteration
              && (step.type !== "iteration" || d.tool_name === step.tool_name)
          })
          if (matchIdx !== -1) {
            const clientDuration = (msg.timestamp - (result[matchIdx].timestamp ?? msg.timestamp)) / 1000
            result[matchIdx] = {
              event: msg.event,
              data: step,
              duration: step.iter_elapsed ?? clientDuration,
              displayIteration: result[matchIdx].displayIteration,
              timestamp: msg.timestamp,
            }
            continue
          }
        }

        // Increment logical iteration counter on each thinking start
        if (step.type === "thinking" && step.status === "start") {
          iterCount++
        }
      }

      // Assign displayIteration for thinking/iteration events (not answer)
      let displayIteration: number | undefined
      if (msg.event === "step") {
        const step = data as ReactStepEvent
        if (step.type !== "answer") {
          displayIteration = iterCount || undefined
        }
      }

      let duration: number | undefined
      if (msg.event === "done") {
        duration = (msg.data as ReactDoneEvent).iter_elapsed
      }

      result.push({ event: msg.event, data, duration, displayIteration, timestamp: msg.timestamp })
    }

    const hasDone = result.some(i => i.event === "done")

    // When aborted: convert remaining starts to done, drop transient items
    if (!isRunning && !hasDone && result.length > 0) {
      return result
        .filter(item => {
          if (item.event !== "step") return true
          const step = item.data as ReactStepEvent
          if (step.type === "thinking" && step.status === "start") return false
          if (step.type === "answer") return false
          return true
        })
        .map(item => {
          if (item.event === "step") {
            const step = item.data as ReactStepEvent
            if (step.status === "start") {
              return { ...item, data: { ...step, status: "done" as const } }
            }
          }
          return item
        })
    }

    // After completion: drop transient items but keep thinking-done (has reasoning)
    if (hasDone) {
      return result.filter(item => {
        if (item.event !== "step") return true
        const step = item.data as ReactStepEvent
        if (step.type === "thinking" && step.status === "start") return false
        if (step.type === "answer") return false
        return true
      })
    }

    return result
  }, [messages, isRunning])
}
