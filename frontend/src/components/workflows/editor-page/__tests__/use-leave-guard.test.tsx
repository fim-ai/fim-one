import { afterEach, describe, expect, it } from "vitest"
import { act, cleanup, renderHook } from "@testing-library/react"

import { inAppDestination, useLeaveGuard } from "@/components/workflows/editor-page/use-leave-guard"

/**
 * The editor's "unsaved changes" dialog existed but nothing opened it:
 * "Back to Workflows" or a sidebar link dropped the edits of the last few
 * seconds without a word.
 */

const here = { origin: "http://localhost:3000", pathname: "/workflows/wf1", href: "http://localhost:3000/workflows/wf1" }
const plainClick = { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, defaultPrevented: false }

function link(href: string, attrs: Record<string, string> = {}): HTMLAnchorElement {
  const a = document.createElement("a")
  a.setAttribute("href", href)
  for (const [k, v] of Object.entries(attrs)) a.setAttribute(k, v)
  // A link's content, as a click usually lands on an icon or a label inside it.
  a.appendChild(document.createElement("span")).textContent = "go"
  document.body.appendChild(a)
  return a
}

function click(el: Element, init: MouseEventInit = {}): MouseEvent {
  const event = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0, ...init })
  el.dispatchEvent(event)
  return event
}

afterEach(() => {
  cleanup()
  document.body.innerHTML = ""
})

describe("inAppDestination", () => {
  it("is the path of a link to another page of the app", () => {
    expect(inAppDestination(plainClick, link("/workflows"), here)).toBe("/workflows")
    expect(inAppDestination(plainClick, link("/agents?tab=mine#top"), here)).toBe("/agents?tab=mine#top")
    expect(inAppDestination(plainClick, link("http://localhost:3000/kb"), here)).toBe("/kb")
  })

  it("lets through what does not leave the page in this tab", () => {
    expect(inAppDestination(plainClick, null, here)).toBeNull()
    expect(inAppDestination(plainClick, link("/workflows/wf1?run=true"), here)).toBeNull()
    expect(inAppDestination(plainClick, link("#section"), here)).toBeNull()
    expect(inAppDestination(plainClick, link("https://docs.example.com/"), here)).toBeNull()
    expect(inAppDestination(plainClick, link("/agents", { target: "_blank" }), here)).toBeNull()
    expect(inAppDestination(plainClick, link("/file.json", { download: "" }), here)).toBeNull()
    expect(inAppDestination({ ...plainClick, metaKey: true }, link("/agents"), here)).toBeNull()
    expect(inAppDestination({ ...plainClick, ctrlKey: true }, link("/agents"), here)).toBeNull()
    expect(inAppDestination({ ...plainClick, button: 1 }, link("/agents"), here)).toBeNull()
    expect(inAppDestination({ ...plainClick, defaultPrevented: true }, link("/agents"), here)).toBeNull()
  })
})

describe("useLeaveGuard", () => {
  it("holds a link click back while there are unsaved changes, and remembers where it led", () => {
    const { result } = renderHook(() => useLeaveGuard(() => true))
    const a = link("/workflows")
    let event!: MouseEvent
    act(() => {
      event = click(a.querySelector("span")!)
    })
    expect(event.defaultPrevented).toBe(true)
    expect(result.current.pendingHref).toBe("/workflows")
    act(() => result.current.stay())
    expect(result.current.pendingHref).toBeNull()
  })

  it("stops the click before the link's own handler runs", () => {
    renderHook(() => useLeaveGuard(() => true))
    const a = link("/agents")
    let handled = false
    a.addEventListener("click", () => {
      handled = true
    })
    act(() => {
      click(a)
    })
    expect(handled).toBe(false)
  })

  it("does nothing when there is nothing unsaved", () => {
    const { result } = renderHook(() => useLeaveGuard(() => false))
    let event!: MouseEvent
    act(() => {
      event = click(link("/workflows"))
    })
    expect(event.defaultPrevented).toBe(false)
    expect(result.current.pendingHref).toBeNull()
  })

  it("asks at the moment of the click, so an edit made an instant ago counts", () => {
    let unsaved = false
    const { result } = renderHook(() => useLeaveGuard(() => unsaved))
    // No re-render between the edit and the click.
    unsaved = true
    act(() => {
      click(link("/workflows"))
    })
    expect(result.current.pendingHref).toBe("/workflows")
    act(() => result.current.stay())
    unsaved = false
    let event!: MouseEvent
    act(() => {
      event = click(link("/agents"))
    })
    expect(event.defaultPrevented).toBe(false)
    expect(result.current.pendingHref).toBeNull()
  })

  it("does not ask the question for a click that would not leave", () => {
    let asked = 0
    renderHook(() =>
      useLeaveGuard(() => {
        asked += 1
        return true
      }),
    )
    act(() => {
      click(link("#section"))
      click(document.body)
    })
    expect(asked).toBe(0)
  })

  it("lets new-tab clicks and external links through", () => {
    const { result } = renderHook(() => useLeaveGuard(() => true))
    act(() => {
      click(link("/agents"), { metaKey: true })
      click(link("https://docs.example.com/"))
    })
    expect(result.current.pendingHref).toBeNull()
  })
})
