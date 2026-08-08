import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import { act, renderHook, waitFor } from "@testing-library/react"
import { useFaviconLoading } from "@/hooks/use-favicon-loading"

// ---------------------------------------------------------------------------
// Helpers — jsdom ships no canvas, no HTMLImageElement.decode and no
// matchMedia, so stand all three up. The stubs keep the hook's real control
// flow intact: each toDataURL() call returns a distinct href, which is what
// lets the assertions below tell one animation frame from the next.
// ---------------------------------------------------------------------------

const REAL_ICON_HREF = "/icon.svg?abc123"
const SVG_SOURCE = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 104 85.8"></svg>'

let drawCount = 0

function installCanvasStub(): void {
  drawCount = 0
  const ctx = {
    filter: "none",
    globalAlpha: 1,
    clearRect: vi.fn(),
    drawImage: vi.fn(() => {
      drawCount += 1
    }),
  }
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
    ctx as unknown as CanvasRenderingContext2D,
  )
  // One unique href per frame, so a cycling favicon is observable.
  vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockImplementation(
    () => `data:image/png;base64,frame-${drawCount}`,
  )
}

function installImageStub(): void {
  class StubImage {
    src = ""
    naturalWidth = 104
    naturalHeight = 86
    decode(): Promise<void> {
      return Promise.resolve()
    }
  }
  vi.stubGlobal("Image", StubImage)
}

function installMatchMedia(reduceMotion: boolean): void {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({ matches: reduceMotion, media: query })),
  )
}

function setIconLink(href: string): HTMLLinkElement {
  const link = document.createElement("link")
  link.rel = "icon"
  link.setAttribute("href", href)
  document.head.appendChild(link)
  return link
}

function currentHref(): string | null {
  const links = document.head.querySelectorAll<HTMLLinkElement>("link[rel~='icon']")
  return links.length > 0 ? links[links.length - 1].getAttribute("href") : null
}

/** Resolves once the hook has swapped in its first generated frame. */
async function waitForBusyFavicon(): Promise<void> {
  await waitFor(() => {
    expect(currentHref()).toMatch(/^data:image\/png/)
  })
}

describe("useFaviconLoading", () => {
  beforeEach(() => {
    document.head.innerHTML = ""
    installCanvasStub()
    installImageStub()
    installMatchMedia(false)
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(SVG_SOURCE, { status: 200 }))),
    )
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it("leaves the favicon alone when not loading", async () => {
    setIconLink(REAL_ICON_HREF)
    renderHook(({ loading }) => useFaviconLoading(loading), {
      initialProps: { loading: false },
    })

    await act(async () => {})
    expect(currentHref()).toBe(REAL_ICON_HREF)
    expect(fetch).not.toHaveBeenCalled()
  })

  it("swaps in a generated frame while loading", async () => {
    setIconLink(REAL_ICON_HREF)
    renderHook(({ loading }) => useFaviconLoading(loading), {
      initialProps: { loading: true },
    })

    await waitForBusyFavicon()
    // The mark is redrawn from the real icon, not replaced by a glyph.
    expect(fetch).toHaveBeenCalledWith(REAL_ICON_HREF)
    expect(drawCount).toBeGreaterThan(1)
  })

  it("advances through frames on a timer", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    setIconLink(REAL_ICON_HREF)
    renderHook(({ loading }) => useFaviconLoading(loading), {
      initialProps: { loading: true },
    })

    await waitForBusyFavicon()
    const first = currentHref()
    await act(async () => {
      vi.advanceTimersByTime(300)
    })
    expect(currentHref()).not.toBe(first)
  })

  it("restores the original favicon when the turn ends", async () => {
    setIconLink(REAL_ICON_HREF)
    const { rerender } = renderHook(({ loading }) => useFaviconLoading(loading), {
      initialProps: { loading: true },
    })

    await waitForBusyFavicon()
    await act(async () => {
      rerender({ loading: false })
    })
    expect(currentHref()).toBe(REAL_ICON_HREF)
  })

  it("restores the original favicon on unmount", async () => {
    setIconLink(REAL_ICON_HREF)
    const { unmount } = renderHook(({ loading }) => useFaviconLoading(loading), {
      initialProps: { loading: true },
    })

    await waitForBusyFavicon()
    await act(async () => {
      unmount()
    })
    expect(currentHref()).toBe(REAL_ICON_HREF)
  })

  it("restores the href Next re-inserted mid-turn, not the stale one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    setIconLink(REAL_ICON_HREF)
    const { unmount } = renderHook(({ loading }) => useFaviconLoading(loading), {
      initialProps: { loading: true },
    })

    await waitForBusyFavicon()
    // Next re-renders <head> and appends a fresh link with a new cache key.
    const rotated = "/icon.svg?def456"
    setIconLink(rotated)
    await act(async () => {
      vi.advanceTimersByTime(200)
    })
    await act(async () => {
      unmount()
    })
    expect(currentHref()).toBe(rotated)
  })

  it("holds a single static frame under prefers-reduced-motion", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    installMatchMedia(true)
    setIconLink(REAL_ICON_HREF)
    renderHook(({ loading }) => useFaviconLoading(loading), {
      initialProps: { loading: true },
    })

    await waitForBusyFavicon()
    const still = currentHref()
    // Still distinct from idle, so the busy cue survives...
    expect(still).not.toBe(REAL_ICON_HREF)
    await act(async () => {
      vi.advanceTimersByTime(3000)
    })
    // ...but it never advances to another frame.
    expect(currentHref()).toBe(still)
  })

  it("leaves the favicon untouched when the icon cannot be fetched", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response("nope", { status: 404 }))),
    )
    setIconLink(REAL_ICON_HREF)
    renderHook(({ loading }) => useFaviconLoading(loading), {
      initialProps: { loading: true },
    })

    await act(async () => {})
    expect(currentHref()).toBe(REAL_ICON_HREF)
  })
})
