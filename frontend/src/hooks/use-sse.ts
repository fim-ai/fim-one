"use client"

import { useState, useCallback, useRef } from "react"
import { ApiError } from "@/lib/api"

export interface SSEMessage {
  event: string
  data: unknown
  timestamp: number
}

export interface StartOptions {
  body?: Record<string, unknown>
  onError?: (err: Error) => void
}

export function useSSE() {
  const [messages, setMessages] = useState<SSEMessage[]>([])
  const [isRunning, setIsRunning] = useState(false)
  const [isError, setIsError] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const start = useCallback((url: string, options?: StartOptions) => {
    // Abort any existing stream
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }

    setMessages([])
    setIsRunning(true)
    setIsError(false)

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
        let buffer = ""
        let currentEvent = "message"
        let currentData = ""

        const dispatch = (eventType: string, rawData: string) => {
          try {
            const data: unknown = JSON.parse(rawData)
            setMessages((prev) => [...prev, { event: eventType, data, timestamp: Date.now() }])
            if (eventType === "end") {
              controller.abort()
              abortRef.current = null
              setIsRunning(false)
            }
          } catch {
            // Ignore unparseable keepalives
          }
        }

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split("\n")
          buffer = lines.pop() ?? "" // keep incomplete last line

          for (const line of lines) {
            if (line.startsWith("event:")) {
              currentEvent = line.slice(6).trim()
            } else if (line.startsWith("data:")) {
              currentData = line.slice(5).trim()
            } else if (line === "") {
              // Empty line = end of event
              if (currentData) {
                dispatch(currentEvent, currentData)
              }
              currentEvent = "message"
              currentData = ""
            }
          }
        }
      } catch (err: unknown) {
        if ((err as { name?: string })?.name === "AbortError") return
        setIsError(true)
        options?.onError?.(err instanceof Error ? err : new Error("Stream error"))
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null
          setIsRunning(false)
        }
      }
    })()
  }, [])

  const abort = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setIsRunning(false)
    // Messages are intentionally kept so the user can see partial results
  }, [])

  const reset = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setMessages([])
    setIsRunning(false)
    setIsError(false)
  }, [])

  return { messages, isRunning, isError, start, reset, abort }
}
