import { describe, expect, it } from "vitest"
import { completeIncompleteMarkdown, splitMarkdownBlocks } from "../streaming-markdown"

describe("splitMarkdownBlocks", () => {
  it("returns no blocks for empty input", () => {
    expect(splitMarkdownBlocks("")).toEqual([])
  })

  it("splits paragraphs separated by blank lines", () => {
    const blocks = splitMarkdownBlocks("first para\n\nsecond para")
    expect(blocks).toHaveLength(2)
    expect(blocks[0]).toContain("first para")
    expect(blocks[1]).toBe("second para")
  })

  it("reproduces the source when blocks are concatenated", () => {
    const md = "# Title\n\npara one\n\n- a\n- b\n\n```js\ncode()\n```\n\ntail"
    expect(splitMarkdownBlocks(md).join("")).toBe(md)
  })

  it("keeps a fenced code block with blank lines as one block", () => {
    const md = "```python\nline1\n\nline2\n```"
    expect(splitMarkdownBlocks(md)).toEqual([md])
  })

  it("keeps an unclosed fence open to the end of input", () => {
    const md = "para\n\n```js\nstill streaming"
    const blocks = splitMarkdownBlocks(md)
    expect(blocks).toHaveLength(2)
    expect(blocks[1]).toBe("```js\nstill streaming")
  })

  it("keeps a loose list (blank lines between items) as one block", () => {
    const md = "1. first\n\n2. second\n\n3. third"
    expect(splitMarkdownBlocks(md)).toEqual([md])
  })

  it("keeps a table as one block", () => {
    const md = "| a | b |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"
    expect(splitMarkdownBlocks(md)).toEqual([md])
  })

  it("never rewrites a completed block as the source grows (memo stability)", () => {
    const full =
      "# Heading\n\npara one with **bold** text\n\n- item a\n- item b\n\n" +
      "```js\nconst x = 1\n\nconst y = 2\n```\n\n" +
      "| a | b |\n| --- | --- |\n| 1 | 2 |\n\n> quote line\n\nfinal para"
    let prev: string[] = []
    for (let len = 1; len <= full.length; len++) {
      const blocks = splitMarkdownBlocks(full.slice(0, len))
      // All blocks except the last must match what we saw before, because
      // completed blocks are memoized by content and never re-rendered.
      for (let i = 0; i < Math.min(prev.length, blocks.length) - 1; i++) {
        expect(blocks[i]).toBe(prev[i])
      }
      prev = blocks
    }
  })
})

describe("completeIncompleteMarkdown", () => {
  it("leaves balanced text unchanged", () => {
    const md = "plain text with **bold**, `code`, ~~strike~~ and *italic*"
    expect(completeIncompleteMarkdown(md)).toBe(md)
  })

  it("closes an unterminated bold span", () => {
    expect(completeIncompleteMarkdown("this is **important stuff")).toBe(
      "this is **important stuff**"
    )
  })

  it("drops a bold marker that has no content yet", () => {
    expect(completeIncompleteMarkdown("this is **")).toBe("this is ")
  })

  it("closes an unterminated italic span", () => {
    expect(completeIncompleteMarkdown("some *emphasis here")).toBe("some *emphasis here*")
  })

  it("does not eat the closing of a bold span when trimming italics", () => {
    expect(completeIncompleteMarkdown("*a **b**")).toBe("*a **b***")
  })

  it("ignores bullet-list asterisks when balancing italics", () => {
    const md = "* item one\n* item two"
    expect(completeIncompleteMarkdown(md)).toBe(md)
  })

  it("closes an unterminated inline code span", () => {
    expect(completeIncompleteMarkdown("run `pnpm buil")).toBe("run `pnpm buil`")
  })

  it("closes an unterminated strikethrough span", () => {
    expect(completeIncompleteMarkdown("that is ~~wrong")).toBe("that is ~~wrong~~")
  })

  it("hides a dangling link opener until it completes", () => {
    expect(completeIncompleteMarkdown("see [the docs")).toBe("see ")
  })

  it("hides a link whose URL is still arriving", () => {
    expect(completeIncompleteMarkdown("see [docs](https://example.co")).toBe("see ")
  })

  it("hides a dangling image opener", () => {
    expect(completeIncompleteMarkdown("look ![alt text](http://img")).toBe("look ")
  })

  it("keeps completed links intact", () => {
    const md = "see [docs](https://example.com) for more"
    expect(completeIncompleteMarkdown(md)).toBe(md)
  })

  it("hides an unterminated display-math fragment", () => {
    expect(completeIncompleteMarkdown("as shown: $$\\frac{a}{b")).toBe("as shown: ")
  })

  it("keeps balanced display math intact", () => {
    const md = "$$x^2$$ and $$y^2$$"
    expect(completeIncompleteMarkdown(md)).toBe(md)
  })

  it("leaves a fenced-code tail block untouched", () => {
    const md = "```js\nconst s = \"**not markdown\"\n"
    expect(completeIncompleteMarkdown(md)).toBe(md)
  })
})
