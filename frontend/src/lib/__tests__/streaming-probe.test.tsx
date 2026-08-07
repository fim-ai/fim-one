import { describe, it, expect, vi, beforeEach } from "vitest"
import { render } from "@testing-library/react"
import { StrictMode } from "react"

vi.mock("next-intl", () => ({ useTranslations: () => (k: string) => k }))
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "dark" }) }))

const mermaidMock = vi.hoisted(() => ({
  initialize: vi.fn(),
  parse: vi.fn(),
  render: vi.fn(),
}))
vi.mock("mermaid", () => ({ default: mermaidMock }))
vi.mock("dompurify", () => ({ default: { sanitize: (h: string) => h } }))

const { MarkdownContent } = await import("@/lib/markdown")

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/**
 * Replay a string in chunks with real elapsed time between them, so the
 * renderers' internal debounce actually fires *mid-stream* the way it does in
 * the browser. Re-rendering faster than the debounce (the naive version of this
 * helper) only ever lets it fire once, at the end, and hides the bug.
 */
async function streamInto(full: string, { chunk = 6, gapMs = 40 } = {}) {
  const wrap = (s: string) => (
    <StrictMode>
      <MarkdownContent content={s} />
    </StrictMode>
  )
  const { rerender } = render(wrap(full.slice(0, chunk)))
  for (let i = chunk * 2; i <= full.length + chunk; i += chunk) {
    rerender(wrap(full.slice(0, i)))
    await sleep(gapMs)
  }
  await sleep(300)
}

const FLOWCHART = `Here is the flow:

\`\`\`mermaid
graph TD
  A[下单] --> B{库存充足?}
  B -->|否| C[缺货提示]
  B -->|是| D{支付成功?}
  D -->|否| E[支付失败]
  D -->|是| F[发货]
\`\`\`

That is the whole process.`

describe("streaming does not corrupt the DOM tree", () => {
  beforeEach(() => {
    mermaidMock.parse.mockReset()
    mermaidMock.render.mockReset()
  })

  it("survives a mermaid flowchart streamed at browser cadence", async () => {
    // Any prefix that happens to be syntactically valid renders; later chunks
    // then invalidate it again. That flip-flop is what the browser hits.
    mermaidMock.parse.mockImplementation(async (src: string) => {
      if (!/graph TD\s*\n\s*\w/.test(src)) throw new Error("Parse error")
      return true
    })
    mermaidMock.render.mockImplementation(async (id: string, src: string) => ({
      svg: `<svg id="${id}"><g>${src.length}</g></svg>`,
    }))

    await expect(streamInto(FLOWCHART)).resolves.not.toThrow()
  })

  it("survives an svg fence streamed at browser cadence", async () => {
    await expect(
      streamInto('Chart:\n\n```svg\n<svg id="f"><rect width="1" height="1" /></svg>\n```\n\ndone')
    ).resolves.not.toThrow()
  })

  it("survives a plain code fence streamed at browser cadence", async () => {
    await expect(
      streamInto("Code:\n\n```python\nprint(1)\nprint(2)\n```\n\ndone")
    ).resolves.not.toThrow()
  })
})
