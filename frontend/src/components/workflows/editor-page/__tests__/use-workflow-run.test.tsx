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
})
