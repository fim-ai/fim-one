"use client"

import { useState } from "react"
import { useTranslations } from "next-intl"
import { Loader2, ChevronRight, Clock } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import type { IterationData } from "./types"
import { IterationHeader } from "./iteration-header"
import { ErrorBlock } from "./error-block"
import { generateStepSummary, getToolIcon, getToolDisplayName } from "./step-summary"
import { fmtDuration } from "@/lib/utils"
import { IterationDetailDrawer } from "./iteration-detail-drawer"
import { useToolCatalog } from "@/hooks/use-tool-catalog"

interface IterationCardProps {
  data: IterationData
  summary?: string
  size?: "default" | "compact"
  variant?: "card" | "inline" | "pill"
  defaultCollapsed?: boolean
  showReasoning?: boolean
}

export function IterationCard({
  data,
  summary: summaryProp,
  variant = "card",
}: IterationCardProps) {
  const t = useTranslations("dag")
  const { data: catalog } = useToolCatalog()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const isLoading = !!data.loading

  const hasDetail = !isLoading && (
    (data.tool_args && Object.keys(data.tool_args).length > 0) ||
    data.observation ||
    data.error ||
    data.reasoning
  )

  const summary = summaryProp ?? (
    data.tool_name
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
            <span className="shiny-text text-[10px] text-muted-foreground">{t("executing")}</span>
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
          className={`border-amber-500/20 py-2 transition-colors ${hasDetail ? "cursor-pointer group hover:bg-muted/20" : ""}`}
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

  if (variant === "pill") {
    const ToolIcon = data.tool_name ? getToolIcon(data.tool_name, catalog?.tools) : Loader2
    const displayName = data.tool_name ? getToolDisplayName(data.tool_name, catalog?.tools) : t("thinking")
    const toolMeta = data.tool_name ? catalog?.tools?.find((t) => t.name === data.tool_name) : undefined

    return (
      <>
        <div
          className={`flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs transition-colors
            ${data.error
              ? "bg-destructive/5 border border-destructive/30 hover:bg-destructive/10"
              : "bg-muted/30 border border-border/30 hover:bg-muted/50"
            }
            ${hasDetail ? "cursor-pointer group" : ""}`}
          onClick={handleClick}
        >
          {isLoading ? (
            <Loader2 className="h-3 w-3 animate-spin text-muted-foreground shrink-0" />
          ) : (
            <ToolIcon className="h-3 w-3 text-muted-foreground shrink-0" />
          )}
          <span className="font-medium shrink-0">{displayName}</span>
          {toolMeta && (
            <span className="text-[10px] text-muted-foreground/60 shrink-0">
              [{toolMeta.category.charAt(0).toUpperCase() + toolMeta.category.slice(1)}]
            </span>
          )}
          {summary && (
            <span className="text-muted-foreground truncate min-w-0">{summary}</span>
          )}
          {!isLoading && data.duration != null && (
            <span className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground shrink-0 tabular-nums">
              <Clock className="h-2.5 w-2.5" />
              {fmtDuration(data.duration)}
            </span>
          )}
          {hasDetail && !isLoading && (
            <ChevronRight className="h-3 w-3 text-muted-foreground shrink-0 group-hover:text-foreground transition-colors" />
          )}
        </div>
        {data.error && (
          <div className="mt-1">
            <ErrorBlock error={data.error} size="compact" />
          </div>
        )}
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
