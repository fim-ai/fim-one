"use client"

import { useState } from "react"
import { Loader2, ChevronDown, ChevronUp } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import type { IterationData } from "./types"
import { IterationHeader } from "./iteration-header"
import { ToolArgsBlock } from "./tool-args-block"
import { ObservationBlock } from "./observation-block"
import { ErrorBlock } from "./error-block"
import { generateStepSummary } from "./step-summary"

interface IterationCardProps {
  data: IterationData
  summary?: string
  size?: "default" | "compact"
  variant?: "card" | "inline"
  defaultCollapsed?: boolean
  showReasoning?: boolean
}

export function IterationCard({
  data,
  summary: summaryProp,
  size = "default",
  variant = "card",
  defaultCollapsed = true,
  showReasoning = false,
}: IterationCardProps) {
  const isLoading = data.loading || data.type === "tool_start"
  const hasArgs = data.tool_args && Object.keys(data.tool_args).length > 0
  const hasObs = !!data.observation
  const hasError = !!data.error

  const [argsOpen, setArgsOpen] = useState(!defaultCollapsed)
  const [obsOpen, setObsOpen] = useState(!defaultCollapsed)

  // Auto-generate summary if not provided
  const summary = summaryProp ?? (
    (data.type === "tool_call" || data.type === "tool_start")
      ? generateStepSummary(data.tool_name, data.tool_args, data.reasoning)
      : undefined
  )

  const content = (
    <div className="space-y-1.5">
      <IterationHeader data={data} summary={summary} />

      {showReasoning && data.reasoning && (
        <p className="text-[11px] italic text-muted-foreground leading-relaxed">
          {data.reasoning}
        </p>
      )}

      {isLoading && (
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          <Loader2 className="h-2.5 w-2.5 animate-spin" />
          <span className="shiny-text">Executing…</span>
        </div>
      )}

      {/* Inline toggle pills for Args / Obs */}
      {(hasArgs || hasObs) && !isLoading && (
        <div className="flex items-center gap-1.5 flex-wrap">
          {hasArgs && (
            <TogglePill label="Arguments" open={argsOpen} onClick={() => setArgsOpen((v) => !v)} />
          )}
          {hasObs && (
            <TogglePill label="Observation" open={obsOpen} onClick={() => setObsOpen((v) => !v)} />
          )}
        </div>
      )}

      {/* Expanded content */}
      {argsOpen && hasArgs && (
        <ToolArgsBlock args={data.tool_args!} size={size} defaultCollapsed={false} />
      )}
      {obsOpen && hasObs && (
        <ObservationBlock observation={data.observation!} size={size} defaultCollapsed={false} />
      )}
      {hasError && <ErrorBlock error={data.error!} size={size} />}
    </div>
  )

  if (variant === "card") {
    return (
      <Card className="animate-in fade-in-0 slide-in-from-bottom-2 duration-200 border-amber-500/20 py-2">
        <CardContent className="py-0">{content}</CardContent>
      </Card>
    )
  }

  // variant === "inline"
  return (
    <div className="rounded-md border border-border/30 bg-muted/20 px-2.5 py-2">
      {content}
    </div>
  )
}

/** Compact inline pill toggle button */
function TogglePill({ label, open, onClick }: { label: string; open: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center gap-1 px-2 py-0.5 rounded border border-border/40 bg-muted/30 text-[10px] text-muted-foreground hover:bg-muted/50 transition-colors"
    >
      <span className="uppercase tracking-wider font-medium">{label}</span>
      {open
        ? <ChevronUp className="h-2.5 w-2.5" />
        : <ChevronDown className="h-2.5 w-2.5" />
      }
    </button>
  )
}
