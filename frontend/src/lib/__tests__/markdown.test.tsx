import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, waitFor } from "@testing-library/react"
import { EvidenceProvider } from "@/contexts/evidence-context"
import type { ParsedSource } from "@/lib/evidence-utils"

const mermaidMock = vi.hoisted(() => ({
  initialize: vi.fn(),
  parse: vi.fn(),
  render: vi.fn(),
}))
const purifyMock = vi.hoisted(() => ({ sanitize: vi.fn((html: string) => html) }))
vi.mock("mermaid", () => ({ default: mermaidMock }))
vi.mock("dompurify", () => ({ default: purifyMock }))

// next-intl and next-themes both need providers we do not care about here;
// stub them so the markdown renderer can be exercised in isolation.
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}))
vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: "light" }),
}))

const { MarkdownContent } = await import("@/lib/markdown")

/** Build a GFM table with `rows` body rows and `cols` data columns. */
function makeTable(rows: number, cols: number): string {
  const head = ["项目", ...Array.from({ length: cols }, (_, i) => `产品${i + 1}`)]
  const lines = [
    `| ${head.join(" | ")} |`,
    `| ${head.map(() => "---").join(" | ")} |`,
  ]
  for (let r = 0; r < rows; r++) {
    lines.push(`| 属性${r + 1} | ${Array.from({ length: cols }, (_, c) => `值${r + 1}-${c + 1}`).join(" | ")} |`)
  }
  return lines.join("\n")
}

describe("MarkdownContent tables", () => {
  it("renders a small comparison table as cards, repeating the label per column", () => {
    const { container } = render(<MarkdownContent content={makeTable(3, 2)} />)

    // Card layout replaces the <table> element entirely
    expect(container.querySelector("table")).toBeNull()
    // Each attribute label is repeated once per data column
    const labels = [...container.querySelectorAll("div")].filter(
      (el) => el.textContent === "属性1"
    )
    expect(labels).toHaveLength(2)
  })

  it("falls back to a grid table when there are too many rows", () => {
    const { container } = render(<MarkdownContent content={makeTable(15, 2)} />)

    const table = container.querySelector("table")
    expect(table).not.toBeNull()
    expect(table?.querySelectorAll("tbody tr")).toHaveLength(15)
  })

  it("falls back to a grid table when there are too many columns", () => {
    const { container } = render(<MarkdownContent content={makeTable(3, 4)} />)
    expect(container.querySelector("table")).not.toBeNull()
  })

  it("falls back to a grid table when the first column is not a short label", () => {
    const long = "超".repeat(40)
    const md = [
      "| 项目 | A | B |",
      "| --- | --- | --- |",
      `| ${long} | 1 | 2 |`,
      `| ${long} | 3 | 4 |`,
    ].join("\n")
    expect(render(<MarkdownContent content={md} />).container.querySelector("table")).not.toBeNull()
  })
})

describe("MarkdownContent code blocks", () => {
  it("shows the language in the code block header", () => {
    const { container } = render(
      <MarkdownContent content={"```python\nprint(1)\n```"} />
    )
    expect(container.textContent).toContain("python")
    expect(container.querySelector("pre")).not.toBeNull()
  })

  it("labels a fence with no language as text", () => {
    const { container } = render(<MarkdownContent content={"```\nplain\n```"} />)
    expect(container.textContent).toContain("text")
  })
})

describe("MarkdownContent diagrams", () => {
  beforeEach(() => {
    mermaidMock.parse.mockReset()
    mermaidMock.render.mockReset()
    purifyMock.sanitize.mockClear()
  })

  it("keeps showing code while the diagram source is still incomplete", async () => {
    // Streaming hands us a few characters at a time; every partial state throws.
    mermaidMock.parse.mockRejectedValue(new Error("Parse error"))
    const { container } = render(<MarkdownContent content={"```mermaid\ngraph TD; A--\n```"} />)

    await waitFor(() => expect(mermaidMock.parse).toHaveBeenCalled(), { timeout: 2000 })
    expect(container.querySelector("pre")).not.toBeNull()
    expect(mermaidMock.render).not.toHaveBeenCalled()
  })

  it("renders the diagram once the source parses", async () => {
    mermaidMock.parse.mockResolvedValue(true)
    mermaidMock.render.mockResolvedValue({ svg: '<svg id="diagram"><g /></svg>' })
    const { container } = render(<MarkdownContent content={"```mermaid\ngraph TD; A-->B\n```"} />)

    await waitFor(() => expect(container.querySelector("#diagram")).not.toBeNull(), { timeout: 2000 })
    expect(container.querySelector("pre")).toBeNull()
  })

  it("sanitizes rendered diagram markup before injecting it", async () => {
    mermaidMock.parse.mockResolvedValue(true)
    mermaidMock.render.mockResolvedValue({ svg: "<svg />" })
    render(<MarkdownContent content={"```mermaid\ngraph TD; A-->B\n```"} />)

    await waitFor(() => expect(purifyMock.sanitize).toHaveBeenCalled(), { timeout: 2000 })
    expect(purifyMock.sanitize).toHaveBeenCalledWith(
      "<svg />",
      expect.objectContaining({ USE_PROFILES: expect.objectContaining({ svg: true }) })
    )
  })

  it("sanitizes an svg fence and renders it as a figure", async () => {
    const svg = '<svg id="figure" viewBox="0 0 1 1"><rect width="1" height="1" /></svg>'
    const { container } = render(<MarkdownContent content={"```svg\n" + svg + "\n```"} />)

    await waitFor(() => expect(container.querySelector("#figure")).not.toBeNull(), { timeout: 2000 })
    expect(purifyMock.sanitize).toHaveBeenCalledWith(
      expect.stringContaining("<svg"),
      expect.objectContaining({ USE_PROFILES: expect.objectContaining({ svg: true }) })
    )
  })
})

describe("MarkdownContent citations", () => {
  const sources: ParsedSource[] = [
    { index: 1, name: "a/spec.md", displayName: "spec.md", relevance: 0.9, quote: "q1" },
    { index: 2, name: "b/notes.md", displayName: "notes.md", relevance: 0.8, quote: "q2" },
  ]

  it("collapses an adjacent citation run into one named pill", () => {
    const { container } = render(
      <EvidenceProvider sources={sources}>
        <MarkdownContent content="结论如此[1][2]。" />
      </EvidenceProvider>
    )
    const pills = container.querySelectorAll("button")
    expect(pills).toHaveLength(1)
    expect(pills[0].textContent).toContain("spec.md")
    expect(pills[0].textContent).toContain("+1")
  })

  it("keeps separate pills for citations that are not adjacent", () => {
    const { container } = render(
      <EvidenceProvider sources={sources}>
        <MarkdownContent content="先看这个[1]，再看那个[2]。" />
      </EvidenceProvider>
    )
    expect(container.querySelectorAll("button")).toHaveLength(2)
  })

  it("does not swallow the word after a citation", () => {
    const { container } = render(
      <EvidenceProvider sources={sources}>
        <MarkdownContent content="see [1] and more" />
      </EvidenceProvider>
    )
    expect(container.textContent).toContain("and more")
  })

  it("falls back to a bare marker when no source matches", () => {
    const { container } = render(<MarkdownContent content="无出处[7]。" />)
    expect(container.querySelectorAll("button")).toHaveLength(0)
    expect(container.querySelector("sup")?.textContent).toBe("7")
  })
})

describe("MarkdownContent math", () => {
  const katex = (container: HTMLElement) => container.querySelectorAll(".katex")

  it("renders a formula written with LaTeX display delimiters", () => {
    const { container } = render(
      <MarkdownContent content={"其价格页显示：\n\n\\[ 20\\ credits/秒 \\times 5秒=100\\ credits \\]\n"} />
    )
    // The visible layer carries the typeset glyphs; the TeX source survives
    // only inside KaTeX's MathML annotation, which is not what the user sees.
    expect(container.querySelector(".katex-html")?.textContent).toContain("×")
    expect(katex(container).length).toBeGreaterThan(0)
  })

  it("renders a formula written with LaTeX inline delimiters", () => {
    const { container } = render(<MarkdownContent content={"成本是 \\(P \\div 10\\) 元。"} />)
    expect(container.querySelector(".katex-html")?.textContent).toContain("÷")
    expect(katex(container).length).toBeGreaterThan(0)
  })

  it("still renders dollar-delimited math", () => {
    const { container } = render(<MarkdownContent content={"$$a^2 + b^2 = c^2$$"} />)
    expect(katex(container).length).toBeGreaterThan(0)
  })

  it("leaves LaTeX delimiters inside a code fence alone", () => {
    const { container } = render(<MarkdownContent content={"```tex\n\\[ x \\]\n```"} />)
    expect(katex(container)).toHaveLength(0)
    expect(container.textContent).toContain("\\[ x \\]")
  })

  it("leaves an unmatched opening delimiter as text", () => {
    const { container } = render(<MarkdownContent content={"开头 \\[ 20 credits"} />)
    expect(katex(container)).toHaveLength(0)
  })
})
