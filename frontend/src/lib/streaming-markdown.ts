/**
 * Streaming-markdown helpers: split a growing markdown source into stable
 * top-level blocks, and clean up the half-arrived syntax at the tail.
 *
 * The contract that makes per-block memoization safe: as the source string
 * grows by appending, every block except the last never changes again. The
 * lexer guarantees this because a new top-level token can only ever extend or
 * follow the final one — earlier token boundaries are sealed by the blank
 * lines / structure already consumed.
 */

import { Lexer } from "marked"

/**
 * Split markdown into top-level block strings (paragraphs, lists, fenced
 * code, tables, headings, …). Blank-line runs are folded into the preceding
 * block so the concatenation of all blocks reproduces the source and block
 * strings stay byte-stable as the source grows.
 */
export function splitMarkdownBlocks(markdown: string): string[] {
  if (!markdown) return []
  const blocks: string[] = []
  for (const token of Lexer.lex(markdown, { gfm: true })) {
    if (token.type === "space" && blocks.length > 0) {
      blocks[blocks.length - 1] += token.raw
      continue
    }
    blocks.push(token.raw)
  }
  return blocks
}

/**
 * If `marker` occurs an odd number of times, the last one is unterminated:
 * close it — unless nothing follows it yet, in which case appending a closer
 * would render a stray empty pair, so drop the dangling marker instead.
 */
function closeOrTrim(text: string, marker: string, count: number): string {
  if (count % 2 === 0) return text
  const trimmed = text.trimEnd()
  if (trimmed.endsWith(marker)) return trimmed.slice(0, trimmed.length - marker.length)
  return text + marker
}

/**
 * Stabilise the still-growing tail block for display. Without this, inline
 * markup flickers between literal and rendered form as its closing marker
 * arrives: `**bold` shows two asterisks for a few frames, `[label](long-url`
 * sits as raw text until the paren lands, an odd `$$` feeds KaTeX a fragment
 * that renders as an error. Unclosed emphasis/code spans are auto-closed;
 * constructs that cannot be meaningfully completed (links, display math) are
 * hidden until they finish arriving.
 *
 * Only ever applied to the tail block — completed blocks render verbatim.
 */
export function completeIncompleteMarkdown(text: string): string {
  // A fenced code block renders identically whether or not the closing fence
  // has arrived, and its body must not be touched.
  if (/^\s*(`{3,}|~{3,})/.test(text)) return text

  let out = text

  // Dangling link/image, both `[label…` and `[label](url…` forms.
  out = out.replace(/!?\[[^\]]*$/, "")
  out = out.replace(/!?\[[^\]]*\]\([^)]*$/, "")

  // Odd number of `$$` fences: hide the fragment from KaTeX.
  const mathParts = out.split("$$")
  if (mathParts.length % 2 === 0) out = mathParts.slice(0, -1).join("$$")

  // Inline code span. (A double-backtick span that is still open counts as
  // even and slips through — rare enough to accept.)
  out = closeOrTrim(out, "`", (out.match(/`/g) ?? []).length)

  // Bold / strikethrough before italic, so `**bold` is not double-counted as
  // two italic markers.
  out = closeOrTrim(out, "**", (out.match(/\*\*/g) ?? []).length)
  out = closeOrTrim(out, "~~", (out.match(/~~/g) ?? []).length)

  // Italic `*`: ignore `**` pairs and line-leading bullet markers, which are
  // list syntax, not emphasis. `_` is left alone — underscores appear inside
  // identifiers far more often than as emphasis in model output.
  const italicSource = out.replace(/\*\*/g, "").replace(/^[ \t]*[*+-][ \t]/gm, "")
  if ((italicSource.match(/\*/g) ?? []).length % 2 === 1) {
    const trimmed = out.trimEnd()
    // Only drop a truly bare trailing `*`; a `**` at the end belongs to bold.
    if (trimmed.endsWith("*") && !trimmed.endsWith("**")) {
      out = trimmed.slice(0, -1)
    } else {
      out += "*"
    }
  }

  return out
}
