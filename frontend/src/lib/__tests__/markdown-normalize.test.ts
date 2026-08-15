import { describe, it, expect } from "vitest"
import { normalizeHeadings, normalizeMathDelimiters } from "@/lib/markdown-normalize"

describe("normalizeHeadings", () => {
  it("inserts the space CommonMark needs before CJK heading text", () => {
    expect(normalizeHeadings("###标题")).toBe("### 标题")
    expect(normalizeHeadings("### already spaced")).toBe("### already spaced")
  })
})

describe("normalizeMathDelimiters", () => {
  it("rewrites display delimiters", () => {
    expect(normalizeMathDelimiters("\\[ a \\times b \\]")).toBe("$$a \\times b$$")
  })

  it("rewrites inline delimiters", () => {
    expect(normalizeMathDelimiters("成本 \\( P \\div 10 \\) 元")).toBe("成本 $P \\div 10$ 元")
  })

  it("keeps a multi-line formula on its own lines", () => {
    expect(normalizeMathDelimiters("\\[\na = b\n\\]")).toBe("$$\na = b\n$$")
  })

  it("leaves text with no LaTeX delimiters untouched", () => {
    const md = "普通段落 $x$ 和 `code`"
    expect(normalizeMathDelimiters(md)).toBe(md)
  })

  it("does not touch fenced code", () => {
    const md = "```tex\n\\[ x \\]\n```"
    expect(normalizeMathDelimiters(md)).toBe(md)
  })

  it("does not touch an unclosed fence's body", () => {
    const md = "```\n\\[ x \\]"
    expect(normalizeMathDelimiters(md)).toBe(md)
  })

  it("does not touch inline code spans", () => {
    const md = "写作 `\\[ x \\]` 即可"
    expect(normalizeMathDelimiters(md)).toBe(md)
  })

  it("leaves matrix row spacing inside existing display math alone", () => {
    const md = "$$\\begin{matrix} a \\\\[2pt] b \\end{matrix}$$"
    expect(normalizeMathDelimiters(md)).toBe(md)
  })

  it("leaves an unmatched opening delimiter for the next streaming frame", () => {
    expect(normalizeMathDelimiters("开头 \\[ 20 credits")).toBe("开头 \\[ 20 credits")
  })

  it("rewrites every pair in a document, prose in between preserved", () => {
    const md = "一：\n\n\\[ a \\]\n\n因此：\n\n\\[ b \\]\n"
    expect(normalizeMathDelimiters(md)).toBe("一：\n\n$$a$$\n\n因此：\n\n$$b$$\n")
  })

  it("preserves surrounding lines when a fence is present", () => {
    const md = "前\n\n```js\nconst a = 1\n```\n\n\\[ x \\]\n"
    expect(normalizeMathDelimiters(md)).toBe("前\n\n```js\nconst a = 1\n```\n\n$$x$$\n")
  })
})
