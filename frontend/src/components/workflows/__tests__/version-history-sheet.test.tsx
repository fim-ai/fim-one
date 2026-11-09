import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"

import workflows from "../../../../messages/en/workflows.json"
import common from "../../../../messages/en/common.json"
import errors from "../../../../messages/en/errors.json"
import { VersionHistorySheet } from "@/components/workflows/version-history-sheet"

const api = vi.hoisted(() => ({ getVersions: vi.fn(), restoreVersion: vi.fn() }))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  workflowApi: api,
}))
vi.mock("sonner", () => ({ toast }))
// The real formatter reads the signed-in user's time zone from the auth context.
vi.mock("@/hooks/use-date-formatter", () => ({
  useDateFormatter: () => ({ formatRelativeTime: () => "a moment ago" }),
}))

const blueprint = { schema_version: 2, nodes: [], edges: [], viewport: { x: 0, y: 0, zoom: 1 } }
const version = (n: number) => ({
  id: `v${n}`,
  workflow_id: "wf1",
  version_number: n,
  blueprint,
  input_schema: null,
  output_schema: null,
  change_summary: null,
  created_by: null,
  created_at: "2026-01-01T00:00:00Z",
})

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
  api.getVersions.mockResolvedValue({ items: [version(2), version(1)], page: 1, pages: 1, total: 2, size: 20 })
})

afterEach(cleanup)

async function openAndConfirmRestore(props: Partial<Parameters<typeof VersionHistorySheet>[0]>) {
  render(
    <NextIntlClientProvider locale="en" messages={{ workflows, common, errors }} timeZone="UTC">
      <VersionHistorySheet
        workflowId="wf1"
        open
        onOpenChange={() => {}}
        onVersionRestored={() => {}}
        {...props}
      />
    </NextIntlClientProvider>,
  )
  // Only the earlier version can be restored.
  fireEvent.click(await screen.findByRole("button", { name: workflows.versionHistoryRestore }))
  const confirm = await screen.findByRole("alertdialog")
  fireEvent.click(within(confirm).getByRole("button", { name: workflows.versionHistoryRestore }))
}

describe("VersionHistorySheet restore", () => {
  it("lets the editor stop saving before it asks the server, then reports the restore", async () => {
    const order: string[] = []
    let release!: () => void
    const onBeforeRestore = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          order.push("before")
          release = () => {
            order.push("released")
            resolve()
          }
        }),
    )
    api.restoreVersion.mockImplementation(async () => {
      order.push("restore")
    })
    const onVersionRestored = vi.fn(() => order.push("restored"))
    const onRestoreFailed = vi.fn()

    await openAndConfirmRestore({ onBeforeRestore, onVersionRestored, onRestoreFailed })
    await waitFor(() => expect(onBeforeRestore).toHaveBeenCalledTimes(1))
    // The server is not asked while the editor is still saving.
    expect(api.restoreVersion).not.toHaveBeenCalled()
    release()
    await waitFor(() => expect(onVersionRestored).toHaveBeenCalledTimes(1))
    expect(order).toEqual(["before", "released", "restore", "restored"])
    expect(api.restoreVersion).toHaveBeenCalledWith("wf1", "v1")
    expect(onRestoreFailed).not.toHaveBeenCalled()
  })

  it("tells the editor when the restore failed, so it resumes saving", async () => {
    api.restoreVersion.mockRejectedValue(new Error("nope"))
    const onVersionRestored = vi.fn()
    const onRestoreFailed = vi.fn()
    await openAndConfirmRestore({ onBeforeRestore: vi.fn(), onVersionRestored, onRestoreFailed })
    await waitFor(() => expect(onRestoreFailed).toHaveBeenCalledTimes(1))
    expect(onVersionRestored).not.toHaveBeenCalled()
    expect(toast.error).toHaveBeenCalledWith(workflows.versionHistoryRestoreFailed)
  })
})
