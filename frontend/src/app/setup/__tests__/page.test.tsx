import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"
import { afterEach, describe, expect, it, vi } from "vitest"

import auth from "../../../../messages/en/auth.json"
import errors from "../../../../messages/en/errors.json"
import { ApiError } from "@/lib/api"
import SetupPage from "@/app/setup/page"

const TOKEN = "setup-secret-for-this-test"
const replace = vi.hoisted(() => vi.fn())
const authApi = vi.hoisted(() => ({
  setupStatus: vi.fn(),
  setup: vi.fn(),
}))

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  authApi,
}))

function renderPage() {
  render(
    <NextIntlClientProvider locale="en" messages={{ auth, errors }} timeZone="UTC">
      <SetupPage />
    </NextIntlClientProvider>,
  )
}

describe("setup page", () => {
  afterEach(() => {
    cleanup()
    localStorage.clear()
    vi.clearAllMocks()
    vi.unstubAllGlobals()
  })

  it("does not render while a session is already stored", async () => {
    localStorage.setItem("fim_access_token", "already-signed-in")
    renderPage()
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"))
    expect(authApi.setupStatus).not.toHaveBeenCalled()
  })

  it("asks for the setup secret and sends it only in the request body", async () => {
    authApi.setupStatus.mockResolvedValue({ initialized: false })
    authApi.setup.mockResolvedValue({
      access_token: "issued-access",
      refresh_token: "issued-refresh",
      user: { id: "u1", email: "root@example.test" },
    })
    renderPage()

    const secret = await screen.findByPlaceholderText("Setup token")
    expect(secret.getAttribute("type")).toBe("password")
    expect(secret.getAttribute("autocomplete")).toBe("off")

    fireEvent.change(secret, { target: { value: TOKEN } })
    fireEvent.change(screen.getByPlaceholderText("Email"), {
      target: { value: "root@example.test" },
    })
    const passwords = screen.getAllByPlaceholderText(/password/i)
    fireEvent.change(passwords[0], { target: { value: "long-enough-password" } })
    fireEvent.change(passwords[1], { target: { value: "long-enough-password" } })
    fireEvent.click(screen.getByRole("button", { name: "Create Admin Account" }))

    await waitFor(() => expect(authApi.setup).toHaveBeenCalledTimes(1))
    expect(authApi.setup).toHaveBeenCalledWith({
      email: "root@example.test",
      password: "long-enough-password",
      setup_token: TOKEN,
    })
    expect(localStorage.getItem("fim_access_token")).toBe("issued-access")
    expect(JSON.stringify(localStorage)).not.toContain(TOKEN)
    expect(document.body.textContent).not.toContain(TOKEN)
  })

  it("shows a mismatch without repeating the secret", async () => {
    authApi.setupStatus.mockResolvedValue({ initialized: false })
    authApi.setup.mockRejectedValue(new ApiError(403, "setup token invalid", "setup_token_invalid"))
    renderPage()

    fireEvent.change(await screen.findByPlaceholderText("Setup token"), { target: { value: TOKEN } })
    fireEvent.change(screen.getByPlaceholderText("Email"), {
      target: { value: "root@example.test" },
    })
    const passwords = screen.getAllByPlaceholderText(/password/i)
    fireEvent.change(passwords[0], { target: { value: "long-enough-password" } })
    fireEvent.change(passwords[1], { target: { value: "long-enough-password" } })
    fireEvent.click(screen.getByRole("button", { name: "Create Admin Account" }))

    expect(await screen.findByText("The setup token does not match.")).toBeTruthy()
    expect(document.body.textContent).not.toContain(TOKEN)
  })
})
