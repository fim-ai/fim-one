"use client"

import { Loader2 } from "lucide-react"
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
  showReasoning = true,
}: IterationCardProps) {
  const isCompact = size === "compact"
  const isLoading = data.loading || data.type === "tool_start"

  // Auto-generate summary if not provided
  const summary = summaryProp ?? (
    (data.type === "tool_call" || data.type === "tool_start")
      ? generateStepSummary(data.tool_name, data.tool_args, data.reasoning)
      : undefined
  )

  const content = (
    <div className={`space-y-${isCompact ? "1.5" : "2"}`}>
      <IterationHeader data={data} summary={summary} size={size} />

      {showReasoning && data.reasoning && (
        <p className={`text-xs italic text-muted-foreground leading-relaxed ${isCompact ? "" : "pl-9"}`}>
          {data.reasoning}
        </p>
      )}

      {data.tool_args && Object.keys(data.tool_args).length > 0 && (
        <div className={isCompact ? "" : "ml-9"}>
          <ToolArgsBlock
            args={data.tool_args}
            size={size}
            defaultCollapsed={defaultCollapsed}
          />
        </div>
      )}

      {isLoading && (
        <div className={`flex items-center gap-2 text-xs text-muted-foreground ${isCompact ? "" : "ml-9"}`}>
          <Loader2 className="h-3 w-3 animate-spin" />
          <span className="shiny-text">Executing...</span>
        </div>
      )}

      {data.observation && (
        <div className={isCompact ? "" : "ml-9"}>
          <ObservationBlock
            observation={data.observation}
            size={size}
            defaultCollapsed={defaultCollapsed}
          />
        </div>
      )}

      {data.error && (
        <div className={isCompact ? "" : "ml-9"}>
          <ErrorBlock error={data.error} size={size} />
        </div>
      )}
    </div>
  )

  if (variant === "card") {
    return (
      <Card className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 border-amber-500/20 py-4">
        <CardContent>{content}</CardContent>
      </Card>
    )
  }

  // variant === "inline"
  return (
    <div className="rounded-md border border-border/30 bg-muted/20 p-2.5">
      {content}
    </div>
  )
}
