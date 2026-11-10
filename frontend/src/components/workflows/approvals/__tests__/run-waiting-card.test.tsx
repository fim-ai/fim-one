import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"

import workflows from "../../../../../messages/en/workflows.json"
import errors from "../../../../../messages/en/errors.json"
import { ApiError } from "@/lib/api"
import { RunWaitingCard } from "@/components/workflows/approvals/run-waiting-card"

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
vi.mock("@/hooks/use-date-formatter", () => ({
  useDateFormatter: () => ({ formatDateTime: (value: string) => `at ${value}` }),
}))

const WAITING = [
  { node_id: "h_1", confirmation_id: "c1", kind: "workflow_approval", expires_at: "2026-01-02T00:00:00" },
]
const inboxItem = {
  id: "c1",
  kind: "workflow_approval",
  status: "pending",
  title: "Release the payment?",
  description: "Invoice 42",
  workflow_id: "wf1",
  workflow_name: "Payments",
  run_id: "r1",
  run_mode: "manual",
  node_id: "h_1",
  step_label: "Finance sign-off",
  started_by: { id: "u1", name: "Ada" },
  action: null,
  arguments: null,
  created_at: "2026-01-01T00:00:00",
  expires_at: "2026-01-02T00:00:00",
}

function renderCard(onDecided = vi.fn()) {
  render(
    <NextIntlClientProvider locale="en" messages={{ workflows, errors }} timeZone="UTC">
      <RunWaitingCard waitingFor={WAITING} nodeLabel={() => "Human Review"} onDecided={onDecided} />
    </NextIntlClientProvider>,
  )
  return onDecided
}

beforeEach(() => vi.clearAllMocks())
afterEach(cleanup)

describe("RunWaitingCard", () => {
  it("offers Approve and Reject for a request in the user's inbox and sends the note", async () => {
    api.approvalInbox.mockResolvedValue({ items: [inboxItem], count: 1 })
    confirmations.respondToConfirmation.mockResolvedValue({ status: "approved", decided_at: "x" })
    const onDecided = renderCard()

    expect(screen.getByText("Waiting for a decision")).toBeTruthy()
    expect(await screen.findByText("Finance sign-off")).toBeTruthy()
    expect(screen.getByText("Release the payment?")).toBeTruthy()
    expect(screen.getByText("Expires at 2026-01-02T00:00:00")).toBeTruthy()
    expect(screen.getByRole("link", { name: "Open approvals" }).getAttribute("href")).toBe(
      "/workflows/approvals",
    )

    fireEvent.change(screen.getByLabelText("Note"), { target: { value: " looks right " } })
    fireEvent.click(screen.getByRole("button", { name: "Approve" }))
    await waitFor(() => expect(onDecided).toHaveBeenCalledTimes(1))
    expect(confirmations.respondToConfirmation).toHaveBeenCalledWith("c1", "approve", "looks right")
    expect(toast.success).toHaveBeenCalledWith("Approved. The run continues.")
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull()
  })

  it("says the run waits for someone else when the request is not in the user's inbox", async () => {
    api.approvalInbox.mockResolvedValue({ items: [], count: 0 })
    renderCard()
    expect(await screen.findByText("Waiting for someone else to decide.")).toBeTruthy()
    expect(screen.getByText("Human Review")).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull()
  })

  it("shows how a request ended when someone else decided first, without an error", async () => {
    api.approvalInbox.mockResolvedValue({ items: [inboxItem], count: 1 })
    confirmations.respondToConfirmation.mockRejectedValue(
      new ApiError(409, "Already decided", "confirmation_already_decided", { status: "rejected" }),
    )
    const onDecided = renderCard()
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }))
    expect(await screen.findByText("Already rejected.")).toBeTruthy()
    expect(toast.error).not.toHaveBeenCalled()
    expect(onDecided).toHaveBeenCalledTimes(1)
  })

  it("reports a refusal and leaves the request open", async () => {
    api.approvalInbox.mockResolvedValue({ items: [inboxItem], count: 1 })
    confirmations.respondToConfirmation.mockRejectedValue(
      new ApiError(403, "Forbidden", "confirmation_forbidden"),
    )
    renderCard()
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }))
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("You are not allowed to decide this approval request."),
    )
    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy()
  })
})
