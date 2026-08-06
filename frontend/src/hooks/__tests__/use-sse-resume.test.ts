import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import { act, renderHook, waitFor } from "@testing-library/react"
import { useSseResume } from "@/hooks/use-sse-resume"

// ---------------------------------------------------------------------------
// Helpers — build a controllable ReadableStream<Uint8Array> so tests can
// drip SSE frames and then trigger either a graceful close or a network
// error mid-stream.
// ---------------------------------------------------------------------------

interface StreamHandle {
  response: Response
  push: (chunk: string) => void
  close: () => void
  error: (err?: Error) => void
}

function makeSseStream(initHeaders: HeadersInit = {}): StreamHandle {
  const encoder = new TextEncoder()
  let controller: ReadableStreamDefaultController<Uint8Array>
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c
    },
  })
  const response = new Response(body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      ...initHeaders,
    },
  })
  return {
    response,
    push: (chunk: string) => {
      controller.enqueue(encoder.encode(chunk))
    },
    close: () => {
      try {
        controller.close()
      } catch {
        /* already closed */
      }
    },
    error: (err?: Error) => {
      try {
        controller.error(err ?? new Error("network"))
      } catch {
        /* already errored */
      }
    },
  }
}

function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

/** Flush all pending microtasks + rAF callbacks. */
async function flush(): Promise<void> {
  // Several rounds because each stream reader iteration requeues a
  // ``reader.read()`` microtask.
  for (let i = 0; i < 10; i++) {
    await Promise.resolve()
  }
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

describe("useSseResume", () => {
  let originalFetch: typeof fetch

  beforeEach(() => {
    originalFetch = global.fetch
  })

  afterEach(() => {
    global.fetch = originalFetch
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it("advances lastCursor as events with cursor arrive", async () => {
    const live = makeSseStream()
    const fetchMock = vi.fn().mockResolvedValue(live.response)
    global.fetch = fetchMock as unknown as typeof fetch

    const { result } = renderHook(() =>
      useSseResume({
        conversationId: "conv-1",
        apiBaseUrl: "http://test",
        getAccessToken: () => "tok",
      }),
    )

    act(() => {
      result.current.start("http://test/api/chat/react")
    })

    act(() => {
      live.push(frame("step", { cursor: 0, data: { type: "thinking", status: "start" } }))
    })
    act(() => {
      live.push(frame("step", { cursor: 3, data: { type: "thinking", status: "done" } }))
    })
    act(() => {
      live.push(frame("end", {}))
      live.close()
    })

    await waitFor(() => {
      const stepMsgs = result.current.messages.filter((m) => m.event === "step")
      expect(stepMsgs.length).toBeGreaterThanOrEqual(2)
    })
    const cursors = result.current.messages
      .map((m) => m.cursor)
      .filter((c): c is number => typeof c === "number")
    expect(cursors).toEqual(expect.arrayContaining([0, 3]))
  })

  it("user-initiated abort does NOT trigger resume", async () => {
    const live = makeSseStream()
    const fetchMock = vi.fn().mockResolvedValue(live.response)
    global.fetch = fetchMock as unknown as typeof fetch

    const { result } = renderHook(() =>
      useSseResume({
        conversationId: "conv-1",
        apiBaseUrl: "http://test",
        getAccessToken: () => "tok",
      }),
    )

    act(() => {
      result.current.start("http://test/api/chat/react")
    })
    act(() => {
      live.push(frame("step", { cursor: 0, data: { type: "thinking", status: "start" } }))
    })

    act(() => {
      result.current.abort()
    })

    await new Promise((r) => setTimeout(r, 50))

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(result.current.resumeState).toBe("idle")
  })

  it("network error triggers resume with correct {conversation_id, cursor} payload", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] })
    const live = makeSseStream()
    const resume = makeSseStream()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(live.response)
      .mockResolvedValueOnce(resume.response)
    global.fetch = fetchMock as unknown as typeof fetch

    const { result } = renderHook(() =>
      useSseResume({
        conversationId: "conv-42",
        apiBaseUrl: "http://test",
        getAccessToken: () => "tok",
      }),
    )

    act(() => {
      result.current.start("http://test/api/chat/react")
    })

    await act(async () => {
      await flush()
      live.push(frame("step", { cursor: 5, data: { type: "thinking", status: "start" } }))
      await flush()
    })

    act(() => {
      live.error(new Error("ECONNRESET"))
    })
    await act(async () => {
      await flush()
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(400)
      await flush()
    })

    expect(fetchMock).toHaveBeenCalledTimes(2)
    const call = fetchMock.mock.calls[1]
    expect(call[0]).toBe("http://test/api/chat/resume")
    const init = call[1] as RequestInit
    expect(init.method).toBe("POST")
    const body = JSON.parse(init.body as string)
    expect(body).toEqual({ conversation_id: "conv-42", cursor: 5 })

    act(() => {
      resume.push(frame("resume_done", { cursor: 5, data: { replayed: 0, last_cursor: 5 } }))
      resume.close()
    })
    await act(async () => {
      await flush()
    })
  })

  it("receives resume_done and exits reconnecting state", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] })
    const live = makeSseStream()
    const resume = makeSseStream()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(live.response)
      .mockResolvedValueOnce(resume.response)
    global.fetch = fetchMock as unknown as typeof fetch

    const { result } = renderHook(() =>
      useSseResume({
        conversationId: "conv-1",
        apiBaseUrl: "http://test",
        getAccessToken: () => null,
      }),
    )

    act(() => {
      result.current.start("http://test/api/chat/react")
    })
    await act(async () => {
      await flush()
    })

    act(() => {
      live.error(new Error("boom"))
    })
    await act(async () => {
      await flush()
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(400)
      await flush()
    })

    expect(result.current.resumeState).toBe("reconnecting")

    act(() => {
      resume.push(frame("resume_done", { cursor: 0, data: { replayed: 0, last_cursor: -1 } }))
      resume.close()
    })
    await act(async () => {
      await flush()
    })

    expect(result.current.resumeState).toBe("idle")
  })

  it("3 failed resume attempts → resumeState='failed', isError=true", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] })
    const live = makeSseStream()
    const makeBad = () => new Response("nope", { status: 500 })
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(live.response)
      .mockResolvedValueOnce(makeBad())
      .mockResolvedValueOnce(makeBad())
      .mockResolvedValueOnce(makeBad())
    global.fetch = fetchMock as unknown as typeof fetch

    const { result } = renderHook(() =>
      useSseResume({
        conversationId: "conv-1",
        maxRetries: 3,
        backoffMs: () => 10,
        apiBaseUrl: "http://test",
        getAccessToken: () => null,
      }),
    )

    act(() => {
      result.current.start("http://test/api/chat/react")
    })
    await act(async () => {
      await flush()
    })

    act(() => {
      live.error(new Error("boom"))
    })
    await act(async () => {
      await flush()
    })

    // 3 retries, 10ms each; pump 4 cycles with good slack + microtask flushes.
    for (let i = 0; i < 6; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(15)
        await flush()
      })
    }

    expect(result.current.resumeState).toBe("failed")
    expect(result.current.isError).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it("resume response with replayed events merges into messages in order", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] })
    const live = makeSseStream()
    const resume = makeSseStream()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(live.response)
      .mockResolvedValueOnce(resume.response)
    global.fetch = fetchMock as unknown as typeof fetch

    const { result } = renderHook(() =>
      useSseResume({
        conversationId: "conv-1",
        apiBaseUrl: "http://test",
        getAccessToken: () => null,
      }),
    )

    act(() => {
      result.current.start("http://test/api/chat/react")
    })
    await act(async () => {
      await flush()
      live.push(frame("step", { cursor: 0, data: { type: "thinking", status: "start", tag: "A" } }))
      await flush()
    })

    act(() => {
      live.error(new Error("boom"))
    })
    await act(async () => {
      await flush()
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(400)
      await flush()
    })

    act(() => {
      resume.push(frame("step", { cursor: 1, data: { type: "thinking", status: "done", tag: "B" } }))
      resume.push(frame("step", { cursor: 2, data: { type: "iteration", status: "start", tag: "C" } }))
      resume.push(frame("resume_done", { cursor: 3, data: { replayed: 2, last_cursor: 2 } }))
      resume.close()
    })
    await act(async () => {
      await flush()
    })

    const steps = result.current.messages
      .filter((m) => m.event === "step")
      .map((m) => (m.data as { tag?: string }).tag)
    expect(steps).toEqual(["A", "B", "C"])
    expect(result.current.resumeState).toBe("idle")
  })

  it("exponential backoff timing (300 → 1000 → 3000ms)", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] })
    const live = makeSseStream()
    const makeBad = () => new Response("nope", { status: 500 })
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(live.response)
      .mockResolvedValueOnce(makeBad())
      .mockResolvedValueOnce(makeBad())
      .mockResolvedValueOnce(makeBad())
    global.fetch = fetchMock as unknown as typeof fetch

    const { result } = renderHook(() =>
      useSseResume({
        conversationId: "conv-1",
        maxRetries: 3,
        apiBaseUrl: "http://test",
        getAccessToken: () => null,
      }),
    )

    act(() => {
      result.current.start("http://test/api/chat/react")
    })
    await act(async () => {
      await flush()
    })

    act(() => {
      live.error(new Error("boom"))
    })
    await act(async () => {
      await flush()
    })

    // Before 300ms elapses, no resume fetch yet.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
      await flush()
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // After 300ms, attempt 1 fires.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250)
      await flush()
    })
    expect(fetchMock).toHaveBeenCalledTimes(2)

    // After another ~1000ms, attempt 2 fires.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1050)
      await flush()
    })
    expect(fetchMock).toHaveBeenCalledTimes(3)

    // After another ~3000ms, attempt 3 fires.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3050)
      await flush()
    })
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it("without conversation_id falls back to plain useSse behaviour (no resume)", async () => {
    const live = makeSseStream()
    const fetchMock = vi.fn().mockResolvedValue(live.response)
    global.fetch = fetchMock as unknown as typeof fetch

    const { result } = renderHook(() =>
      useSseResume({
        apiBaseUrl: "http://test",
        getAccessToken: () => null,
      }),
    )

    act(() => {
      result.current.start("http://test/api/chat/react")
    })
    await new Promise((r) => setTimeout(r, 20))

    act(() => {
      live.push(frame("step", { cursor: 0, data: { hello: "world" } }))
    })

    act(() => {
      live.error(new Error("boom"))
    })

    await new Promise((r) => setTimeout(r, 50))
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await waitFor(() => {
      expect(result.current.resumeState).toBe("failed")
    })
  })

  it("keeps a frame's event name when the chunk boundary splits it", async () => {
    // The resume parser used to reset its frame state on every chunk, so a
    // frame whose `event:` and `data:` lines arrived separately lost its
    // name and was dispatched as "message", then dropped downstream.
    const live = makeSseStream()
    const replay = makeSseStream()
    const fetchMock = vi.fn().mockResolvedValue(replay.response)
    global.fetch = fetchMock as unknown as typeof fetch

    const { result } = renderHook(() =>
      useSseResume({
        conversationId: "conv-split",
        apiBaseUrl: "http://test",
        getAccessToken: () => "tok",
      }),
    )

    act(() => {
      result.current.start("http://test/api/chat/react")
    })
    await new Promise((r) => setTimeout(r, 20))
    act(() => {
      live.error(new Error("boom"))
    })
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())

    // Split the replayed frame across two reads, mid-frame.
    act(() => {
      replay.push("event: step\n")
    })
    // Let the reader consume that chunk on its own before the rest of the
    // frame arrives — otherwise both land in a single read and the split
    // never happens.
    await new Promise((r) => setTimeout(r, 20))
    act(() => {
      replay.push(`data: ${JSON.stringify({ cursor: 7, data: { type: "tool_call" } })}\n\n`)
    })
    await new Promise((r) => setTimeout(r, 20))
    act(() => {
      replay.push(frame("resume_done", { replayed: 1, last_cursor: 7 }))
      replay.close()
    })

    await waitFor(() => {
      expect(result.current.messages.some((m) => m.event === "step")).toBe(true)
    })
    const step = result.current.messages.find((m) => m.event === "step")
    expect(step?.cursor).toBe(7)
    expect(result.current.messages.some((m) => m.event === "message")).toBe(false)
  })
})
