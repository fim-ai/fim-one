"use client"

import { useMemo, useState } from "react"
import { parseEvidence, parseSimpleEvidence, mergeEvidence, type ParsedEvidence } from "@/lib/evidence-utils"
import type { ReactStepEvent } from "@/types/api"
import type { StepItem } from "@/hooks/use-react-steps"
import { ChevronDown } from "lucide-react"

interface ReferencesSectionProps {
  items: StepItem[]
}

const COLLAPSED_COUNT = 3

export function ReferencesSection({ items }: ReferencesSectionProps) {
  const [expanded, setExpanded] = useState(false)

  const evidence = useMemo<ParsedEvidence | null>(() => {
    const blocks: ParsedEvidence[] = []
    for (const item of items) {
      if (item.event === "step") {
        const step = item.data as ReactStepEvent
        if (step.type === "tool_call" && step.observation) {
          const parsed = parseEvidence(step.observation) ?? parseSimpleEvidence(step.observation)
          if (parsed) blocks.push(parsed)
        }
      }
    }
    return blocks.length > 0 ? mergeEvidence(blocks) : null
  }, [items])

  if (!evidence || evidence.sources.length === 0) return null

  const confidenceColor = evidence.confidence >= 70
    ? "text-green-600 dark:text-green-400"
    : evidence.confidence >= 40
      ? "text-yellow-600 dark:text-yellow-400"
      : "text-red-600 dark:text-red-400"

  const visibleSources = expanded
    ? evidence.sources
    : evidence.sources.slice(0, COLLAPSED_COUNT)
  const hasMore = evidence.sources.length > COLLAPSED_COUNT

  return (
    <div className="mt-4 pt-3 border-t border-border/60">
      {/* Header */}
      <div className="flex items-center gap-2 mb-2.5">
        <span className="text-xs font-medium text-muted-foreground">References</span>
        <span className={`text-xs font-mono ${confidenceColor}`}>
          {Math.round(evidence.confidence)}% confidence
        </span>
        <span className="text-[10px] text-muted-foreground/60">
          {evidence.sources.length} source{evidence.sources.length !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Reference items */}
      <div className="grid gap-1">
        {visibleSources.map((source) => (
          <div
            key={source.index}
            className="rounded-md px-2.5 py-1.5 bg-muted/30 hover:bg-muted/50 transition-colors overflow-hidden"
          >
            {/* Row 1: badge + filename + KB tag */}
            <div className="flex items-baseline gap-2 min-w-0">
              <sup className="inline-flex items-center justify-center min-w-[1.1em] h-[1.1em] px-0.5 rounded text-[0.7em] font-medium bg-primary/10 text-primary shrink-0 relative top-0">
                {source.index}
              </sup>
              <span className="text-xs font-medium text-foreground/85 truncate" title={source.displayName}>
                {source.displayName}
                {source.page != null && (
                  <span className="ml-1 font-normal text-muted-foreground">p.{source.page}</span>
                )}
              </span>
              {source.kbName && (
                <span className="text-[10px] text-muted-foreground/60 bg-muted/60 rounded px-1.5 py-0.5 shrink-0 whitespace-nowrap">
                  {source.kbName}
                </span>
              )}
            </div>
            {/* Row 2: quote — wrap naturally, clamp to 2 lines */}
            {source.quote && (
              <p className="mt-0.5 ml-6 text-[11px] text-muted-foreground/70 leading-relaxed line-clamp-2" title={source.quote}>
                {source.quote}
              </p>
            )}
          </div>
        ))}
      </div>

      {/* Show more / less toggle */}
      {hasMore && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-1.5 flex items-center gap-1 text-[11px] text-muted-foreground/70 hover:text-muted-foreground transition-colors cursor-pointer"
        >
          <ChevronDown className={`h-3 w-3 transition-transform ${expanded ? "rotate-180" : ""}`} />
          {expanded
            ? "Show less"
            : `Show ${evidence.sources.length - COLLAPSED_COUNT} more`}
        </button>
      )}
    </div>
  )
}
