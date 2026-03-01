"use client"

import { useState } from "react"
import { Loader2, ChevronRight } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import type { IterationData } from "./types"
import { IterationHeader } from "./iteration-header"
import { ErrorBlock } from "./error-block"
import { generateStepSummary } from "./step-summary"
import { IterationDetailDrawer } from "./iteration-detail-drawer"

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
  variant = "card",
}: IterationCardProps) {
  const [drawerOpen, setDrawerOpen] = useState(false)
  const isLoading = data.loading || data.type === "tool_start"

  const hasDetail = !isLoading && (
    (data.tool_args && Object.keys(data.tool_args).length > 0) ||
    data.observation ||
    data.error ||
    data.reasoning
  )

  const summary = summaryProp ?? (
    (data.type === "tool_call" || data.type === "tool_start")
      ? generateStepSummary(data.tool_name, data.tool_args, data.reasoning)
      : undefined
  )

  const handleClick = hasDetail ? () => setDrawerOpen(true) : undefined

  const inner = (
    <>
      <div className="flex items-center gap-2">
        <div className="flex-1 min-w-0">
          <IterationHeader data={data} summary={summary} />
        </div>
        {isLoading && (
          <div className="flex items-center gap-1.5 shrink-0">
            <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
            <span className="shiny-text text-[10px] text-muted-foreground">Executing…</span>
          </div>
        )}
        {hasDetail && (
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0 group-hover:text-foreground transition-colors" />
        )}
      </div>
      {data.error && (
        <div className="mt-1.5">
          <ErrorBlock error={data.error} size="compact" />
        </div>
      )}
    </>
  )

  if (variant === "card") {
    return (
      <>
        <Card
          className={`animate-in fade-in-0 slide-in-from-bottom-2 duration-200 border-amber-500/20 py-2 transition-colors ${hasDetail ? "cursor-pointer group hover:bg-muted/20" : ""}`}
          onClick={handleClick}
        >
          <CardContent className="py-0">{inner}</CardContent>
        </Card>
        <IterationDetailDrawer
          data={drawerOpen ? data : null}
          summary={summary}
          onClose={() => setDrawerOpen(false)}
        />
      </>
    )
  }

  // variant === "inline"
  return (
    <>
      <div
        className={`rounded-md border border-border/30 bg-muted/20 px-2.5 py-2 transition-colors ${hasDetail ? "cursor-pointer group hover:bg-muted/30" : ""}`}
        onClick={handleClick}
      >
        {inner}
      </div>
      <IterationDetailDrawer
        data={drawerOpen ? data : null}
        summary={summary}
        onClose={() => setDrawerOpen(false)}
      />
    </>
  )
}
