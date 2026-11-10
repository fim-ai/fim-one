import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"

import workflows from "../../../../messages/en/workflows.json"
import common from "../../../../messages/en/common.json"
import errors from "../../../../messages/en/errors.json"
import { BatchRunDialog } from "@/components/workflows/batch-run-dialog"
import { TestNodeDialog } from "@/components/workflows/test-node-dialog"

const api = vi.hoisted(() => ({ testNode: vi.fn(), batchRun: vi.fn() }))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  workflowApi: api,
}))
vi.mock("sonner", () => ({ toast }))

beforeAll(() => {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub)
})
beforeEach(() => vi.clearAllMocks())
afterEach(cleanup)

const wrap = (ui: React.ReactNode) =>
  render(
    <NextIntlClientProvider locale="en" messages={{ workflows, common, errors }} timeZone="UTC">
      {ui}
    </NextIntlClientProvider>,
  )
const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() })

// Test Node and Batch Run execute the workflow the server has stored. Both
// used to go straight to the server, so an edit made in the last few
// seconds (before the automatic save) was not in what ran.
describe("Test Node saves the canvas first", () => {
  const dialog = (beforeRun: () => Promise<boolean>) =>
    wrap(
      <TestNodeDialog
        workflowId="wf1"
        nodeId="llm_1"
        nodeType="llm"
        nodeLabel="LLM"
        open
        onOpenChange={() => {}}
        beforeRun={beforeRun}
      />,
    )

  it("saves, then tests", async () => {
    const order: string[] = []
    api.testNode.mockImplementation(async () => {
      order.push("test")
      return { status: "completed", output: "ok", duration_ms: 1, variables: {} }
    })
    dialog(async () => {
      order.push("save")
      return true
    })
    fireEvent.click(screen.getByRole("button", { name: workflows.testNodeRun }))
    await flush()
    expect(order).toEqual(["save", "test"])
  })

  it("does not test when the save failed", async () => {
    dialog(async () => false)
    fireEvent.click(screen.getByRole("button", { name: workflows.testNodeRun }))
    await flush()
    expect(api.testNode).not.toHaveBeenCalled()
    expect((screen.getByRole("button", { name: workflows.testNodeRun }) as HTMLButtonElement).disabled).toBe(false)
  })
})

describe("Batch Run saves the canvas first", () => {
  const dialog = (beforeRun: () => Promise<boolean>) =>
    wrap(<BatchRunDialog workflowId="wf1" open onOpenChange={() => {}} beforeRun={beforeRun} />)
  const type = () =>
    fireEvent.change(screen.getByRole("textbox"), { target: { value: '[{"q": "a"}]' } })

  it("saves, then starts the batch", async () => {
    const order: string[] = []
    api.batchRun.mockImplementation(async () => {
      order.push("batch")
      return { batch_id: "b", total: 1, results: [] }
    })
    dialog(async () => {
      order.push("save")
      return true
    })
    type()
    fireEvent.click(screen.getByRole("button", { name: workflows.batchRunStartButton }))
    await flush()
    expect(order).toEqual(["save", "batch"])
  })

  it("does not start when the save failed, and stays on the input", async () => {
    dialog(async () => false)
    type()
    fireEvent.click(screen.getByRole("button", { name: workflows.batchRunStartButton }))
    await flush()
    expect(api.batchRun).not.toHaveBeenCalled()
    expect(screen.queryByRole("button", { name: workflows.batchRunStartButton })).not.toBeNull()
  })
})
