import { describe, it, expect, vi } from "vitest"
import { render } from "@testing-library/react"

vi.mock("next-intl", () => ({ useTranslations: () => (k: string) => k }))
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "light" }) }))

const { MarkdownContent } = await import("@/lib/markdown")

const html = (md: string) => render(<MarkdownContent content={md} />).container.innerHTML

/**
 * The pipeline runs rehype-raw, so raw HTML from model output or an uploaded
 * .md file becomes real DOM. These assert the sanitize pass neutralizes it
 * without breaking anything the trusted remark plugins emit.
 */
describe("markdown sanitization blocks injected markup", () => {
  it("drops script tags", () => {
    const out = html("before\n\n<script>window.__pwned = 1</script>\n\nafter")
    expect(out).not.toContain("<script")
    expect(out).toContain("before")
    expect(out).toContain("after")
  })

  it("drops inline event handler attributes", () => {
    const out = html('<img src="x" onerror="window.__pwned = 1" alt="a">')
    expect(out.toLowerCase()).not.toContain("onerror")
  })

  it("drops iframes", () => {
    expect(html('<iframe src="https://evil.example"></iframe>')).not.toContain("<iframe")
  })

  it("drops style tags and inline styles", () => {
    const out = html('<style>body{display:none}</style><div style="position:fixed;inset:0">x</div>')
    expect(out).not.toContain("<style")
    expect(out).not.toContain("position:fixed")
  })

  it("drops form controls used for credential phishing", () => {
    const out = html('<form action="https://evil.example"><input name="password"></form>')
    expect(out).not.toContain("<form")
  })

  it("neutralizes javascript: URLs", () => {
    expect(html("[click](javascript:window.__pwned=1)").toLowerCase()).not.toContain(
      "href=\"javascript:"
    )
  })

  it("drops object and embed tags", () => {
    const out = html('<object data="evil.swf"></object><embed src="evil.swf">')
    expect(out).not.toContain("<object")
    expect(out).not.toContain("<embed")
  })
})

describe("sanitization preserves trusted rendering", () => {
  it("still renders inline and display math", () => {
    const out = html("inline $a^2$ and display\n\n$$\\frac{1}{2}$$")
    // rehype-katex runs after sanitize; its output must be intact
    expect(out).toContain("katex")
  })

  it("still applies syntax highlighting classes", () => {
    const out = html("```python\nprint(1)\n```")
    expect(out).toContain("hljs")
    expect(out).toContain("language-python")
  })

  it("still renders GFM tables", () => {
    // Four columns so this takes the grid path rather than the card layout.
    const out = html("| a | b | c | d |\n| --- | --- | --- | --- |\n| 1 | 2 | 3 | 4 |\n")
    expect(out).toContain("<table")
  })

  it("still renders task list checkboxes", () => {
    const out = html("- [x] done\n- [ ] todo\n")
    expect(out).toContain('type="checkbox"')
  })

  it("still renders strikethrough", () => {
    expect(html("~~gone~~")).toContain("<del")
  })

  it("keeps line breaks inside table cells", () => {
    const out = html("| a | b |\n| --- | --- |\n| one<br>two | 2 |\n| x | y |\n")
    expect(out).toContain("<br")
  })

  it("keeps details and summary disclosure blocks", () => {
    const out = html("<details><summary>more</summary>\n\nhidden\n\n</details>")
    expect(out).toContain("<details")
    expect(out).toContain("<summary")
  })

  it("keeps links with safe protocols", () => {
    expect(html("[docs](https://example.com)")).toContain('href="https://example.com"')
  })
})
