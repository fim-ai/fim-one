"use client"

import { useState, useCallback, useRef } from "react"

export interface SSEMessage {
  event: string
  data: unknown
  timestamp: number
}

export function useSSE() {
  const [messages, setMessages] = useState<SSEMessage[]>([])
  const [isRunning, setIsRunning] = useState(false)
  const sourceRef = useRef<EventSource | null>(null)

  const start = useCallback((url: string) => {
    // Close any existing connection
    if (sourceRef.current) {
      sourceRef.current.close()
      sourceRef.current = null
    }

    setMessages([])
    setIsRunning(true)

    const es = new EventSource(url)
    sourceRef.current = es

    const handleEvent = (eventType: string) => (e: MessageEvent) => {
      try {
        const data: unknown = JSON.parse(e.data)
        setMessages((prev) => [...prev, { event: eventType, data, timestamp: Date.now() }])

        if (eventType === "done") {
          es.close()
          sourceRef.current = null
          setIsRunning(false)
        }
      } catch {
        // Ignore unparseable data (keepalives, etc.)
      }
    }

    es.addEventListener("step", handleEvent("step"))
    es.addEventListener("step_progress", handleEvent("step_progress"))
    es.addEventListener("phase", handleEvent("phase"))
    es.addEventListener("done", handleEvent("done"))

    es.onerror = () => {
      es.close()
      sourceRef.current = null
      setIsRunning(false)
    }
  }, [])

  const reset = useCallback(() => {
    if (sourceRef.current) {
      sourceRef.current.close()
      sourceRef.current = null
    }
    setMessages([])
    setIsRunning(false)
  }, [])

  return { messages, isRunning, start, reset }
}
