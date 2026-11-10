import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, render, screen } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"

import workflows from "../../../../../messages/en/workflows.json"
import { announceApprovalsChanged, vanishedRequests } from "@/lib/workflow-approvals"
import { ApprovalsLink } from "@/components/workflows/approvals/approvals-link"

const api = vi.hoisted(() => ({ approvalInboxCount: vi.fn() }))
const flags = vi.hoisted(() => ({ humanIntervention: false }))

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  workflowApi: api,
}))
vi.mock("@/lib/module-flags", () => ({
  useModuleFlags: () => ({ skills: false, workflows: true, loaded: true, ...flags }),
}))

function renderLink() {
  render(
    <NextIntlClientProvider locale="en" messages={{ workflows }} timeZone="UTC">
      <ApprovalsLink />
    </NextIntlClientProvider>,
  )
}
const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0) })

beforeEach(() => {
  vi.clearAllMocks()
  flags.humanIntervention = false
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "setTimeout", "clearTimeout"] })
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("ApprovalsLink", () => {
  it("is not shown when Human Review is off and nothing waits", async () => {
    api.approvalInboxCount.mockResolvedValue(0)
    renderLink()
    await flush()
    expect(screen.queryByTestId("approvals-link")).toBeNull()
  })

  it("is shown with the count when something waits, even with Human Review off", async () => {
    api.approvalInboxCount.mockResolvedValue(2)
    renderLink()
    await flush()
    const link = screen.getByRole("link", { name: /Approvals/ })
    expect(link.getAttribute("href")).toBe("/workflows/approvals")
    expect(screen.getByTestId("approvals-count").textContent).toBe("2")
  })

  it("is shown without a badge when Human Review is on and nothing waits", async () => {
    flags.humanIntervention = true
    api.approvalInboxCount.mockResolvedValue(0)
    renderLink()
    await flush()
    expect(screen.getByRole("link", { name: "Approvals" })).toBeTruthy()
    expect(screen.queryByTestId("approvals-count")).toBeNull()
  })

  it("reads the count every 30 s and right after a decision", async () => {
    flags.humanIntervention = true
    api.approvalInboxCount.mockResolvedValue(1)
    renderLink()
    await flush()
    expect(api.approvalInboxCount).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(api.approvalInboxCount).toHaveBeenCalledTimes(2)

    api.approvalInboxCount.mockResolvedValue(0)
    await act(async () => {
      announceApprovalsChanged()
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(api.approvalInboxCount).toHaveBeenCalledTimes(3)
    expect(screen.queryByTestId("approvals-count")).toBeNull()
  })
})

describe("vanishedRequests", () => {
  it("names the shown requests a fresh read no longer has, except the handled ones", () => {
    const shown = [{ id: "a" }, { id: "b" }, { id: "c" }]
    expect(vanishedRequests(shown, [{ id: "b" }], new Set(["c"]))).toEqual([{ id: "a" }])
    expect(vanishedRequests(shown, shown, new Set())).toEqual([])
  })
})
