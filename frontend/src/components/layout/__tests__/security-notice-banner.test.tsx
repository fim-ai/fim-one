import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"

import securityNotice from "../../../../messages/en/securityNotice.json"
import layout from "../../../../messages/en/layout.json"
import { NoticeBody } from "@/components/layout/security-notice-banner"
import type { SecurityNotice } from "@/types/auth"

const api = vi.hoisted(() => ({
  apiFetch: vi.fn(async () => ({})),
  dismiss: vi.fn(async () => ({})),
  restore: vi.fn(async () => ({})),
  resume: vi.fn(async () => ({})),
}))

vi.mock("@/lib/api", () => ({
  apiFetch: api.apiFetch,
  authApi: { dismissSecurityNotice: api.dismiss, restoreFromSecurityNotice: api.restore },
  workflowApi: { resumeAccess: api.resume },
}))
vi.mock("@/contexts/auth-context", () => ({ useAuth: () => ({ user: null, updateUser: vi.fn() }) }))

const notice: SecurityNotice = {
  at: "2026-11-12T10:00:00+00:00",
  sign_in: { password_removed: true, api_keys: [{ id: "k1", name: "ci" }] },
  items: [
    { kind: "workflow_access", count: 1, items: [{ id: "wf1", name: "Nightly" }] },
    { kind: "connectors", count: 1, items: [{ id: "cn1", name: "CRM" }] },
    { kind: "credentials_removed", count: 1, items: [{ id: "cn2", name: "Shared" }] },
    { kind: "memberships", count: 1, items: [{ id: "o1", name: "Team", role: "admin" }] },
  ],
  restorable: ["system_instructions"],
}

function renderNotice(onDismissed = vi.fn()) {
  render(
    <NextIntlClientProvider locale="en" messages={{ securityNotice, layout }} timeZone="UTC">
      <NoticeBody notice={notice} onDismissed={onDismissed} />
    </NextIntlClientProvider>,
  )
  return onDismissed
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("security notice", () => {
  it("lists what was switched off and turns an item back on", async () => {
    renderNotice()
    fireEvent.click(screen.getByText("Details"))
    expect(screen.getByText(/Password removed/)).toBeTruthy()
    expect(screen.getByText(/credentials for these shared tools were deleted: Shared/)).toBeTruthy()
    expect(screen.getByText(/Team \(admin\)/)).toBeTruthy()

    fireEvent.click(screen.getByText(/Nightly: Turn back on/))
    await waitFor(() => expect(api.resume).toHaveBeenCalledWith("wf1"))
    fireEvent.click(screen.getByText(/CRM: Turn back on/))
    await waitFor(() =>
      expect(api.apiFetch).toHaveBeenCalledWith("/api/connectors/cn1", {
        method: "PUT",
        body: JSON.stringify({ is_active: true }),
      }),
    )
    fireEvent.click(screen.getByText(/ci: Turn back on/))
    await waitFor(() =>
      expect(api.apiFetch).toHaveBeenCalledWith("/api/me/api-keys/k1/active", {
        method: "PATCH",
        body: JSON.stringify({ is_active: true }),
      }),
    )
  })

  it("restores kept settings and can be dismissed", async () => {
    const onDismissed = renderNotice()
    fireEvent.click(screen.getByText("Restore system instructions"))
    await waitFor(() => expect(api.restore).toHaveBeenCalledWith("system_instructions"))
    fireEvent.click(screen.getByLabelText("Dismiss"))
    await waitFor(() => expect(onDismissed).toHaveBeenCalled())
  })
})
