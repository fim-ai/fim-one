import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"

import workflows from "../../../../../messages/en/workflows.json"
import errors from "../../../../../messages/en/errors.json"
import { ApiError } from "@/lib/api"
import { APPROVALS_CHANGED_EVENT } from "@/lib/workflow-approvals"
import WorkflowApprovalsPage from "@/app/workflows/approvals/page"

const api = vi.hoisted(() => ({ approvalInbox: vi.fn() }))
const confirmations = vi.hoisted(() => ({
  respondToConfirmation: vi.fn(),
  getConfirmationStatus: vi.fn(),
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  workflowApi: api,
}))
vi.mock("@/lib/api/confirmations", () => confirmations)
vi.mock("sonner", () => ({ toast }))
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }))
vi.mock("@/contexts/auth-context", () => ({
  useAuth: () => ({ user: { id: "u1" }, isLoading: false }),
}))
vi.mock("@/lib/module-flags", () => ({ useModuleGuard: () => true }))
vi.mock("@/hooks/use-date-formatter", () => ({
  useDateFormatter: () => ({
    formatRelativeTime: () => "3 minutes ago",
    formatDateTime: (value: string) => `at ${value}`,
  }),
}))
// Motion wrappers add nothing to what is being tested.
vi.mock("@/components/ui/fade-in", () => ({
  StaggerOnce: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  StaggerItem: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

const approval = {
  id: "c1",
  kind: "workflow_approval",
  status: "pending",
  title: "Release the payment?",
  description: "Invoice 42",
  workflow_id: "wf1",
  workflow_name: "Payments",
  run_id: "r1",
  run_mode: "interactive",
  node_id: "h_1",
  step_label: "Finance sign-off",
  started_by: { id: "u2", name: "Ada" },
  action: null,
  arguments: null,
  created_at: "2026-01-01T00:00:00",
  expires_at: "2026-01-02T00:00:00",
}
const confirmation = {
  ...approval,
  id: "c2",
  kind: "confirmation",
  title: "Ledger.wipe",
  description: null,
  workflow_name: "Cleanup",
  workflow_id: "wf2",
  run_mode: "schedule",
  node_id: "connector_1",
  step_label: null,
  action: "Ledger.wipe",
  arguments: { ledger: "2025", confirm: true },
  expires_at: null,
}
const inbox = (...items: unknown[]) => ({ items, count: items.length })

function renderPage() {
  render(
    <NextIntlClientProvider locale="en" messages={{ workflows, errors }} timeZone="UTC">
      <WorkflowApprovalsPage />
    </NextIntlClientProvider>,
  )
}
const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0) })
const cards = () => screen.queryAllByTestId("approval-card")
const card = (text: string) =>
  cards().find((el) => within(el).queryByText(text)) as HTMLElement

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "setTimeout", "clearTimeout"] })
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("approval inbox page", () => {
  it("lists what waits for the user: workflow, step, who started the run, the message or the action", async () => {
    api.approvalInbox.mockResolvedValue(inbox(approval, confirmation))
    renderPage()
    await flush()
    expect(cards()).toHaveLength(2)

    const first = within(card("Payments"))
    expect(first.getByRole("link", { name: "Payments" }).getAttribute("href")).toBe("/workflows/wf1")
    expect(first.getByText("Step: Finance sign-off")).toBeTruthy()
    expect(first.getByText("Human Review")).toBeTruthy()
    expect(first.getByText("Release the payment?")).toBeTruthy()
    expect(first.getByText("Invoice 42")).toBeTruthy()
    expect(first.getByText("Started by Ada")).toBeTruthy()
    expect(first.getByText("Requested 3 minutes ago")).toBeTruthy()
    expect(first.getByText("Expires at 2026-01-02T00:00:00")).toBeTruthy()

    const second = within(card("Cleanup"))
    expect(second.getByText("Step: connector_1")).toBeTruthy()
    expect(second.getByText("Connector action")).toBeTruthy()
    expect(second.getByText("Ledger.wipe")).toBeTruthy()
    expect(second.getByText(/"ledger": "2025"/)).toBeTruthy()
    expect(second.getByText("Started by a schedule, as Ada")).toBeTruthy()
  })

  it("shows an empty state when nothing waits", async () => {
    api.approvalInbox.mockResolvedValue(inbox())
    renderPage()
    await flush()
    expect(screen.getByText("Nothing waits for your decision")).toBeTruthy()
  })

  it("approving with a note removes the card, says so and tells the count badges", async () => {
    api.approvalInbox.mockResolvedValue(inbox(approval))
    confirmations.respondToConfirmation.mockResolvedValue({ status: "approved", decided_at: "x" })
    const changed = vi.fn()
    window.addEventListener(APPROVALS_CHANGED_EVENT, changed)
    renderPage()
    await flush()
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "ok by me" } })
    fireEvent.click(screen.getByRole("button", { name: "Approve" }))
    await flush()
    expect(confirmations.respondToConfirmation).toHaveBeenCalledWith("c1", "approve", "ok by me")
    expect(toast.success).toHaveBeenCalledWith("Approved. The run continues.")
    expect(cards()).toHaveLength(0)
    expect(changed).toHaveBeenCalledTimes(1)
    window.removeEventListener(APPROVALS_CHANGED_EVENT, changed)

    // The next read no longer has it; that is not "decided elsewhere".
    api.approvalInbox.mockResolvedValue(inbox())
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(confirmations.getConfirmationStatus).not.toHaveBeenCalled()
    expect(screen.getByText("Nothing waits for your decision")).toBeTruthy()
  })

  it("shows how a request ended when someone else decided first, with no error toast, until dismissed", async () => {
    api.approvalInbox.mockResolvedValue(inbox(approval))
    confirmations.respondToConfirmation.mockRejectedValue(
      new ApiError(409, "Already decided", "confirmation_already_decided", { status: "approved" }),
    )
    renderPage()
    await flush()
    fireEvent.click(screen.getByRole("button", { name: "Reject" }))
    await flush()
    expect(screen.getByText("Already approved.")).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull()
    expect(toast.error).not.toHaveBeenCalled()

    // It stays through later reads of the inbox, without asking again.
    api.approvalInbox.mockResolvedValue(inbox())
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000) })
    expect(screen.getByText("Already approved.")).toBeTruthy()
    expect(confirmations.getConfirmationStatus).not.toHaveBeenCalled()
    expect(toast.error).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }))
    expect(cards()).toHaveLength(0)
  })

  it("notices on the next read that a request was decided on another surface", async () => {
    api.approvalInbox.mockResolvedValueOnce(inbox(approval, confirmation))
    api.approvalInbox.mockResolvedValue(inbox(confirmation))
    confirmations.getConfirmationStatus.mockResolvedValue({ status: "rejected" })
    renderPage()
    await flush()
    expect(api.approvalInbox).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(api.approvalInbox).toHaveBeenCalledTimes(2)
    expect(confirmations.getConfirmationStatus).toHaveBeenCalledWith("c1")
    expect(within(card("Payments")).getByText("Already rejected.")).toBeTruthy()
    expect(within(card("Cleanup")).getByRole("button", { name: "Approve" })).toBeTruthy()
    // Asked once, not on every read.
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(confirmations.getConfirmationStatus).toHaveBeenCalledTimes(1)
  })

  it("reads the inbox again when the window gets focus", async () => {
    api.approvalInbox.mockResolvedValue(inbox())
    renderPage()
    await flush()
    api.approvalInbox.mockResolvedValue(inbox(approval))
    await act(async () => {
      window.dispatchEvent(new Event("focus"))
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(cards()).toHaveLength(1)
  })

  it("reports a refusal with its message and keeps the card", async () => {
    api.approvalInbox.mockResolvedValue(inbox(approval))
    confirmations.respondToConfirmation.mockRejectedValue(
      new ApiError(404, "Not found", "confirmation_not_found"),
    )
    renderPage()
    await flush()
    fireEvent.click(screen.getByRole("button", { name: "Approve" }))
    await flush()
    expect(toast.error).toHaveBeenCalledWith("This approval request no longer exists.")
    expect(cards()).toHaveLength(1)
  })
})
