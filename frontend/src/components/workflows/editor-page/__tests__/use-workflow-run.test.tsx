import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"

import { useWorkflowRun } from "@/components/workflows/editor-page/use-workflow-run"

const translate = vi.hoisted(() => (key: string) => key)
vi.mock("next-intl", () => ({ useTranslations: () => translate }))

/** A response whose body streams the given SSE text. */
function sseResponse(text: string) {
  const bytes = new TextEncoder().encode(text)
  let sent = false
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => {
          if (sent) return { done: true, value: undefined }
          sent = true
          return { done: false, value: bytes }
        },
      }),
    },
  }
}

const event = (name: string, data: Record<string, unknown>) =>
  `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal("fetch", fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe("useWorkflowRun", () => {
  // The run panel's "n/m nodes" took m from the canvas. The run executes
  // the stored workflow, so with unsaved steps on the canvas the two
  // disagreed (3/4 on a finished run). m is now what the run itself reports.
  it("counts progress against the step count the run reports", async () => {
    fetchMock.mockResolvedValue(
      sseResponse(
        event("run_started", { run_id: "r1", status: "running" }) +
          event("run_started", { run_id: "r1", workflow_id: "wf1", node_count: 3 }) +
          event("node_started", { node_id: "start_1" }) +
          event("node_completed", { node_id: "start_1", duration_ms: 1 }) +
          event("node_completed", { node_id: "llm_1", output_preview: "hi", duration_ms: 20 }) +
          event("node_completed", { node_id: "end_1", duration_ms: 1 }) +
          event("run_completed", { outputs: { out: "hi" }, duration_ms: 30 }) +
          event("end", {}),
      ),
    )
    const { result } = renderHook(() => useWorkflowRun("wf1"))
    expect(result.current.totalNodeCount).toBeNull()
    await act(() => result.current.startRun({ topic: "x" }))
    await waitFor(() => expect(result.current.isRunning).toBe(false))
    expect(result.current.totalNodeCount).toBe(3)
    expect(Object.keys(result.current.nodeResults ?? {})).toEqual(["start_1", "llm_1", "end_1"])
    expect(result.current.finalOutputs).toEqual({ out: "hi" })
    expect(result.current.logEvents.map((e) => e.eventType)).toEqual([
      "node_started",
      "node_completed",
      "node_completed",
      "node_completed",
      "run_completed",
    ])
    expect(fetchMock.mock.calls[0][0]).toBe("/api/workflows/wf1/run")
  })

  it("forgets the count when the panel goes back to its input form", async () => {
    fetchMock.mockResolvedValue(
      sseResponse(event("run_started", { node_count: 5 }) + event("run_completed", { outputs: {} })),
    )
    const { result } = renderHook(() => useWorkflowRun("wf1"))
    await act(() => result.current.startRun({}))
    await waitFor(() => expect(result.current.totalNodeCount).toBe(5))
    act(() => result.current.runAgain())
    expect(result.current.totalNodeCount).toBeNull()
  })

  it("has no count for a run the server refused", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: "Workflow is already running", error_code: "workflow_already_running" }),
    })
    const { result } = renderHook(() => useWorkflowRun("wf1"))
    await act(() => result.current.startRun({}))
    expect(result.current.totalNodeCount).toBeNull()
    expect(result.current.finalError).toBe("Workflow is already running")
    expect(result.current.isRunning).toBe(false)
  })

  // A run refused for one of its inputs used to show as a failed run. The
  // panel goes back to its form and the refusal names the input.
  it("goes back to the input form with the refused input named", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({
        detail: "Input 'count' must be a number.",
        error_code: "workflow_input_invalid",
        error_args: { input: "count", expected: "a number" },
      }),
    })
    const { result } = renderHook(() => useWorkflowRun("wf1"))
    await act(() => result.current.startRun({ count: "abc" }))
    expect(result.current.inputError).toEqual({ input: "count", message: "Input 'count' must be a number." })
    expect(result.current.isRunning).toBe(false)
    expect(result.current.nodeResults).toBeNull()
    expect(result.current.finalError).toBeNull()
    expect(result.current.runStatus).toBeNull()
    act(() => result.current.clearInputError())
    expect(result.current.inputError).toBeNull()
  })

  // The server runs the stored workflow, so what is on the canvas has to be
  // saved before the run is requested.
  describe("saving before the run", () => {
    const finishedRun = () =>
      sseResponse(event("run_started", { node_count: 1 }) + event("run_completed", { outputs: {} }) + event("end", {}))

    it("requests the run only after the save has finished", async () => {
      fetchMock.mockResolvedValue(finishedRun())
      let saved!: (ok: boolean) => void
      const beforeRun = vi.fn(() => new Promise<boolean>((resolve) => (saved = resolve)))
      const { result } = renderHook(() => useWorkflowRun("wf1", beforeRun))
      let started!: Promise<void>
      act(() => {
        started = result.current.startRun({ topic: "x" })
      })
      expect(beforeRun).toHaveBeenCalledTimes(1)
      // Busy from the click on, so that the form cannot be sent twice.
      expect(result.current.isRunning).toBe(true)
      expect(fetchMock).not.toHaveBeenCalled()

      await act(async () => {
        saved(true)
        await started
      })
      expect(fetchMock).toHaveBeenCalledTimes(1)
      await waitFor(() => expect(result.current.isRunning).toBe(false))
      expect(result.current.totalNodeCount).toBe(1)
    })

    it("does not run when the save failed, and goes back to the input form", async () => {
      fetchMock.mockResolvedValue(finishedRun())
      const { result } = renderHook(() => useWorkflowRun("wf1", async () => false))
      await act(() => result.current.startRun({}))
      expect(fetchMock).not.toHaveBeenCalled()
      expect(result.current.isRunning).toBe(false)
      expect(result.current.nodeResults).toBeNull()
      expect(result.current.finalError).toBeNull()
    })

    it("does not run when the save threw", async () => {
      const { result } = renderHook(() =>
        useWorkflowRun("wf1", async () => {
          throw new Error("boom")
        }),
      )
      await act(() => result.current.startRun({}))
      expect(fetchMock).not.toHaveBeenCalled()
      expect(result.current.isRunning).toBe(false)
    })

    it("does not run when it was cancelled while the save was pending", async () => {
      fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({}) })
      let saved!: (ok: boolean) => void
      const { result } = renderHook(() =>
        useWorkflowRun("wf1", () => new Promise<boolean>((resolve) => (saved = resolve))),
      )
      let started!: Promise<void>
      act(() => {
        started = result.current.startRun({})
      })
      act(() => result.current.cancelRun())
      expect(result.current.isRunning).toBe(false)
      await act(async () => {
        saved(true)
        await started
      })
      const runRequests = fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/run"))
      expect(runRequests).toHaveLength(0)
    })
  })

  // A run is not the stream that started it. Closing the stream no longer
  // cancels the run, and a run that waits for a decision goes on later
  // without a stream: it is followed by reading its record.
  describe("a run that outlives its stream", () => {
    const json = (body: unknown, status = 200) => ({
      ok: status < 400,
      status,
      headers: { get: () => null },
      json: async () => body,
    })
    const runRecord = (over: Record<string, unknown>) =>
      json({ data: { id: "r1", workflow_id: "wf1", user_id: "u1", inputs: {}, outputs: null, node_results: null, duration_ms: null, error: null, waiting_for: null, ...over } })
    const WAITING = [{ node_id: "h_1", confirmation_id: "c1", kind: "workflow_approval", expires_at: null }]
    const waitingStream = () =>
      sseResponse(
        event("run_started", { run_id: "r1", status: "running" }) +
          event("run_started", { run_id: "r1", workflow_id: "wf1", node_count: 3 }) +
          event("node_completed", { node_id: "start_1", duration_ms: 1 }) +
          event("node_waiting", { node_id: "h_1", status: "waiting", waiting_for: WAITING[0] }) +
          event("run_waiting", { run_id: "r1", status: "waiting", waiting: WAITING }) +
          event("end", {}),
      )
    /** Requests by path, in order. */
    const requests = () => fetchMock.mock.calls.map(([url, init]) => `${init?.method ?? "GET"} ${url}`)
    const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const tick = () => act(async () => { await vi.advanceTimersByTimeAsync(2000) })

    beforeEach(() => {
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] })
    })
    afterEach(() => {
      vi.useRealTimers()
    })

    it("shows a run that stopped for a decision as waiting, not running or failed", async () => {
      fetchMock.mockImplementation(async (url: string) =>
        String(url).endsWith("/run")
          ? waitingStream()
          : runRecord({ status: "waiting", waiting_for: WAITING, node_results: { start_1: { status: "completed" }, h_1: { status: "waiting" } } }),
      )
      const { result } = renderHook(() => useWorkflowRun("wf1"))
      await act(() => result.current.startRun({}))
      await flush()
      expect(result.current.runId).toBe("r1")
      expect(result.current.isRunning).toBe(false)
      expect(result.current.isWaiting).toBe(true)
      expect(result.current.runStatus).toBe("waiting")
      expect(result.current.finalError).toBeNull()
      expect(result.current.waitingFor).toEqual(WAITING)
      expect(result.current.nodeResults?.h_1.status).toBe("waiting")
    })

    it("follows a waiting run by its record until it ends", async () => {
      let reads = 0
      fetchMock.mockImplementation(async (url: string) => {
        if (String(url).endsWith("/run")) return waitingStream()
        reads += 1
        if (reads < 3) return runRecord({ status: "waiting", waiting_for: WAITING, node_results: { h_1: { status: "waiting" } } })
        return runRecord({
          status: "completed",
          outputs: { out: "approved" },
          duration_ms: 4200,
          node_results: { start_1: { status: "completed" }, h_1: { status: "completed", output_preview: "ok" }, end_1: { status: "completed" } },
        })
      })
      const { result } = renderHook(() => useWorkflowRun("wf1"))
      await act(() => result.current.startRun({}))
      await flush()
      expect(reads).toBe(1)
      expect(requests()[1]).toBe("GET /api/workflows/wf1/runs/r1")
      await tick()
      expect(reads).toBe(2)
      expect(result.current.isWaiting).toBe(true)
      await tick()
      expect(result.current.runStatus).toBe("completed")
      expect(result.current.isWaiting).toBe(false)
      expect(result.current.finalOutputs).toEqual({ out: "approved" })
      expect(result.current.runDuration).toBe(4200)
      expect(result.current.nodeResults?.h_1).toMatchObject({ status: "completed", output: "ok" })
      expect(Object.keys(result.current.nodeResults ?? {})).toEqual(["start_1", "h_1", "end_1"])
      // Ended: no further reads.
      await tick()
      await tick()
      expect(reads).toBe(3)
    })

    it("follows the run when the stream ends without an outcome", async () => {
      fetchMock.mockImplementation(async (url: string) =>
        String(url).endsWith("/run")
          ? sseResponse(
              event("run_started", { run_id: "r1", status: "running" }) +
                event("node_started", { node_id: "llm_1" }),
            )
          : runRecord({ status: "failed", error: "Model unreachable", duration_ms: 9, node_results: { llm_1: { status: "failed", error: "Model unreachable" } } }),
      )
      const { result } = renderHook(() => useWorkflowRun("wf1"))
      await act(() => result.current.startRun({}))
      await flush()
      expect(requests()).toEqual(["POST /api/workflows/wf1/run", "GET /api/workflows/wf1/runs/r1"])
      expect(result.current.isRunning).toBe(false)
      expect(result.current.runStatus).toBe("failed")
      expect(result.current.finalError).toBe("Model unreachable")
      expect(result.current.nodeResults?.llm_1.status).toBe("failed")
    })

    it("cancels the run by its id", async () => {
      fetchMock.mockImplementation(async (url: string, init?: { method?: string }) => {
        if (String(url).endsWith("/run")) return waitingStream()
        if (init?.method === "POST") return runRecord({ status: "cancelled" })
        return runRecord({ status: fetchMock.mock.calls.some(([u]) => String(u).endsWith("/cancel")) ? "cancelled" : "waiting", waiting_for: WAITING })
      })
      const { result } = renderHook(() => useWorkflowRun("wf1"))
      await act(() => result.current.startRun({}))
      await flush()
      act(() => result.current.cancelRun())
      await flush()
      expect(requests()).toContain("POST /api/workflows/wf1/runs/r1/cancel")
      expect(requests().some((r) => r.includes("/runs/current/"))).toBe(false)
      expect(result.current.runStatus).toBe("cancelled")
      expect(result.current.isWaiting).toBe(false)
      expect(result.current.waitingFor).toEqual([])
      const before = fetchMock.mock.calls.length
      await tick()
      expect(fetchMock.mock.calls.length).toBe(before)
    })

    it("cancels a run that is cancelled before the server has named it, once it is named", async () => {
      let release!: () => void
      const gate = new Promise<void>((resolve) => (release = resolve))
      const bytes = new TextEncoder().encode(event("run_started", { run_id: "r9", status: "running" }))
      fetchMock.mockImplementation(async (url: string) => {
        if (!String(url).endsWith("/run")) return runRecord({ id: "r9", status: "cancelled" })
        let sent = false
        return {
          ok: true,
          status: 200,
          body: {
            getReader: () => ({
              read: async () => {
                if (sent) return new Promise(() => {})
                await gate
                sent = true
                return { done: false, value: bytes }
              },
            }),
          },
        }
      })
      const { result } = renderHook(() => useWorkflowRun("wf1"))
      act(() => {
        void result.current.startRun({})
      })
      await flush()
      act(() => result.current.cancelRun())
      expect(result.current.isRunning).toBe(false)
      expect(requests()).toEqual(["POST /api/workflows/wf1/run"])
      await act(async () => {
        release()
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(requests()).toContain("POST /api/workflows/wf1/runs/r9/cancel")
    })

    it("attaches to a run that was already in progress and follows it", async () => {
      fetchMock.mockImplementation(async () =>
        runRecord({ status: "completed", outputs: { out: 1 }, duration_ms: 10, node_results: { h_1: { status: "completed" } } }),
      )
      const { result } = renderHook(() => useWorkflowRun("wf1"))
      act(() =>
        result.current.attachToRun({
          id: "r1",
          workflow_id: "wf1",
          user_id: "u1",
          status: "waiting",
          inputs: {},
          outputs: null,
          node_results: { h_1: { status: "waiting" } } as never,
          started_at: null,
          completed_at: null,
          duration_ms: null,
          error: null,
          waiting_for: WAITING,
          created_at: "2026-01-01T00:00:00",
        }),
      )
      expect(result.current.runPanelOpen).toBe(true)
      expect(result.current.isWaiting).toBe(true)
      expect(result.current.waitingFor).toEqual(WAITING)
      expect(result.current.nodeResults?.h_1.status).toBe("waiting")
      await flush()
      expect(requests()).toEqual(["GET /api/workflows/wf1/runs/r1"])
      expect(result.current.runStatus).toBe("completed")
      expect(result.current.finalOutputs).toEqual({ out: 1 })
    })

    it("keeps showing the waiting run when the panel is opened again", async () => {
      fetchMock.mockImplementation(async (url: string) =>
        String(url).endsWith("/run") ? waitingStream() : runRecord({ status: "waiting", waiting_for: WAITING }),
      )
      const { result } = renderHook(() => useWorkflowRun("wf1"))
      await act(() => result.current.startRun({}))
      await flush()
      act(() => result.current.closeRunPanel())
      act(() => result.current.openRunPanel())
      expect(result.current.runPanelOpen).toBe(true)
      expect(result.current.isWaiting).toBe(true)
      expect(result.current.nodeResults).not.toBeNull()
    })

    it("leaving the page stops reading the run and does not cancel it", async () => {
      fetchMock.mockImplementation(async (url: string) =>
        String(url).endsWith("/run") ? waitingStream() : runRecord({ status: "waiting", waiting_for: WAITING }),
      )
      const { result, unmount } = renderHook(() => useWorkflowRun("wf1"))
      await act(() => result.current.startRun({}))
      await flush()
      unmount()
      const before = fetchMock.mock.calls.length
      await vi.advanceTimersByTimeAsync(6000)
      expect(fetchMock.mock.calls.length).toBe(before)
      expect(requests().some((r) => r.includes("/cancel"))).toBe(false)
    })
  })
})
