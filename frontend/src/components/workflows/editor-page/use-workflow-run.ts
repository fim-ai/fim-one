import { useCallback, useRef, useState } from "react"
import { useTranslations } from "next-intl"

import { ApiError, workflowApi } from "@/lib/api"
import { ACCESS_TOKEN_KEY, getApiBaseUrl } from "@/lib/constants"
import { getErrorMessage } from "@/lib/error-utils"
import type { NodeRunResult, WorkflowLogEvent } from "@/types/workflow"
import { createSseParser, logEventOf, nodeResultOfEvent, nodeStatusOfEvent } from "./run-stream"

/**
 * One interactive run of the workflow: the run panel being open, the event
 * stream, and what it has reported so far (per-node results, final outputs
 * or error, duration, the execution log).
 */
export function useWorkflowRun(workflowId: string | null) {
  const tError = useTranslations("errors")

  const [isRunning, setIsRunning] = useState(false)
  const [runPanelOpen, setRunPanelOpen] = useState(false)
  const [nodeResults, setNodeResults] = useState<Record<string, NodeRunResult> | null>(null)
  const [finalOutputs, setFinalOutputs] = useState<Record<string, unknown> | null>(null)
  const [finalError, setFinalError] = useState<string | null>(null)
  const [runDuration, setRunDuration] = useState<number | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const [logEvents, setLogEvents] = useState<WorkflowLogEvent[]>([])

  /** Open the run panel on its input form. */
  const openRunPanel = useCallback(() => {
    setRunPanelOpen(true)
    setNodeResults(null)
    setFinalOutputs(null)
    setFinalError(null)
    setRunDuration(null)
  }, [])

  const closeRunPanel = useCallback(() => setRunPanelOpen(false), [])

  /** Back to the input form after a run. */
  const runAgain = useCallback(() => {
    setNodeResults(null)
    setFinalOutputs(null)
    setFinalError(null)
    setRunDuration(null)
    setLogEvents([])
  }, [])

  const startRun = useCallback(
    async (inputs: Record<string, unknown>) => {
      if (!workflowId) return

      // Abort any existing run
      if (abortRef.current) {
        abortRef.current.abort()
      }

      const controller = new AbortController()
      abortRef.current = controller
      setIsRunning(true)
      setNodeResults({})
      setFinalOutputs(null)
      setFinalError(null)
      setRunDuration(null)
      setLogEvents([])

      try {
        const token = typeof window !== "undefined" ? localStorage.getItem(ACCESS_TOKEN_KEY) : null
        const headers: Record<string, string> = {
          "Content-Type": "application/json",
        }
        if (token) headers["Authorization"] = `Bearer ${token}`

        const res = await fetch(
          `${getApiBaseUrl()}/api/workflows/${workflowId}/run`,
          {
            method: "POST",
            headers,
            body: JSON.stringify({ inputs }),
            signal: controller.signal,
          },
        )

        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          throw new ApiError(
            res.status,
            body.detail || `HTTP ${res.status}`,
            body.error_code ?? null,
            body.error_args ?? {},
          )
        }

        if (!res.body) throw new Error("No response body")

        const reader = res.body.getReader()
        const decoder = new TextDecoder()

        const dispatch = (eventType: string, rawData: string) => {
          try {
            const data = JSON.parse(rawData) as Record<string, unknown>

            // Push raw event to execution log
            const logEvent = logEventOf(eventType, data, Date.now())
            if (logEvent) {
              setLogEvents((prev) => [...prev, logEvent])
            }

            const status = nodeStatusOfEvent(eventType)
            if (status) {
              const nodeId = data.node_id as string
              setNodeResults((prev) => ({
                ...(prev ?? {}),
                [nodeId]: nodeResultOfEvent(status, data, prev?.[nodeId]),
              }))
            } else if (eventType === "run_completed") {
              setFinalOutputs((data.outputs ?? null) as Record<string, unknown> | null)
              setRunDuration((data.duration_ms ?? null) as number | null)
              setIsRunning(false)
            } else if (eventType === "run_failed") {
              setFinalError((data.error ?? "Unknown error") as string)
              setRunDuration((data.duration_ms ?? null) as number | null)
              setIsRunning(false)
            } else if (eventType === "end") {
              setIsRunning(false)
            }
          } catch {
            // Ignore unparseable
          }
        }

        const feed = createSseParser(dispatch)
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          feed(decoder.decode(value, { stream: true }))
        }
      } catch (err) {
        if ((err as { name?: string })?.name === "AbortError") return
        // A run the server refused to start is explained in the user's
        // language where the refusal has a message.
        setFinalError(
          err instanceof ApiError
            ? getErrorMessage(err, tError)
            : err instanceof Error ? err.message : "Stream error",
        )
        setIsRunning(false)
      }
    },
    [workflowId, tError],
  )

  const cancelRun = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setIsRunning(false)
    if (workflowId) {
      workflowApi.cancelRun(workflowId, "current").catch(() => {})
    }
  }, [workflowId])

  return {
    isRunning,
    runPanelOpen,
    nodeResults,
    finalOutputs,
    finalError,
    runDuration,
    logEvents,
    openRunPanel,
    closeRunPanel,
    runAgain,
    startRun,
    cancelRun,
  }
}
