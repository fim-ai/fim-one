export interface ParsedSource {
  index: number
  name: string
  displayName: string
  kbName?: string
  relevance: number
  quote: string
  page?: number
}

export interface ParsedEvidence {
  confidence: number
  sourceCount: number
  sources: ParsedSource[]
  conflicts: { sourceA: string; sourceB: string; textA: string; textB: string }[]
}

/**
 * Extract a human-readable filename from a full path.
 * e.g. "uploads/kb/.../summary.md" -> "summary.md"
 */
export function extractDisplayName(path: string): string {
  const parts = path.split("/")
  return parts[parts.length - 1] || path
}

/**
 * Parse the **Evidence** block from a grounded-generation tool observation.
 *
 * Expected format:
 *   **Evidence** (confidence: 85%, 3 sources):
 *   [1] Source: uploads/kb/.../file.md (relevance: 0.912)
 *   > "quoted text"
 */
export function parseEvidence(content: string): ParsedEvidence | null {
  // Match **Evidence** (confidence: XX.X%, N sources):
  const headerMatch = content.match(/\*\*Evidence\*\*\s*\(confidence:\s*([\d.]+)%,\s*(\d+)\s*sources?\)/)
  if (!headerMatch) return null

  const confidence = parseFloat(headerMatch[1])
  const sourceCount = parseInt(headerMatch[2])

  // Match [N] Source: name [KB: kb_name]? (relevance: X.XXX)
  const sourceRegex = /\[(\d+)\]\s*Source:\s*(.+?)(?:\s*\[KB:\s*(.+?)\])?\s*\(relevance:\s*([\d.]+)\)\s*\n\s*>\s*"?([^"\n]+)"?\s*(?:p\.(\d+))?/g
  const sources: ParsedSource[] = []
  let match
  while ((match = sourceRegex.exec(content)) !== null) {
    const name = match[2].trim()
    sources.push({
      index: parseInt(match[1]),
      name,
      displayName: extractDisplayName(name),
      kbName: match[3]?.trim(),
      relevance: parseFloat(match[4]),
      quote: match[5],
      page: match[6] ? parseInt(match[6]) : undefined,
    })
  }

  // Match conflicts
  const conflicts: ParsedEvidence["conflicts"] = []
  const conflictSection = content.match(/\*\*Conflicts detected:\*\*([\s\S]*?)$/)
  if (conflictSection) {
    const conflictRegex = /- (.+?) vs (.+?):\s*\n\s*A: "([^"]+)"\s*\n\s*B: "([^"]+)"/g
    while ((match = conflictRegex.exec(conflictSection[1])) !== null) {
      conflicts.push({
        sourceA: match[1].trim(),
        sourceB: match[2].trim(),
        textA: match[3],
        textB: match[4],
      })
    }
  }

  return { confidence, sourceCount, sources, conflicts }
}

/**
 * Parse the simple `kb_retrieve` tool output into ParsedEvidence.
 *
 * Expected format (chunks separated by `---`):
 *   [1] (score: 0.912)
 *   This is the chunk content...
 *
 *   ---
 *
 *   [2] (score: 0.845)
 *   Another chunk...
 */
export function parseSimpleEvidence(content: string): ParsedEvidence | null {
  const sourceRegex = /\[(\d+)\]\s*\(score:\s*([\d.]+)\)\s*\n([\s\S]*?)(?=\n\n---|\n*$)/g
  const sources: ParsedSource[] = []
  let match
  while ((match = sourceRegex.exec(content)) !== null) {
    const text = match[3].trim()
    const preview = text.length > 200 ? text.slice(0, 200) + "..." : text
    sources.push({
      index: parseInt(match[1]),
      name: "knowledge base",
      displayName: "Knowledge Base",
      relevance: parseFloat(match[2]),
      quote: preview,
    })
  }
  if (sources.length === 0) return null
  const avgRelevance = sources.reduce((s, src) => s + src.relevance, 0) / sources.length
  return {
    confidence: Math.round(avgRelevance * 100),
    sourceCount: sources.length,
    sources,
    conflicts: [],
  }
}

/**
 * Strip citation markers like [1], [10], [27] from text.
 *
 * Targets bracketed numbers (`[N]`) that are NOT followed by `(`,
 * which preserves markdown links like `[text](url)`.
 * Also cleans up extra whitespace left behind, e.g. `text [10].` becomes `text.`
 */
export function stripCitations(text: string): string {
  // Remove ` [N]` (with preceding space) when NOT followed by `(`
  // Then remove `[N]` at start of string or after newline, also not followed by `(`
  return text
    .replace(/ \[\d+\](?!\()/g, "")
    .replace(/\[\d+\](?!\()/g, "")
}

export interface InsufficientEvidence {
  confidence: number
  threshold: number
}

/**
 * Parse the `[Evidence insufficient]` message from a grounded-generation tool
 * observation and extract the confidence / threshold numbers.
 *
 * Expected format:
 *   [Evidence insufficient] Confidence 35% is below the threshold 60%...
 */
export function parseInsufficientEvidence(content: string): InsufficientEvidence | null {
  const match = content.match(
    /\[Evidence insufficient\]\s*Confidence\s+(\d+)%\s+is below\s+the threshold\s+(\d+)%/
  )
  if (!match) return null
  return { confidence: parseInt(match[1]), threshold: parseInt(match[2]) }
}

/**
 * Merge multiple ParsedEvidence blocks into a single unified result.
 *
 * - confidence: max across all blocks
 * - sourceCount: sum across all blocks
 * - sources: concatenated (backend uses cumulative numbering so indices are non-overlapping)
 * - conflicts: concatenated
 */
export function mergeEvidence(blocks: ParsedEvidence[]): ParsedEvidence {
  if (blocks.length === 0) {
    return { confidence: 0, sourceCount: 0, sources: [], conflicts: [] }
  }
  if (blocks.length === 1) {
    return blocks[0]
  }
  return {
    confidence: Math.max(...blocks.map((b) => b.confidence)),
    sourceCount: blocks.reduce((sum, b) => sum + b.sourceCount, 0),
    sources: blocks.flatMap((b) => b.sources),
    conflicts: blocks.flatMap((b) => b.conflicts),
  }
}
