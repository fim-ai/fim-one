import { describe, it, expect, vi, beforeAll } from "vitest"

vi.mock("next-intl", () => ({ useTranslations: () => (k: string) => k }))
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "dark" }) }))

const { MERMAID_INIT, SVG_SANITIZE_CONFIG } = await import("@/lib/markdown")

/**
 * Mermaid's label markup and our sanitize profile are coupled: DOMPurify lists
 * `foreignObject` as disallowed (right next to `script`), so the moment mermaid
 * renders HTML labels every node comes out as a correctly-sized but empty box.
 * These lock in the combination that keeps label text intact.
 */
describe("mermaid labels survive sanitization", () => {
  beforeAll(() => {
    // jsdom has no layout engine; mermaid needs these to measure labels.
    const proto = (globalThis as never as { SVGElement: { prototype: Record<string, unknown> } })
      .SVGElement.prototype
    proto.getBBox = () => ({ x: 0, y: 0, width: 120, height: 24 })
    proto.getComputedTextLength = () => 120
  })

  it("emits text labels, not foreignObject, and keeps CJK through DOMPurify", async () => {
    const mermaid = (await import("mermaid")).default
    const DOMPurify = (await import("dompurify")).default

    mermaid.initialize({ ...MERMAID_INIT, theme: "dark" })
    const { svg } = await mermaid.render(
      "labelprobe",
      "flowchart TD\n  A([用户下单]) --> B[创建订单]\n  B --> C{检查库存}"
    )

    expect(svg).not.toContain("<foreignObject")
    expect(svg).toContain("<text")

    const clean = DOMPurify.sanitize(svg, SVG_SANITIZE_CONFIG)
    for (const label of ["用户下单", "创建订单", "检查库存"]) {
      expect(clean).toContain(label)
    }
  })

  it("pins htmlLabels at the top level, where mermaid actually reads it", () => {
    // `config.htmlLabels ?? config.flowchart?.htmlLabels ?? true` — a nested
    // `flowchart.htmlLabels` is the deprecated spelling and does not apply.
    expect(MERMAID_INIT.htmlLabels).toBe(false)
    expect(MERMAID_INIT).not.toHaveProperty("flowchart")
  })
})
