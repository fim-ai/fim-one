"use client"

import { useTranslations } from "next-intl"
import { Wrench, Brain, Loader2, Clock } from "lucide-react"
import { fmtDuration } from "@/lib/utils"
import type { IterationData } from "./types"
import { getToolDisplayName } from "./step-summary"
import { useToolCatalog } from "@/hooks/use-tool-catalog"

interface IterationHeaderProps {
  data: IterationData
  summary?: string
}

/** Single-line compact header: icon · DisplayName · summary · duration */
export function IterationHeader({ data, summary }: IterationHeaderProps) {
  const t = useTranslations("dag")
  const { data: catalog } = useToolCatalog()
  const isTool = !!data.tool_name
  const isLoading = !!data.loading

  const Icon = isLoading ? Loader2 : isTool ? Wrench : Brain
  const iconCls = isLoading ? "animate-spin" : ""

  const displayName = isTool && data.tool_name
    ? getToolDisplayName(data.tool_name, catalog?.tools)
    : t("thinking")

  return (
    <div className="flex items-center gap-2 min-w-0">
      <Icon className={`h-3 w-3 text-amber-500 shrink-0 ${iconCls}`} />
      <span className="text-xs font-medium text-foreground shrink-0">
        {displayName}
      </span>
      {summary && (
        <span className="text-[11px] text-muted-foreground truncate min-w-0">
          {summary}
        </span>
      )}
      {!isLoading && data.duration != null && (
        <span className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground shrink-0 tabular-nums">
          <Clock className="h-2.5 w-2.5" />
          {fmtDuration(data.duration)}
        </span>
      )}
    </div>
  )
}
