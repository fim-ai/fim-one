export interface ParsedSource {
  index: number
  name: string
  displayName: string
  kbName?: string
  relevance: number
  alignment: number
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
 *   **Evidence** (confidence: 85.0%, 3 sources):
 *   [1] Source: uploads/kb/.../file.md (relevance: 0.912, alignment: 0.850)
 *   > "quoted text"
 */
export function parseEvidence(content: string): ParsedEvidence | null {
  // Match **Evidence** (confidence: XX.X%, N sources):
  const headerMatch = content.match(/\*\*Evidence\*\*\s*\(confidence:\s*([\d.]+)%,\s*(\d+)\s*sources?\)/)
  if (!headerMatch) return null

  const confidence = parseFloat(headerMatch[1])
  const sourceCount = parseInt(headerMatch[2])

  // Match [N] Source: name [KB: kb_name]? (relevance: X.XXX, alignment: X.XXX)
  const sourceRegex = /\[(\d+)\]\s*Source:\s*(.+?)(?:\s*\[KB:\s*(.+?)\])?\s*\(relevance:\s*([\d.]+),\s*alignment:\s*([\d.]+)\)\s*\n\s*>\s*"?([^"\n]+)"?\s*(?:p\.(\d+))?/g
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
      alignment: parseFloat(match[5]),
      quote: match[6],
      page: match[7] ? parseInt(match[7]) : undefined,
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
