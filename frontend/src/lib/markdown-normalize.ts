/**
 * Source-level fixes applied before markdown is parsed.
 *
 * Both handle the same class of problem: model output that is valid to a human
 * reader but not to CommonMark, so the renderer would otherwise show raw
 * syntax. They run on the source string, never on parsed nodes, because by
 * parse time the damage (an escaped bracket, a heading read as plain text) is
 * already baked in.
 */

/**
 * ATX headings written without a space after the `#` run (`###标题`) — invalid
 * per CommonMark, but common in model output before CJK text.
 */
export function normalizeHeadings(md: string): string {
  return md.replace(/^(#{1,6})([^\s#])/gm, "$1 $2")
}

/** Opening or closing line of a fenced code block. */
const FENCE_RE = /^[ \t]{0,3}(`{3,}|~{3,})/

/**
 * In order: an inline code span, an existing `$$…$$` region, a `\[…\]` display
 * formula, a `\(…\)` inline formula. The first two are matched only so they
 * can be skipped — a `\\[2pt]` row spacing inside a matrix must not be read as
 * a delimiter.
 */
const MATH_OR_CODE_RE =
  /(`+)[\s\S]*?\1|\$\$[\s\S]*?\$\$|\\\[([\s\S]*?)\\\]|\\\(([\s\S]*?)\\\)/g

/** Split a source into fenced-code and prose segments, preserving every line. */
function splitFencedSegments(md: string): { code: boolean; text: string }[] {
  const segments: { code: boolean; text: string }[] = []
  let current: string[] = []
  let inCode = false
  let fence: string | null = null

  const flush = () => {
    if (current.length > 0) segments.push({ code: inCode, text: current.join("\n") })
    current = []
  }

  for (const line of md.split("\n")) {
    const marker = FENCE_RE.exec(line)?.[1]
    if (fence === null) {
      if (marker) {
        flush()
        inCode = true
        fence = marker
      }
      current.push(line)
      continue
    }
    current.push(line)
    // A closing fence uses the same character and is at least as long.
    if (marker && marker[0] === fence[0] && marker.length >= fence.length) {
      flush()
      inCode = false
      fence = null
    }
  }
  flush()
  return segments
}

/**
 * Rewrite LaTeX's `\[…\]` and `\(…\)` delimiters as `$$…$$` and `$…$`.
 *
 * `remark-math` only recognises dollar delimiters, so a formula in LaTeX's own
 * delimiters reached the page as literal text with its backslashes stripped
 * (markdown reads `\[` as an escaped bracket). Models emit either form freely,
 * more often the LaTeX one when writing Chinese.
 *
 * Only balanced pairs are rewritten, which is also what keeps a streaming tail
 * honest: a `\[` whose closer has not arrived yet stays literal for a frame
 * instead of handing KaTeX a fragment.
 */
export function normalizeMathDelimiters(md: string): string {
  if (!md.includes("\\[") && !md.includes("\\(")) return md

  return splitFencedSegments(md)
    .map((segment) => {
      if (segment.code) return segment.text
      return segment.text.replace(
        MATH_OR_CODE_RE,
        (match: string, ticks?: string, display?: string, inline?: string) => {
          if (ticks !== undefined) return match
          if (display !== undefined) {
            const body = display.trim()
            // Keep multi-line formulas on their own lines so they stay a block.
            return display.includes("\n") ? `$$\n${body}\n$$` : `$$${body}$$`
          }
          if (inline !== undefined) return `$${inline.trim()}$`
          return match
        },
      )
    })
    .join("\n")
}
