import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"

import kb from "../../../../messages/en/kb.json"
import common from "../../../../messages/en/common.json"
import { TooltipProvider } from "@/components/ui/tooltip"
import { DocumentTable } from "@/components/kb/document-table"
import type { KBDocumentResponse } from "@/types/kb"

const api = vi.hoisted(() => ({ retryDocument: vi.fn() }))

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  kbApi: api,
}))

const doc = (over: Partial<KBDocumentResponse> = {}): KBDocumentResponse => ({
  id: "d1",
  kb_id: "kb1",
  filename: "scan.pdf",
  file_size: 10,
  file_type: "pdf",
  chunk_count: 2,
  status: "ready",
  error_message: null,
  vision_notice: null,
  created_at: "2026-01-01T00:00:00Z",
  ...over,
})

function renderTable(documents: KBDocumentResponse[], onRetried = vi.fn()) {
  const view = (docs: KBDocumentResponse[]) => (
    <NextIntlClientProvider locale="en" messages={{ kb, common }} timeZone="UTC">
      <TooltipProvider>
        <DocumentTable
          kbId="kb1"
          documents={docs}
          onDeleteDocument={vi.fn()}
          onRetried={onRetried}
        />
      </TooltipProvider>
    </NextIntlClientProvider>
  )
  const result = render(view(documents))
  return { ...result, rerenderWith: (docs: KBDocumentResponse[]) => result.rerender(view(docs)) }
}

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
  api.retryDocument.mockResolvedValue({ status: "processing" })
})
afterEach(cleanup)

describe("DocumentTable vision notice", () => {
  it("shows no notice on a document read with the vision model", () => {
    renderTable([doc()])
    expect(screen.queryByRole("img", { name: "Read as text only" })).toBeNull()
    expect(screen.queryByText("Read again with image recognition")).toBeNull()
  })

  it.each([
    ["token_quota_exceeded", /your token quota was used up/],
    ["subscription_required", /no plan that includes model use/],
    ["quota_unavailable", /could not be checked/],
  ])("explains a document read as text only under %s", async (code, text) => {
    renderTable([doc({ vision_notice: code })])
    const icon = screen.getByRole("img", { name: "Read as text only" })
    await act(async () => {
      fireEvent.focus(icon)
    })
    expect((await screen.findAllByText(text)).length).toBeGreaterThan(0)
  })

  it("offers a retry that shows processing until the list is fetched again", async () => {
    const onRetried = vi.fn()
    const noticed = doc({ vision_notice: "token_quota_exceeded" })
    const { container, rerenderWith } = renderTable([noticed], onRetried)

    const retry = container.querySelector("button")
    expect(retry).not.toBeNull()
    await act(async () => {
      fireEvent.click(retry!)
    })

    expect(api.retryDocument).toHaveBeenCalledWith("kb1", "d1")
    expect(onRetried).toHaveBeenCalledOnce()
    expect(screen.getByText("processing")).toBeTruthy()
    expect(screen.queryByRole("img", { name: "Read as text only" })).toBeNull()

    // The fetched list says what the server now holds.
    rerenderWith([doc()])
    expect(screen.getByText("ready")).toBeTruthy()
    expect(screen.queryByRole("img", { name: "Read as text only" })).toBeNull()
  })
})
