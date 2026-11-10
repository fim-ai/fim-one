import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"

import workflows from "../../../../messages/en/workflows.json"
import common from "../../../../messages/en/common.json"
import errors from "../../../../messages/en/errors.json"
import { ApiError } from "@/lib/api"
import { RunHistorySheet } from "@/components/workflows/run-history-sheet"

const api = vi.hoisted(() => ({ getRuns: vi.fn(), getRun: vi.fn(), deleteRun: vi.fn() }))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  workflowApi: api,
}))
vi.mock("sonner", () => ({ toast }))
vi.mock("@/hooks/use-date-formatter", () => ({
  useDateFormatter: () => ({ formatRelativeTime: () => "a moment ago" }),
}))

const run = (id: string, status: string, over: Record<string, unknown> = {}) => ({
  id,
  workflow_id: "wf1",
  user_id: "u1",
  status,
  inputs: { topic: id },
  outputs: null,
  node_results: null,
  started_at: null,
  completed_at: null,
  duration_ms: null,
  error: null,
  created_at: "2026-01-01T00:00:00Z",
  ...over,
})
const page = (items: unknown[]) => ({ items, page: 1, pages: 1, total: items.length, size: 20 })

beforeAll(() => {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub)
})

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] })
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

function renderSheet() {
  render(
    <NextIntlClientProvider locale="en" messages={{ workflows, common, errors }} timeZone="UTC">
      <RunHistorySheet workflowId="wf1" open onOpenChange={() => {}} nodeTypeMap={{}} />
    </NextIntlClientProvider>,
  )
}
const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve() })
const tick = () => act(async () => { await vi.advanceTimersByTimeAsync(3000) })

describe("RunHistorySheet", () => {
  // A run that waits for a decision had no badge (its status was unknown
  // to the sheet) and the list was read once, on opening.
  it("shows a waiting run as waiting and reads the list again until nothing can change", async () => {
    api.getRuns
      .mockResolvedValueOnce(page([run("r2", "waiting"), run("r1", "completed")]))
      .mockResolvedValueOnce(page([run("r2", "waiting"), run("r1", "completed")]))
      .mockResolvedValue(page([run("r2", "completed"), run("r1", "completed")]))
    renderSheet()
    await flush()
    expect(screen.getByText("Waiting")).toBeTruthy()
    expect(screen.getAllByText("Completed")).toHaveLength(1)
    expect(api.getRuns).toHaveBeenCalledTimes(1)

    await tick()
    expect(api.getRuns).toHaveBeenCalledTimes(2)
    expect(screen.getByText("Waiting")).toBeTruthy()

    await tick()
    expect(api.getRuns).toHaveBeenCalledTimes(3)
    expect(screen.queryByText("Waiting")).toBeNull()
    expect(screen.getAllByText("Completed")).toHaveLength(2)

    // Every run has ended: the list is left alone.
    await tick()
    await tick()
    expect(api.getRuns).toHaveBeenCalledTimes(3)
  })

  it("does not read the list again when every run has ended", async () => {
    api.getRuns.mockResolvedValue(page([run("r1", "completed"), run("r0", "failed")]))
    renderSheet()
    await flush()
    await tick()
    expect(api.getRuns).toHaveBeenCalledTimes(1)
  })

  it("offers Delete for runs that have ended only, not for a waiting or running one", async () => {
    api.getRuns.mockResolvedValue(
      page([run("r3", "waiting"), run("r2", "running"), run("r1", "completed")]),
    )
    renderSheet()
    await flush()
    expect(screen.getAllByRole("button", { name: "Delete run" })).toHaveLength(1)
  })

  it("says why when the server refuses to delete a run that is still in progress", async () => {
    api.getRuns.mockResolvedValue(page([run("r1", "completed")]))
    api.deleteRun.mockRejectedValue(new ApiError(409, "active", "cannot_delete_active_run"))
    renderSheet()
    await flush()
    fireEvent.click(screen.getByRole("button", { name: "Delete run" }))
    await flush()
    expect(toast.error).toHaveBeenCalledWith("A run that is still in progress cannot be deleted.")
    expect(screen.getByText("Completed")).toBeTruthy()
  })

  it("shows the steps of a run as its record stores them", async () => {
    api.getRuns.mockResolvedValue(page([run("r1", "waiting")]))
    api.getRun.mockResolvedValue(
      run("r1", "waiting", {
        node_results: {
          start_1: { node_id: "start_1", status: "completed", output_preview: "short output" },
          h_1: { node_id: "h_1", status: "waiting" },
        },
      }),
    )
    renderSheet()
    await flush()
    fireEvent.click(screen.getByText("Waiting"))
    await flush()
    expect(screen.getByText("start_1")).toBeTruthy()
    expect(screen.getByText("h_1")).toBeTruthy()
    expect(screen.getByText("short output")).toBeTruthy()
  })
})
