import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, render, waitFor } from "@testing-library/react"

import { ConversationProvider, useConversation } from "@/contexts/conversation-context"

const api = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
}))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  conversationApi: api,
}))
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
const auth = vi.hoisted(() => ({ user: { id: "u1" }, isLoading: false }))
vi.mock("@/contexts/auth-context", () => ({ useAuth: () => auth }))

type Ctx = ReturnType<typeof useConversation>
let ctx: Ctx | null = null
function Probe() {
  ctx = useConversation()
  return null
}

const conv = (id: string, business_ref: string | null = null) => ({
  id,
  title: "",
  mode: "react",
  agent_id: null,
  status: "active",
  model_name: null,
  total_tokens: 0,
  starred: false,
  business_ref,
  created_at: "2026-11-12T00:00:00Z",
  updated_at: null,
})

describe("ConversationProvider business reference", () => {
  beforeEach(() => {
    api.list.mockResolvedValue({ items: [], total: 0, page: 1, size: 20, pages: 0 })
    api.create.mockImplementation(async (body: { business_ref?: string }) =>
      conv("new", body.business_ref ?? null),
    )
  })
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    ctx = null
  })

  it("lists everything until a reference is set, then only that reference", async () => {
    render(
      <ConversationProvider>
        <Probe />
      </ConversationProvider>,
    )
    await waitFor(() => expect(api.list).toHaveBeenCalledWith(1, 20, undefined, undefined))

    act(() => ctx!.setBusinessRef("HT-1"))
    await waitFor(() => expect(api.list).toHaveBeenLastCalledWith(1, 20, undefined, "HT-1"))

    act(() => ctx!.setBusinessRef(null))
    await waitFor(() => expect(api.list).toHaveBeenLastCalledWith(1, 20, undefined, undefined))
  })

  it("creates conversations carrying the reference while it is set", async () => {
    render(
      <ConversationProvider>
        <Probe />
      </ConversationProvider>,
    )
    await act(async () => {
      await ctx!.createConversation("react", "first")
    })
    expect(api.create).toHaveBeenLastCalledWith(
      expect.objectContaining({ business_ref: undefined }),
    )

    act(() => ctx!.setBusinessRef("HT-1"))
    await act(async () => {
      await ctx!.createConversation("react", "second")
    })
    expect(api.create).toHaveBeenLastCalledWith(
      expect.objectContaining({ mode: "react", title: "second", business_ref: "HT-1" }),
    )
    expect(ctx!.activeConversation?.business_ref).toBe("HT-1")
  })
})
