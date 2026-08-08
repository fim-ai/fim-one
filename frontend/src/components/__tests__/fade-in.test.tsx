import { describe, it, expect } from "vitest"
import { render, act } from "@testing-library/react"
import { StaggerOnce, StaggerItem } from "@/components/ui/fade-in"

/**
 * The contract worth protecting here is the "once" in StaggerOnce.
 *
 * List pages keep the container mounted while its children churn: filtering a
 * search box or turning a page unmounts every card and mounts a fresh set. If
 * the entrance ran for those too, a list would re-animate on every keystroke.
 * The tests below pin the entrance to the first run and assert that later
 * arrivals mount straight into their resting state.
 */

/** Long enough to cover the stagger plus the item duration. */
const SETTLE_MS = 600

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, SETTLE_MS))
  })
}

function styleOf(container: HTMLElement, testId: string): string {
  const el = container.querySelector(`[data-testid="${testId}"]`)
  if (!el) throw new Error(`no element with data-testid="${testId}"`)
  return el.getAttribute("style") ?? ""
}

describe("StaggerOnce", () => {
  it("starts its first children hidden and offset", () => {
    const { container } = render(
      <StaggerOnce>
        <StaggerItem data-testid="first">A</StaggerItem>
      </StaggerOnce>,
    )
    const style = styleOf(container, "first")
    expect(style).toContain("opacity: 0")
    expect(style).toContain("translateY")
  })

  it("settles those children into their resting state", async () => {
    const { container } = render(
      <StaggerOnce>
        <StaggerItem data-testid="first">A</StaggerItem>
      </StaggerOnce>,
    )
    await settle()
    const style = styleOf(container, "first")
    expect(style).toContain("opacity: 1")
    expect(style).not.toContain("translateY")
  })

  it("does not replay the entrance for children mounted after the first run", async () => {
    const { container, rerender } = render(
      <StaggerOnce>
        <StaggerItem data-testid="first">A</StaggerItem>
      </StaggerOnce>,
    )
    await settle()

    // Stands in for a search filter or page change swapping the list contents.
    rerender(
      <StaggerOnce>
        <StaggerItem data-testid="first">A</StaggerItem>
        <StaggerItem data-testid="late">B</StaggerItem>
      </StaggerOnce>,
    )

    // No opacity:0 frame: the new card is visible on the paint it appears in.
    const style = styleOf(container, "late")
    expect(style).toContain("opacity: 1")
    expect(style).not.toContain("opacity: 0")
    expect(style).not.toContain("translateY")
  })

  it("keeps the class names it is given, so grid layout survives", () => {
    const { container } = render(
      <StaggerOnce className="grid grid-cols-3 gap-4">
        <StaggerItem className="grid" data-testid="first">
          A
        </StaggerItem>
      </StaggerOnce>,
    )
    expect(container.firstElementChild?.className).toBe("grid grid-cols-3 gap-4")
    expect(
      container.querySelector('[data-testid="first"]')?.className,
    ).toBe("grid")
  })
})
