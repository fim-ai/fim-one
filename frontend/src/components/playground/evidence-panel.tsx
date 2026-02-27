"use client"

import { useState } from "react"
import { ChevronDown, ChevronRight, AlertTriangle, CheckCircle2 } from "lucide-react"

interface EvidencePanelProps {
  content: string
}

interface ParsedEvidence {
  confidence: number
  sourceCount: number
  sources: {
    index: number
    name: string
    relevance: number
    alignment: number
    quote: string
    page?: number
  }[]
  conflicts: {
    sourceA: string
    sourceB: string
    textA: string
    textB: string
  }[]
}

function parseEvidence(content: string): ParsedEvidence | null {
  // Match **Evidence** (confidence: XX.X%, N sources):
  const headerMatch = content.match(/\*\*Evidence\*\*\s*\(confidence:\s*([\d.]+)%,\s*(\d+)\s*sources?\)/)
  if (!headerMatch) return null

  const confidence = parseFloat(headerMatch[1])
  const sourceCount = parseInt(headerMatch[2])

  // Match [N] Source: name (relevance: X.XXX, alignment: X.XXX)
  const sourceRegex = /\[(\d+)\]\s*Source:\s*(.+?)\s*\(relevance:\s*([\d.]+),\s*alignment:\s*([\d.]+)\)\s*\n\s*>\s*"([^"]+)"\s*(?:p\.(\d+))?/g
  const sources: ParsedEvidence["sources"] = []
  let match
  while ((match = sourceRegex.exec(content)) !== null) {
    sources.push({
      index: parseInt(match[1]),
      name: match[2].trim(),
      relevance: parseFloat(match[3]),
      alignment: parseFloat(match[4]),
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

function ConfidenceBadge({ value }: { value: number }) {
  const color =
    value >= 80
      ? "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400"
      : value >= 50
        ? "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400"
        : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400"

  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${color}`}>
      {value.toFixed(1)}% confidence
    </span>
  )
}

export function EvidencePanel({ content }: EvidencePanelProps) {
  const [isOpen, setIsOpen] = useState(false)
  const parsed = parseEvidence(content)

  if (!parsed) return null

  return (
    <div className="my-2 rounded-lg border border-border bg-muted/30">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex w-full items-center gap-2 px-3 py-2 text-sm font-medium hover:bg-muted/50 transition-colors"
      >
        {isOpen ? (
          <ChevronDown className="h-4 w-4 shrink-0" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0" />
        )}
        <CheckCircle2 className="h-4 w-4 shrink-0 text-primary" />
        <span>Evidence ({parsed.sourceCount} sources)</span>
        <ConfidenceBadge value={parsed.confidence} />
        {parsed.conflicts.length > 0 && (
          <span className="ml-auto flex items-center gap-1 text-xs text-yellow-600 dark:text-yellow-400">
            <AlertTriangle className="h-3 w-3" />
            {parsed.conflicts.length} conflict{parsed.conflicts.length > 1 ? "s" : ""}
          </span>
        )}
      </button>

      {isOpen && (
        <div className="border-t border-border px-3 py-2 space-y-3">
          {parsed.sources.map((source) => (
            <div key={source.index} className="space-y-1">
              <div className="flex items-center gap-2 text-xs">
                <span className="font-mono font-bold text-primary">[{source.index}]</span>
                <span className="font-medium">{source.name}</span>
                {source.page && (
                  <span className="text-muted-foreground">p.{source.page}</span>
                )}
                <span className="ml-auto text-muted-foreground">
                  rel: {source.relevance.toFixed(3)} | align: {source.alignment.toFixed(3)}
                </span>
              </div>
              <blockquote className="border-l-2 border-primary/30 pl-3 text-xs text-muted-foreground italic">
                &ldquo;{source.quote}&rdquo;
              </blockquote>
            </div>
          ))}

          {parsed.conflicts.length > 0 && (
            <div className="space-y-2 pt-2 border-t border-border">
              <div className="flex items-center gap-1 text-xs font-medium text-yellow-600 dark:text-yellow-400">
                <AlertTriangle className="h-3.5 w-3.5" />
                Conflicts Detected
              </div>
              {parsed.conflicts.map((conflict, i) => (
                <div key={i} className="rounded border border-yellow-200 dark:border-yellow-800 bg-yellow-50 dark:bg-yellow-900/20 p-2 text-xs space-y-1">
                  <div className="font-medium">
                    {conflict.sourceA} vs {conflict.sourceB}
                  </div>
                  <div className="text-muted-foreground">
                    A: &ldquo;{conflict.textA}&rdquo;
                  </div>
                  <div className="text-muted-foreground">
                    B: &ldquo;{conflict.textB}&rdquo;
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
