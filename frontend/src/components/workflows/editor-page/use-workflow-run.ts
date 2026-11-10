import { useCallback, useEffect, useRef, useState } from "react"
import { useTranslations } from "next-intl"

import { ApiError, workflowApi } from "@/lib/api"
import { ACCESS_TOKEN_KEY, getApiBaseUrl } from "@/lib/constants"
import { getErrorMessage } from "@/lib/error-utils"
import type {
  NodeRunResult,
  RunWaitingItem,
  WorkflowLogEvent,
  WorkflowRunResponse,
  WorkflowRunStatus,
} from "@/types/workflow"
import { refusedInputOf } from "./run-inputs"
import { mergeNodeResults, panelStateOfRun } from "./run-record"
import {
  createSseParser,
  logEventOf,
  nodeCountOfRunStarted,
  nodeResultOfEvent,
  nodeStatusOfEvent,
  runIdOfRunStarted,
  waitingOfRunWaiting,
} from "./run-stream"

/** How often a run that is followed by its record is read again. */
export const RUN_POLL_INTERVAL_MS = 2000

/**
 * One interactive run of the workflow: the run panel being open, and what
 * the run has reported so far (per-node results, final outputs or error,
 * duration, the execution log).
 *
 * The run is not the stream. The stream reports a run while it executes;
 * when it ends without the run having ended (the run waits for a decision,
 * or the connection dropped), the run is followed by reading its record
 * every {@link RUN_POLL_INTERVAL_MS} until it ends. Cancelling is a request
 * to the server for that run; closing the stream or the page cancels
 * nothing.
 *
 * The server runs the workflow it has stored. `beforeRun` is awaited before
 * each run is requested, to save what is on the canvas first; when it
 * resolves false (the save failed) the run does not start and the panel
 * goes back to its input form. So does a run the server refused for one of
 * its inputs: `inputError` names the input, for the form to show it there.
 */
export function useWorkflowRun(workflowId: string | null, beforeRun?: () => Promise<boolean>) {
  const tError = useTranslations("errors")

  const [isRunning, setIsRunning] = useState(false)
  const [runPanelOpen, setRunPanelOpen] = useState(false)
  const [nodeResults, setNodeResults] = useState<Record<string, NodeRunResult> | null>(null)
  const [finalOutputs, setFinalOutputs] = useState<Record<string, unknown> | null>(null)
  const [finalError, setFinalError] = useState<string | null>(null)
  const [runDuration, setRunDuration] = useState<number | null>(null)
  // Steps the run executes, as the server reported when it started; null
  // until then.
  const [totalNodeCount, setTotalNodeCount] = useState<number | null>(null)
  // The run the panel shows: its id and status, once the server has named it.
  const [runId, setRunId] = useState<string | null>(null)
  const [runStatus, setRunStatus] = useState<WorkflowRunStatus | null>(null)
  const [waitingFor, setWaitingFor] = useState<RunWaitingItem[]>([])
  const [logEvents, setLogEvents] = useState<WorkflowLogEvent[]>([])
  // The input the server refused the last run for, and why.
  const [inputError, setInputError] = useState<{ input: string; message: string } | null>(null)

  const abortRef = useRef<AbortController | null>(null)
  // Counts run attempts and cancellations: an attempt that was cancelled or
  // superseded while `beforeRun` was pending does not go on to run.
  const attemptRef = useRef(0)
  const runIdRef = useRef<string | null>(null)
  // Set by a cancel that came before the server had named the run: the run
  // is cancelled as soon as its id arrives.
  const cancelWhenNamedRef = useRef(false)
  // Counts followings of a run by its record; a newer one ends the older.
  const followRef = useRef(0)
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)
  const beforeRunRef = useRef(beforeRun)
  beforeRunRef.current = beforeRun

  const stopFollowing = useCallback(() => {
    followRef.current += 1
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }, [])

  // Leaving the page stops reading. It does not cancel the run.
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      stopFollowing()
      abortRef.current?.abort()
    }
  }, [stopFollowing])

  const clearRun = useCallback(() => {
    setNodeResults(null)
    setFinalOutputs(null)
    setFinalError(null)
    setRunDuration(null)
    setTotalNodeCount(null)
    setRunStatus(null)
    setWaitingFor([])
    setRunId(null)
    runIdRef.current = null
    setInputError(null)
  }, [])

  const clearInputError = useCallback(() => setInputError(null), [])

  /** Show a run record in the panel. Returns whether the run can still change. */
  const showRecord = useCallback((record: WorkflowRunResponse): boolean => {
    const state = panelStateOfRun(record)
    setRunStatus(state.status)
    setIsRunning(state.status === "running" || state.status === "pending")
    setWaitingFor(state.waitingFor)
    setNodeResults((shown) => mergeNodeResults(shown, state.nodeResults))
    setFinalOutputs(state.finalOutputs)
    setFinalError(state.finalError)
    setRunDuration(state.runDuration)
    return state.isActive
  }, [])

  /** Follow a run by its record until it ends. */
  const followRun = useCallback(
    (id: string) => {
      if (!workflowId) return
      stopFollowing()
      const following = followRef.current
      const current = () => mountedRef.current && followRef.current === following
      const read = async () => {
        let active = true
        try {
          const record = await workflowApi.getRun(workflowId, id)
          if (!current()) return
          active = showRecord(record)
        } catch (err) {
          if (!current()) return
          // The run is gone (deleted elsewhere): nothing left to follow.
          if (err instanceof ApiError && err.status === 404) {
            setIsRunning(false)
            setRunStatus("failed")
            setWaitingFor([])
            setFinalError(getErrorMessage(err, tError))
            return
          }
          // Anything else (network, a restart in progress): read again.
        }
        if (active && current()) pollTimerRef.current = setTimeout(read, RUN_POLL_INTERVAL_MS)
      }
      void read()
    },
    [workflowId, stopFollowing, showRecord, tError],
  )

  /** Read the followed run now (after a decision was sent, for instance). */
  const refreshRun = useCallback(() => {
    if (runIdRef.current) followRun(runIdRef.current)
  }, [followRun])

  /**
   * Open the run panel. On its input form, unless a run is in progress (it
   * runs, or waits for a decision): then the panel shows that run.
   */
  const openRunPanel = useCallback(() => {
    setRunPanelOpen(true)
    if (isRunning || runStatus === "waiting") return
    stopFollowing()
    clearRun()
  }, [isRunning, runStatus, stopFollowing, clearRun])

  const closeRunPanel = useCallback(() => setRunPanelOpen(false), [])

  /** Back to the input form after a run. */
  const runAgain = useCallback(() => {
    stopFollowing()
    clearRun()
    setLogEvents([])
  }, [stopFollowing, clearRun])

  /**
   * Open the panel on a run that is already in progress (started before the
   * page was opened) and follow it.
   */
  const attachToRun = useCallback(
    (record: WorkflowRunResponse) => {
      // A run started from this page since is the one to show.
      if (abortRef.current || runIdRef.current) return
      attemptRef.current += 1
      runIdRef.current = record.id
      setRunId(record.id)
      setTotalNodeCount(null)
      setLogEvents([])
      setNodeResults({})
      setRunPanelOpen(true)
      if (showRecord(record)) followRun(record.id)
    },
    [showRecord, followRun],
  )

  const startRun = useCallback(
    async (inputs: Record<string, unknown>) => {
      if (!workflowId) return

      // Stop reading any earlier run (this does not cancel it).
      if (abortRef.current) {
        abortRef.current.abort()
      }
      stopFollowing()

      const attempt = ++attemptRef.current
      const controller = new AbortController()
      abortRef.current = controller
      cancelWhenNamedRef.current = false
      runIdRef.current = null
      setRunId(null)
      setIsRunning(true)
      setRunStatus("running")
      setInputError(null)
      setWaitingFor([])
      setNodeResults({})
      setFinalOutputs(null)
      setFinalError(null)
      setRunDuration(null)
      setTotalNodeCount(null)
      setLogEvents([])

      const backToForm = () => {
        if (abortRef.current === controller) abortRef.current = null
        setNodeResults(null)
        setRunStatus(null)
        setIsRunning(false)
      }

      if (beforeRunRef.current) {
        let ready = false
        try {
          ready = await beforeRunRef.current()
        } catch {
          ready = false
        }
        // Cancelled, or started again, while that was pending.
        if (attemptRef.current !== attempt) {
          if (abortRef.current === controller) abortRef.current = null
          return
        }
        if (!ready) {
          // Not run. Whoever refused has said why; back to the input form.
          backToForm()
          return
        }
      }

      // A cancel that comes before the run has a name is carried out when
      // the name arrives, so the stream is read on even when this attempt
      // is no longer the page's current one.
      const isCurrent = () => mountedRef.current && attemptRef.current === attempt
      let ended = false
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

            const startedId = runIdOfRunStarted(eventType, data)
            if (startedId && cancelWhenNamedRef.current && !isCurrent()) {
              // Cancelled before the run had a name: cancel it now.
              cancelWhenNamedRef.current = false
              workflowApi.cancelRun(workflowId, startedId).catch(() => {})
              controller.abort()
              return
            }
            if (!isCurrent()) return
            if (startedId) {
              runIdRef.current = startedId
              setRunId(startedId)
            }

            // Push raw event to execution log
            const logEvent = logEventOf(eventType, data, Date.now())
            if (logEvent) {
              setLogEvents((prev) => [...prev, logEvent])
            }

            const nodeCount = nodeCountOfRunStarted(eventType, data)
            if (nodeCount !== null) setTotalNodeCount(nodeCount)

            const waiting = waitingOfRunWaiting(eventType, data)
            const status = nodeStatusOfEvent(eventType)
            if (status) {
              const nodeId = data.node_id as string
              setNodeResults((prev) => ({
                ...(prev ?? {}),
                [nodeId]: nodeResultOfEvent(status, data, prev?.[nodeId]),
              }))
            } else if (waiting !== null) {
              // The run stopped for a decision. The stream ends here; the
              // run goes on later and is followed by its record.
              setWaitingFor(waiting)
              setRunStatus("waiting")
              setIsRunning(false)
            } else if (eventType === "run_completed") {
              ended = true
              const cancelled = data.status === "cancelled"
              setRunStatus(cancelled ? "cancelled" : "completed")
              setFinalOutputs(cancelled ? null : ((data.outputs ?? null) as Record<string, unknown> | null))
              setRunDuration((data.duration_ms ?? null) as number | null)
              setIsRunning(false)
            } else if (eventType === "run_failed") {
              ended = true
              setRunStatus("failed")
              setFinalError((data.error ?? "Unknown error") as string)
              setRunDuration((data.duration_ms ?? null) as number | null)
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
        if (!isCurrent()) return
        if (runIdRef.current === null) {
          if (abortRef.current === controller) abortRef.current = null
          // The run did not start. A refusal about an input goes back to
          // the input form, which shows it on that input.
          const refusedInput = refusedInputOf(err)
          if (refusedInput !== null) {
            setInputError({ input: refusedInput, message: getErrorMessage(err, tError) })
            backToForm()
            return
          }
          // A run the server refused to start is explained in the user's
          // language where the refusal has a message.
          setFinalError(
            err instanceof ApiError
              ? getErrorMessage(err, tError)
              : err instanceof Error ? err.message : "Stream error",
          )
          setRunStatus("failed")
          setIsRunning(false)
          return
        }
        // The stream broke after the run had started: the run goes on.
      }

      if (!isCurrent()) return
      if (abortRef.current === controller) abortRef.current = null
      if (ended) return
      if (runIdRef.current) {
        // The stream is over and the run is not (it waits for a decision,
        // or the connection dropped): follow the run itself.
        followRun(runIdRef.current)
      } else {
        setIsRunning(false)
      }
    },
    [workflowId, tError, stopFollowing, followRun],
  )

  /** Ask the server to cancel the run the panel shows. */
  const cancelRun = useCallback(() => {
    attemptRef.current += 1
    stopFollowing()
    setIsRunning(false)
    setWaitingFor([])
    const id = runIdRef.current
    if (!id) {
      // Not named yet. If the request is on its way the run is cancelled
      // when its id arrives; aborting the stream would only stop reading.
      if (abortRef.current) cancelWhenNamedRef.current = true
      setRunStatus(null)
      setNodeResults(null)
      return
    }
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setRunStatus("cancelled")
    if (!workflowId) return
    workflowApi
      .cancelRun(workflowId, id)
      .catch(() => {
        // Refused (the run had just ended): its record says how.
      })
      .then(() => {
        if (mountedRef.current && runIdRef.current === id) followRun(id)
      })
  }, [workflowId, stopFollowing, followRun])

  return {
    isRunning,
    /** The run stopped at a step that needs a person's decision. */
    isWaiting: runStatus === "waiting",
    runPanelOpen,
    runId,
    runStatus,
    waitingFor,
    nodeResults,
    finalOutputs,
    finalError,
    runDuration,
    totalNodeCount,
    logEvents,
    inputError,
    clearInputError,
    openRunPanel,
    closeRunPanel,
    runAgain,
    startRun,
    cancelRun,
    attachToRun,
    refreshRun,
  }
}
