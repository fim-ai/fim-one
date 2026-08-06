"use client"

import { useState, useCallback, useRef, useEffect } from "react"
import { ApiError } from "@/lib/api"
import { createSseParser, unwrapPayload } from "@/lib/sse-parser"

export interface SSEMessage {
  event: string
  data: unknown
  timestamp: number
  /**
   * Monotonic per-conversation cursor. Set on events that arrive from
   * ``/api/chat/resume`` (which wraps each frame as ``{cursor, data}``).
   * Live stream events currently omit this — keep nullable for safety.
   */
  cursor?: number
}

/**
 * Reason for an abort. ``"user"`` means the abort() method was invoked
 * explicitly (cancel button, route change, unmount). ``"network"`` means
 * the stream terminated due to a fetch/reader error — a candidate for
 * automatic resume. ``null`` is the initial / post-reset state.
 */
export type AbortReason = "user" | "network" | null

export interface StartOptions {
  body?: Record<string, unknown>
  onError?: (err: Error) => void
  /**
   * Fired when the stream terminates due to a network error (not a user
   * abort, not a normal ``end`` event). Receives the last cursor
   * consumed by the client so a resume request can pick up where we
   * left off. If no cursored event was seen, ``-1`` is passed — the
   * ``/chat/resume`` endpoint treats that as "replay everything".
   */
  onDisconnect?: (lastCursor: number) => void
}

export function useSSE() {
  const [messages, setMessages] = useState<SSEMessage[]>([])
  const [isRunning, setIsRunning] = useState(false)
  const [isError, setIsError] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const abortReasonRef = useRef<AbortReason>(null)
  const lastCursorRef = useRef<number>(-1)

  // rAF batching: buffer incoming messages, flush once per animation frame
  const pendingMessagesRef = useRef<SSEMessage[]>([])
  const rafIdRef = useRef<number | null>(null)

  const flushPending = useCallback(() => {
    rafIdRef.current = null
    if (pendingMessagesRef.current.length === 0) return
    const batch = pendingMessagesRef.current
    pendingMessagesRef.current = []
    setMessages((prev) => [...prev, ...batch])
  }, [])

  const scheduleFlush = useCallback(() => {
    if (rafIdRef.current === null) {
      rafIdRef.current = requestAnimationFrame(flushPending)
    }
  }, [flushPending])

  // Cancel any pending rAF on unmount
  useEffect(() => {
    return () => {
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current)
        rafIdRef.current = null
      }
    }
  }, [])

  const start = useCallback((url: string, options?: StartOptions) => {
    // Abort any existing stream (user-initiated transition, not a network drop)
    if (abortRef.current) {
      abortReasonRef.current = "user"
      abortRef.current.abort()
      abortRef.current = null
    }

    // Clear any buffered messages from a previous stream
    pendingMessagesRef.current = []
    if (rafIdRef.current !== null) {
      cancelAnimationFrame(rafIdRef.current)
      rafIdRef.current = null
    }

    setMessages([])
    setIsRunning(true)
    setIsError(false)
    abortReasonRef.current = null
    lastCursorRef.current = -1

    const controller = new AbortController()
    abortRef.current = controller

    ;(async () => {
      try {
        const fetchInit: RequestInit = { signal: controller.signal }
        if (options?.body) {
          fetchInit.method = "POST"
          fetchInit.headers = {
            "Content-Type": "application/json",
            // Uses browser timezone as fallback. Ideally this would use the user's
            // saved timezone from useAuth(), but useSSE is a low-level fetch hook
            // that may be invoked before auth context is available.
            "X-Timezone": Intl.DateTimeFormat().resolvedOptions().timeZone,
          }
          fetchInit.body = JSON.stringify(options.body)
        }
        const res = await fetch(url, fetchInit)

        if (!res.ok) {
          // Read error body before closing
          const body = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
          const detail: string =
            typeof body?.detail === "string"
              ? body.detail
              : `Request failed (${res.status})`
          setIsError(true)
          options?.onError?.(new ApiError(
            res.status,
            detail,
            body?.error_code ?? null,
            body?.error_args ?? {},
          ))
          setIsRunning(false)
          abortRef.current = null
          return
        }

        if (!res.body) {
          setIsError(true)
          options?.onError?.(new Error("No response body"))
          setIsRunning(false)
          abortRef.current = null
          return
        }

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        const parser = createSseParser()

        const dispatch = (eventType: string, rawData: string) => {
          try {
            // Both the live stream and /chat/resume wrap payloads as
            // {cursor, data}, so position tracking is identical on either.
            const { cursor, data: unwrapped } = unwrapPayload(rawData)
            if (cursor !== undefined && cursor > lastCursorRef.current) {
              lastCursorRef.current = cursor
            }
            pendingMessagesRef.current.push({
              event: eventType,
              data: unwrapped,
              timestamp: Date.now(),
              cursor,
            })

            if (eventType === "end") {
              // Flush immediately on stream end so consumers see the final state
              if (rafIdRef.current !== null) {
                cancelAnimationFrame(rafIdRef.current)
                rafIdRef.current = null
              }
              flushPending()
              abortReasonRef.current = "user" // graceful end, not a disconnect
              controller.abort()
              abortRef.current = null
              setIsRunning(false)
            } else {
              scheduleFlush()
            }
          } catch {
            // Ignore unparseable keepalives
          }
        }

        while (true) {
          const { done, value } = await reader.read()
          if (done) {
            // Stream closed without an explicit "end" event — treat as a
            // network disconnect so callers can consider resuming.
            if (abortReasonRef.current === null) {
              abortReasonRef.current = "network"
            }
            break
          }

          for (const { event, data } of parser.push(
            decoder.decode(value, { stream: true }),
          )) {
            dispatch(event, data)
          }
        }
      } catch (err: unknown) {
        if ((err as { name?: string })?.name === "AbortError") {
          // Flush any buffered messages so partial results are visible after abort
          if (rafIdRef.current !== null) {
            cancelAnimationFrame(rafIdRef.current)
            rafIdRef.current = null
          }
          flushPending()
          return
        }
        // Non-abort exception = network error (fetch throw, reader throw, …)
        if (abortReasonRef.current === null) {
          abortReasonRef.current = "network"
        }
        // Flush buffered messages before signaling error
        if (rafIdRef.current !== null) {
          cancelAnimationFrame(rafIdRef.current)
          rafIdRef.current = null
        }
        flushPending()
        setIsError(true)
        options?.onError?.(err instanceof Error ? err : new Error("Stream error"))
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null
          setIsRunning(false)
        }
        // Fire disconnect callback only when the stream died due to a
        // network fault — user aborts and graceful "end" events are not
        // candidates for auto-resume.
        if (abortReasonRef.current === "network") {
          options?.onDisconnect?.(lastCursorRef.current)
        }
      }
    })()
  }, [flushPending, scheduleFlush])

  const abort = useCallback(() => {
    if (abortRef.current) {
      abortReasonRef.current = "user"
      abortRef.current.abort()
      abortRef.current = null
    }
    // Flush buffered messages so partial results are visible
    if (rafIdRef.current !== null) {
      cancelAnimationFrame(rafIdRef.current)
      rafIdRef.current = null
    }
    flushPending()
    setIsRunning(false)
    // Messages are intentionally kept so the user can see partial results
  }, [flushPending])

  const reset = useCallback(() => {
    if (abortRef.current) {
      abortReasonRef.current = "user"
      abortRef.current.abort()
      abortRef.current = null
    }
    // Discard any buffered messages — reset clears everything
    pendingMessagesRef.current = []
    if (rafIdRef.current !== null) {
      cancelAnimationFrame(rafIdRef.current)
      rafIdRef.current = null
    }
    lastCursorRef.current = -1
    abortReasonRef.current = null
    setMessages([])
    setIsRunning(false)
    setIsError(false)
  }, [])

  /**
   * Append externally-produced SSE messages (used by the resume hook to
   * merge replayed frames back into the same messages array consumers
   * are already reading). Advances ``lastCursor`` if the appended
   * messages carry larger cursors.
   */
  const appendMessages = useCallback((msgs: SSEMessage[]) => {
    if (msgs.length === 0) return
    for (const m of msgs) {
      if (typeof m.cursor === "number" && m.cursor > lastCursorRef.current) {
        lastCursorRef.current = m.cursor
      }
    }
    setMessages((prev) => [...prev, ...msgs])
  }, [])

  return {
    messages,
    isRunning,
    isError,
    start,
    reset,
    abort,
    appendMessages,
    lastCursorRef,
    abortReasonRef,
  }
}
