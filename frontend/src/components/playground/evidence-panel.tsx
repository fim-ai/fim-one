"use client"

import { useState } from "react"
import { useTranslations } from "next-intl"
import { ChevronDown, ChevronRight, AlertTriangle, CheckCircle2 } from "lucide-react"
import { parseEvidence } from "@/lib/evidence-utils"

interface EvidencePanelProps {
  content: string
}

function ConfidenceBadge({ value }: { value: number }) {
  const t = useTranslations("playground")
  const color =
    value >= 80
      ? "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400"
      : value >= 50
        ? "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400"
        : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400"

  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${color}`}>
      {t("confidenceLabel", { value: value.toFixed(1) })}
    </span>
  )
}

export function EvidencePanel({ content }: EvidencePanelProps) {
  const t = useTranslations("playground")
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
        <span>{t("evidence", { count: parsed.sourceCount })}</span>
        <ConfidenceBadge value={parsed.confidence} />
        {parsed.conflicts.length > 0 && (
          <span className="ml-auto flex items-center gap-1 text-xs text-yellow-600 dark:text-yellow-400">
            <AlertTriangle className="h-3 w-3" />
            {parsed.conflicts.length > 1 ? t("conflictCountPlural", { count: parsed.conflicts.length }) : t("conflictCount", { count: parsed.conflicts.length })}
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
                  rel: {source.relevance.toFixed(3)}
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
                {t("conflictsDetected")}
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
