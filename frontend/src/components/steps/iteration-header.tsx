"use client"

import { Wrench, Brain, Loader2, Clock } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { fmtDuration } from "@/lib/utils"
import type { IterationData } from "./types"

interface IterationHeaderProps {
  data: IterationData
  summary?: string
  size?: "default" | "compact"
}

export function IterationHeader({ data, summary, size = "default" }: IterationHeaderProps) {
  const isCompact = size === "compact"
  const isTool = data.type === "tool_call" || data.type === "tool_start"
  const isThinking = data.type === "thinking"
  const isLoading = data.loading || data.type === "tool_start"

  const Icon = isLoading ? Loader2 : isTool ? Wrench : Brain
  const iconCls = isLoading ? "animate-spin" : ""

  return (
    <div className="space-y-0.5">
      <div className={`flex items-center gap-${isCompact ? "2" : "3"} flex-wrap`}>
        {isCompact ? (
          <Icon className={`h-3 w-3 text-amber-500 ${iconCls}`} />
        ) : (
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-500/10">
            <Icon className={`h-3.5 w-3.5 text-amber-500 ${iconCls}`} />
          </div>
        )}
        <Badge
          variant="outline"
          className="border-amber-500/30 text-amber-500 text-[10px] uppercase tracking-wider"
        >
          {isThinking ? "Thinking" : "Tool"}
        </Badge>
        {isTool && data.tool_name && (
          <span className={`font-medium text-foreground ${isCompact ? "text-xs" : "text-xs"}`}>
            {data.tool_name}
          </span>
        )}
        {data.displayIteration != null && (
          <span className="text-[10px] text-muted-foreground">
            {isCompact ? `#${data.displayIteration}` : `Iteration ${data.displayIteration}`}
          </span>
        )}
        {!isLoading && data.duration != null && (
          <span className={`${isCompact ? "" : "ml-auto"} flex items-center gap-1 text-[10px] text-muted-foreground shrink-0`}>
            <Clock className="h-2.5 w-2.5" />
            {fmtDuration(data.duration)}
          </span>
        )}
      </div>
      {summary && (
        <p className={`text-muted-foreground leading-relaxed ${isCompact ? "text-[10px]" : "text-[11px] pl-9"}`}>
          {summary}
        </p>
      )}
    </div>
  )
}
