import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"

import workflows from "../../../../messages/en/workflows.json"
import common from "../../../../messages/en/common.json"
import errors from "../../../../messages/en/errors.json"
import organizations from "../../../../messages/en/organizations.json"
import { ApiError } from "@/lib/api"
import WorkflowsPage from "@/app/workflows/page"

const api = vi.hoisted(() => ({
  list: vi.fn(),
  publish: vi.fn(),
  unpublish: vi.fn(),
  delete: vi.fn(),
  resubmit: vi.fn(),
}))
const orgs = vi.hoisted(() => ({ list: vi.fn() }))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }))

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  workflowApi: api,
  orgApi: orgs,
}))
vi.mock("sonner", () => ({ toast }))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/workflows",
}))
// One object for every render: the page reloads its list when `user` changes.
const auth = vi.hoisted(() => ({ user: { id: "u1" }, isLoading: false }))
vi.mock("@/contexts/auth-context", () => ({ useAuth: () => auth }))
vi.mock("@/lib/module-flags", () => ({ useModuleGuard: () => true }))
vi.mock("@/components/workflows/approvals/approvals-link", () => ({ ApprovalsLink: () => null }))
vi.mock("@/components/workflows/template-gallery-dialog", () => ({
  TemplateGalleryDialog: () => null,
}))
// The card's menu and the dialog's organisation picker are Radix overlays;
// the page's handlers are what is under test, so both are reduced to buttons.
vi.mock("@/components/workflows/workflow-card", () => ({
  WorkflowCard: (p: {
    workflow: { id: string }
    onPublish: (id: string) => void
    onUnpublish: (id: string) => void
    onDelete: (id: string) => void
    onResubmit: (id: string) => void
  }) => (
    <div>
      <button onClick={() => p.onPublish(p.workflow.id)}>card-publish</button>
      <button onClick={() => p.onUnpublish(p.workflow.id)}>card-unpublish</button>
      <button onClick={() => p.onDelete(p.workflow.id)}>card-delete</button>
      <button onClick={() => p.onResubmit(p.workflow.id)}>card-resubmit</button>
    </div>
  ),
}))
vi.mock("@/components/shared/publish-dialog", () => ({
  PublishDialog: (p: { open: boolean; onConfirm: () => void }) =>
    p.open ? <button onClick={p.onConfirm}>dialog-publish</button> : null,
}))

const refusal = () =>
  new ApiError(
    400,
    "Human Intervention step 'Yi Review': Approval assignee 't2yi' is not a member",
    "approval_assignee_not_in_org",
    { step: "Yi Review", assignee: "t2yi" },
  )

const flush = () =>
  act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve()
  })

async function renderPage() {
  render(
    <NextIntlClientProvider
      locale="en"
      messages={{ workflows, common, errors, organizations }}
      timeZone="UTC"
    >
      <WorkflowsPage />
    </NextIntlClientProvider>,
  )
  await flush()
}

beforeEach(() => {
  vi.clearAllMocks()
  api.list.mockResolvedValue({
    items: [{ id: "wf1", name: "flow", description: null, user_id: "u1", visibility: "personal" }],
  })
  orgs.list.mockResolvedValue([{ id: "org1", name: "Org", review_workflows: false }])
})
afterEach(cleanup)

describe("workflow list: a refused action says why", () => {
  // Publishing from the list showed "Failed to publish workflow" for a
  // refusal that names the step and the assignee at fault; the editor
  // showed the full sentence for the same code.
  it("names the step and the assignee when a publish is refused", async () => {
    api.publish.mockRejectedValue(refusal())
    await renderPage()
    fireEvent.click(screen.getByText("card-publish"))
    await flush()
    fireEvent.click(screen.getByText("dialog-publish"))
    await flush()

    expect(api.publish).toHaveBeenCalledWith("wf1", { scope: "org", org_id: "org1" })
    expect(toast.error).toHaveBeenCalledTimes(1)
    const title = String(toast.error.mock.calls[0][0])
    expect(title).toContain("Yi Review")
    expect(title).toContain("t2yi")
    // Coded refusals share an id, so a repeat replaces the toast.
    expect(toast.error.mock.calls[0][1]).toMatchObject({ id: "approval_assignee_not_in_org" })
  })

  it("falls back to the plain message when the failure has no code", async () => {
    api.publish.mockRejectedValue(new Error("network"))
    await renderPage()
    fireEvent.click(screen.getByText("card-publish"))
    await flush()
    fireEvent.click(screen.getByText("dialog-publish"))
    await flush()

    expect(toast.error).toHaveBeenCalledWith(workflows.workflowPublishFailed)
  })

  it("shows the refusal's own message for a refused resubmit", async () => {
    api.resubmit.mockRejectedValue(
      new ApiError(400, "Only rejected submissions can be resubmitted", "not_rejected"),
    )
    await renderPage()
    fireEvent.click(screen.getByText("card-resubmit"))

    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
    expect(toast.error.mock.calls[0][0]).toBe(errors.not_rejected)
  })

  it("shows the refusal's own message for a refused unpublish", async () => {
    api.unpublish.mockRejectedValue(new ApiError(403, "denied", "unpublish_denied"))
    await renderPage()
    fireEvent.click(screen.getByText("card-unpublish"))
    fireEvent.click(await screen.findByRole("button", { name: common.unpublish }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
    expect(toast.error.mock.calls[0][0]).toBe(errors.unpublish_denied)
  })

  it("shows the refusal's own message for a refused delete", async () => {
    api.delete.mockRejectedValue(new ApiError(404, "Workflow not found", "workflow_not_found"))
    await renderPage()
    fireEvent.click(screen.getByText("card-delete"))
    fireEvent.click(await screen.findByRole("button", { name: common.delete }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
    expect(toast.error.mock.calls[0][0]).toBe(errors.workflow_not_found)
  })
})
