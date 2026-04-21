import { useMemo } from "react"
import type { SSEMessage } from "@/hooks/use-sse"
import type { ReactStepEvent, ReactDoneEvent, AnswerEvent } from "@/types/api"

export interface StepItem {
  event: string
  data: unknown
  duration?: number
  displayIteration?: number
  timestamp?: number
}

export interface ReactStepsResult {
  items: StepItem[]
  /** Accumulated answer text from streaming answer events. */
  streamingAnswer: string
  /** True when all answer chunks have been received (answer status="done"). */
  answerDone: boolean
  /** Suggested follow-up questions (from async `suggestions` event or done payload). */
  suggestions: string[]
  /** Auto-generated conversation title (from async `title` event or done payload). */
  title: string | null
  /** True when post_processing event has been received but end event has not yet arrived. */
  isPostProcessing: boolean
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

export function useReactSteps(messages: SSEMessage[], isRunning: boolean): ReactStepsResult {
  return useMemo(() => {
    const result: StepItem[] = []
    let streamingAnswer = ""
    let answerDone = false
    let iterCount = 0
    let suggestions: string[] = []
    let title: string | null = null
    let isPostProcessing = false

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
      // Track post-processing phase (between done and end)
      if (msg.event === "post_processing") {
        isPostProcessing = true
        continue
      }
      // Skip end event — it's a stream terminator, not a data event
      if (msg.event === "end") {
        isPostProcessing = false
        continue
      }
      // resume_done is a resume-protocol marker emitted by /api/chat/resume.
      // It carries no step data — just swallow it so downstream rendering
      // isn't disrupted while we're recovering from a disconnect.
      if (msg.event === "resume_done") {
        continue
      }
      // awaiting_confirmation is emitted mid-stream when a tool hook pauses
      // execution pending human approval. Forward it as a first-class item
      // so ReactOutput can render the inline ConfirmationCard. Payload shape
      // is frozen (see Phase 1 Task #3) — do not normalize/reshape.
      if (msg.event === "awaiting_confirmation") {
        result.push({
          event: msg.event,
          data: msg.data,
          timestamp: msg.timestamp,
        })
        continue
      }
      // Normalize step events for backward compat with stored sse_events
      const data = msg.event === "step"
        ? normalizeStep(msg.data as ReactStepEvent)
        : msg.data

      if (msg.event === "step") {
        const step = data as ReactStepEvent

        // Accumulate thinking delta tokens into the existing thinking-start item
        if (step.type === "thinking" && step.status === "delta" && step.content) {
          const thinkingIdx = result.findLastIndex(item => {
            if (item.event !== "step") return false
            const d = item.data as ReactStepEvent
            return d.type === "thinking" && d.status === "start"
          })
          if (thinkingIdx !== -1) {
            const existing = result[thinkingIdx].data as ReactStepEvent
            result[thinkingIdx] = {
              ...result[thinkingIdx],
              data: {
                ...existing,
                reasoning: (existing.reasoning ?? "") + step.content,
              },
            }
          }
          continue
        }

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
            // For thinking-done: preserve accumulated reasoning from deltas if done has none
            let mergedData = step
            if (step.type === "thinking") {
              const accumulated = result[matchIdx].data as ReactStepEvent
              if (!step.reasoning && accumulated.reasoning) {
                mergedData = { ...step, reasoning: accumulated.reasoning }
              }
            }
            result[matchIdx] = {
              event: msg.event,
              data: mergedData,
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
        // Backward compat: read from done payload if separate events didn't arrive
        const doneData = msg.data as ReactDoneEvent
        if (!suggestions.length && doneData.suggestions?.length) {
          suggestions = doneData.suggestions
        }
        if (title === null && doneData.title) {
          title = doneData.title
        }
      }

      result.push({ event: msg.event, data, duration, displayIteration, timestamp: msg.timestamp })
    }

    const hasDone = result.some(i => i.event === "done")

    // When aborted: convert remaining starts to done, drop transient items
    if (!isRunning && !hasDone && result.length > 0) {
      const items = result
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
      return { items, streamingAnswer, answerDone, suggestions, title, isPostProcessing }
    }

    // After completion: drop transient items but keep thinking-done (has reasoning)
    if (hasDone) {
      // Collect displayIterations that contain at least one tool call
      const itersWithToolCalls = new Set<number>()
      for (const item of result) {
        if (item.event !== "step") continue
        const step = item.data as ReactStepEvent
        if (step.type === "iteration" && item.displayIteration != null) {
          itersWithToolCalls.add(item.displayIteration)
        }
      }

      const items = result.filter(item => {
        if (item.event !== "step") return true
        const step = item.data as ReactStepEvent
        if (step.type === "thinking" && step.status === "start") return false
        if (step.type === "answer") return false
        // Drop empty thinking rounds (no reasoning and no tool call in that iteration)
        if (step.type === "thinking" && !step.reasoning
            && item.displayIteration != null
            && !itersWithToolCalls.has(item.displayIteration)) {
          return false
        }
        return true
      })
      return { items, streamingAnswer, answerDone, suggestions, title, isPostProcessing }
    }

    return { items: result, streamingAnswer, answerDone, suggestions, title, isPostProcessing }
  }, [messages, isRunning])
}
