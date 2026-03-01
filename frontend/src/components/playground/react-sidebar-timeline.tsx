"use client"

import { Loader2, Clock } from "lucide-react"
import { fmtDuration } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { StepItem } from "@/hooks/use-react-steps"
import type { ReactStepEvent, ReactDoneEvent } from "@/types/api"

interface ReactSidebarTimelineProps {
  items: StepItem[]
  isRunning: boolean
  onItemClick?: (idx: number) => void
}

export function ReactSidebarTimeline({ items, isRunning, onItemClick }: ReactSidebarTimelineProps) {
  return (
    <ScrollArea className="h-full">
      <div className="p-3 space-y-0">
        {items.map((item, idx) => {
          if (item.event === "step") {
            const step = item.data as ReactStepEvent
            return (
              <TimelineStep
                key={idx}
                step={step}
                item={item}
                isLast={idx === items.length - 1 && !isRunning}
                onClick={onItemClick ? () => onItemClick(idx) : undefined}
              />
            )
          }
          if (item.event === "done") {
            const done = item.data as ReactDoneEvent
            return (
              <TimelineDone
                key={idx}
                done={done}
                stepNumber={done.iterations}
                onClick={onItemClick ? () => onItemClick(idx) : undefined}
              />
            )
          }
          return null
        })}
      </div>
    </ScrollArea>
  )
}

function TimelineStep({ step, item, isLast, onClick }: { step: ReactStepEvent; item: StepItem; isLast: boolean; onClick?: () => void }) {
  const isThinking = step.type === "thinking"
  const isToolStart = step.type === "tool_start"
  const isTool = step.type === "tool_call" || isToolStart

  const dotColor = isThinking
    ? "bg-amber-500"
    : isTool
      ? "bg-amber-500"
      : "bg-zinc-500"

  const lineColor = isLast ? "bg-transparent" : "bg-border/50"

  return (
    <div className={`flex gap-3 relative ${onClick ? "cursor-pointer hover:bg-muted/30 rounded -mx-1 px-1 transition-colors" : ""}`} onClick={onClick}>
      {/* Timeline line + dot */}
      <div className="flex flex-col items-center shrink-0 pt-0.5">
        <div className={`w-2 h-2 rounded-full shrink-0 ${dotColor} ${isToolStart ? "animate-pulse" : ""}`} />
        <div className={`w-px flex-1 ${lineColor}`} />
      </div>

      {/* Content */}
      <div className="pb-3 min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          {item.displayIteration && (
            <span className="text-[10px] text-muted-foreground font-mono">#{item.displayIteration}</span>
          )}
          <Badge
            variant="outline"
            className={`text-[9px] uppercase tracking-wider py-0 h-4 ${
              isThinking
                ? "border-amber-500/30 text-amber-500"
                : "border-amber-500/30 text-amber-500"
            }`}
          >
            {isThinking ? "Think" : "Tool"}
          </Badge>
          {step.tool_name && (
            <span className="text-xs font-medium text-foreground truncate">{step.tool_name}</span>
          )}
          {item.duration != null && (
            <span className="ml-auto flex items-center gap-0.5 text-[10px] text-muted-foreground shrink-0">
              <Clock className="h-2.5 w-2.5" />
              {fmtDuration(item.duration)}
            </span>
          )}
        </div>
        {step.reasoning && (
          <p className="text-[11px] text-muted-foreground mt-1 line-clamp-2 leading-relaxed">
            {step.reasoning}
          </p>
        )}
        {isToolStart && (
          <div className="flex items-center gap-1.5 mt-1 text-[11px] text-muted-foreground">
            <Loader2 className="h-2.5 w-2.5 animate-spin" />
            <span className="shiny-text">Executing...</span>
          </div>
        )}
      </div>
    </div>
  )
}

function TimelineDone({ done, stepNumber, onClick }: { done: ReactDoneEvent; stepNumber?: number; onClick?: () => void }) {
  return (
    <div className={`flex gap-3 relative ${onClick ? "cursor-pointer hover:bg-muted/30 rounded -mx-1 px-1 transition-colors" : ""}`} onClick={onClick}>
      <div className="flex flex-col items-center shrink-0 pt-0.5">
        <div className="w-2 h-2 rounded-full shrink-0 bg-green-500" />
      </div>
      <div className="pb-2 min-w-0 flex-1">
        <div className="flex items-center gap-2">
          {stepNumber != null && (
            <span className="text-[10px] text-muted-foreground font-mono">#{stepNumber}</span>
          )}
          <Badge
            variant="outline"
            className="text-[9px] uppercase tracking-wider py-0 h-4 border-green-500/30 text-green-500"
          >
            Result
          </Badge>
          <span className="ml-auto flex items-center gap-0.5 text-[10px] text-muted-foreground">
            <Clock className="h-2.5 w-2.5" />
            {fmtDuration(done.elapsed)}
          </span>
        </div>
      </div>
    </div>
  )
}
