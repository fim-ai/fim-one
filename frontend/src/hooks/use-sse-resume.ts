"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useSSE, type SSEMessage, type StartOptions } from "@/hooks/use-sse"
import { getApiDirectUrl, ACCESS_TOKEN_KEY } from "@/lib/constants"
import { createSseParser, unwrapPayload, type SseFrame } from "@/lib/sse-parser"

/**
 * High-level state of the auto-resume machinery.
 *
 * - ``idle``         — no stream has started yet (or we finished cleanly)
 * - ``running``      — a live stream is in progress
 * - ``reconnecting`` — stream disconnected, attempting ``/chat/resume``
 * - ``failed``       — exhausted ``maxRetries`` without a successful resume
 */
export type ResumeState = "idle" | "running" | "reconnecting" | "failed"

export interface UseSseResumeOptions {
  /**
   * Conversation ID the stream is bound to. Required to enable resume —
   * if omitted, the hook degrades to plain ``useSSE`` behaviour.
   */
  conversationId?: string
  /** Default: 3 */
  maxRetries?: number
  /** Default: [300, 1000, 3000] ms */
  backoffMs?: (attempt: number) => number
  /**
   * Fetch implementation. Injected for testing — in production this
   * defaults to the global ``fetch``. Must support ``AbortSignal``.
   */
  fetchFn?: typeof fetch
  /**
   * API base URL for the resume endpoint. Defaults to
   * ``getApiDirectUrl()``. Overridable for tests.
   */
  apiBaseUrl?: string
  /**
   * Access token accessor. Defaults to reading ``ACCESS_TOKEN_KEY``
   * from ``localStorage``. Overridable for tests.
   */
  getAccessToken?: () => string | null
}

export interface UseSseResumeReturn {
  messages: SSEMessage[]
  isRunning: boolean
  isError: boolean
  resumeState: ResumeState
  resumeAttempt: number
  start: (url: string, options?: StartOptions) => void
  abort: () => void
  reset: () => void
}

const DEFAULT_BACKOFF = [300, 1000, 3000]

/**
 * Wraps ``useSSE`` with cursor-aware automatic resume.
 *
 * When the underlying stream terminates with ``abortReason === "network"``
 * the hook fires ``POST /api/chat/resume`` with the last seen cursor,
 * merges replayed frames back into ``messages`` in order, and exits the
 * reconnecting state upon a ``resume_done`` frame. After
 * ``maxRetries`` consecutive failures it surfaces ``resumeState="failed"``.
 *
 * Server-side dedup is authoritative: we send ``lastCursor`` and trust
 * the backend to only replay frames with ``cursor > lastCursor``.
 */
export function useSseResume(
  opts: UseSseResumeOptions = {},
): UseSseResumeReturn {
  const {
    conversationId,
    maxRetries = 3,
    backoffMs = (attempt) => DEFAULT_BACKOFF[Math.min(attempt, DEFAULT_BACKOFF.length - 1)] ?? 3000,
    fetchFn,
    apiBaseUrl,
    getAccessToken,
  } = opts

  const sse = useSSE()
  const { start: startInner, abort: abortInner, reset: resetInner, appendMessages, lastCursorRef } = sse

  const [resumeState, setResumeState] = useState<ResumeState>("idle")
  const [resumeAttempt, setResumeAttempt] = useState(0)
  const resumeAbortRef = useRef<AbortController | null>(null)
  const resumeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Preserve the last start() invocation so ``onError`` / error state is
  // threaded through replay attempts consistently.
  const lastStartOptionsRef = useRef<StartOptions | undefined>(undefined)
  // Synchronous guard — setState is async but we need to short-circuit
  // "abort during resume" immediately.
  const abortedRef = useRef(false)

  const clearResumeTimer = useCallback(() => {
    if (resumeTimerRef.current !== null) {
      clearTimeout(resumeTimerRef.current)
      resumeTimerRef.current = null
    }
  }, [])

  const cancelResume = useCallback(() => {
    clearResumeTimer()
    if (resumeAbortRef.current) {
      resumeAbortRef.current.abort()
      resumeAbortRef.current = null
    }
  }, [clearResumeTimer])

  // Clean up any pending retry timer on unmount
  useEffect(() => {
    return () => {
      cancelResume()
    }
  }, [cancelResume])

  /**
   * Turn parsed frames into appended messages.
   *
   * Frame splitting itself lives in the shared parser: this hook and the
   * live-stream hook used to each carry their own copy of that logic,
   * which is how one of them ended up resetting frame state per chunk.
   *
   * Returns true when a ``resume_done`` frame was seen.
   */
  const appendFrames = useCallback(
    (frames: SseFrame[]): boolean => {
      const appended: SSEMessage[] = []
      let sawResumeDone = false

      for (const { event, data } of frames) {
        let unwrapped
        try {
          unwrapped = unwrapPayload(data)
        } catch {
          continue // ignore malformed frames
        }
        if (event === "resume_done") {
          // Surfaced too, so consumers may inspect it (e.g. telemetry).
          sawResumeDone = true
        }
        appended.push({
          event,
          data: unwrapped.data,
          timestamp: Date.now(),
          cursor: unwrapped.cursor,
        })
      }

      if (appended.length > 0) {
        appendMessages(appended)
      }
      return sawResumeDone
    },
    [appendMessages],
  )

  const performResume = useCallback(
    async (): Promise<"ok" | "retry" | "aborted"> => {
      if (!conversationId) return "aborted"
      if (abortedRef.current) return "aborted"

      const controller = new AbortController()
      resumeAbortRef.current = controller

      const base = apiBaseUrl ?? getApiDirectUrl()
      const tokenGetter = getAccessToken
        ?? (() => (typeof window !== "undefined" ? localStorage.getItem(ACCESS_TOKEN_KEY) : null))
      const token = tokenGetter()
      const fetchImpl = fetchFn ?? (typeof fetch !== "undefined" ? fetch : null)
      if (!fetchImpl) return "retry"

      try {
        const res = await fetchImpl(`${base}/api/chat/resume`, {
          method: "POST",
          signal: controller.signal,
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({
            conversation_id: conversationId,
            cursor: lastCursorRef.current,
          }),
        })

        if (!res.ok || !res.body) {
          return "retry"
        }

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        const parser = createSseParser()
        let sawResumeDone = false

        while (true) {
          if (abortedRef.current) {
            try {
              await reader.cancel()
            } catch {
              /* ignore */
            }
            return "aborted"
          }
          const { done, value } = await reader.read()
          if (done) break
          const chunk = decoder.decode(value, { stream: true })
          if (appendFrames(parser.push(chunk))) {
            sawResumeDone = true
          }
        }

        // Complete a trailing frame that never got its blank line
        // (shouldn't happen on a well-formed stream, but be defensive).
        if (appendFrames(parser.flush())) {
          sawResumeDone = true
        }

        // If the server produced no events and no resume_done marker
        // (shouldn't happen — the endpoint always closes with one) we
        // still treat it as success rather than thrash.
        void sawResumeDone
        return "ok"
      } catch (err: unknown) {
        if ((err as { name?: string })?.name === "AbortError") return "aborted"
        return "retry"
      } finally {
        if (resumeAbortRef.current === controller) {
          resumeAbortRef.current = null
        }
      }
    },
    [apiBaseUrl, conversationId, fetchFn, getAccessToken, lastCursorRef, appendFrames],
  )

  const scheduleResume = useCallback(
    (attempt: number) => {
      if (abortedRef.current) return
      if (!conversationId) {
        setResumeState("failed")
        return
      }
      if (attempt > maxRetries) {
        setResumeState("failed")
        return
      }

      setResumeState("reconnecting")
      setResumeAttempt(attempt)

      const delay = backoffMs(attempt - 1)
      resumeTimerRef.current = setTimeout(async () => {
        resumeTimerRef.current = null
        const outcome = await performResume()
        if (outcome === "aborted") {
          return
        }
        if (outcome === "ok") {
          setResumeState("idle")
          setResumeAttempt(0)
          return
        }
        // "retry"
        scheduleResume(attempt + 1)
      }, delay)
    },
    [backoffMs, conversationId, maxRetries, performResume],
  )

  const start = useCallback(
    (url: string, options?: StartOptions) => {
      abortedRef.current = false
      setResumeState("running")
      setResumeAttempt(0)
      cancelResume()
      lastStartOptionsRef.current = options

      const wrapped: StartOptions = {
        ...options,
        onDisconnect: (cursor) => {
          options?.onDisconnect?.(cursor)
          if (!conversationId) {
            setResumeState("failed")
            return
          }
          if (abortedRef.current) return
          scheduleResume(1)
        },
      }
      startInner(url, wrapped)
    },
    [cancelResume, conversationId, scheduleResume, startInner],
  )

  const abort = useCallback(() => {
    abortedRef.current = true
    cancelResume()
    abortInner()
    setResumeState("idle")
    setResumeAttempt(0)
  }, [abortInner, cancelResume])

  const reset = useCallback(() => {
    abortedRef.current = false
    cancelResume()
    resetInner()
    setResumeState("idle")
    setResumeAttempt(0)
  }, [cancelResume, resetInner])

  // If the underlying stream finishes cleanly (isRunning=false, no
  // network abort), advance to idle so the indicator clears.
  useEffect(() => {
    if (!sse.isRunning && resumeState === "running") {
      setResumeState("idle")
    }
  }, [sse.isRunning, resumeState])

  const isError = sse.isError || resumeState === "failed"

  return {
    messages: sse.messages,
    isRunning: sse.isRunning || resumeState === "reconnecting",
    isError,
    resumeState,
    resumeAttempt,
    start,
    abort,
    reset,
  }
}
