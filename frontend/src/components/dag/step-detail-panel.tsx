"use client"

import { useTranslations } from "next-intl"
import { X, Clock } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn, fmtDuration } from "@/lib/utils"
import { IterationCard } from "@/components/steps"
import type { IterationData } from "@/components/steps"
import type { StepState } from "@/hooks/use-dag-steps"
import { ResultBlock } from "@/components/playground/dag-output"

interface StepDetailPanelProps {
  state: StepState | null
  onClose: () => void
}

export function StepDetailPanel({ state, onClose }: StepDetailPanelProps) {
  const t = useTranslations("dag")
  return (
    <div
      className={cn(
        "absolute top-0 right-0 bottom-0 w-72 z-10 border-l border-border/50 bg-card/95 backdrop-blur-md transition-transform duration-200 ease-out flex flex-col overflow-hidden",
        state ? "translate-x-0" : "translate-x-full"
      )}
    >
      {state && (
        <>
          {/* Header */}
          <div className="flex items-start gap-2 p-3 border-b border-border/40 shrink-0">
            <div className="flex-1 min-w-0 space-y-1">
              <Badge
                variant="outline"
                className="text-[10px] border-amber-500/30 text-amber-400"
              >
                {state.step_id}
              </Badge>
              <p
                className="text-sm font-medium text-foreground/80 leading-snug line-clamp-2 hover:line-clamp-none hover:text-foreground transition-colors cursor-default"
                title={state.task}
              >
                {state.task}
              </p>
              {state.duration != null && (
                <div className="flex items-center gap-1 text-[10px] text-muted-foreground tabular-nums">
                  <Clock className="h-2.5 w-2.5" />
                  <span>{fmtDuration(state.duration)}</span>
                </div>
              )}
            </div>
            <button
              onClick={onClose}
              className="shrink-0 p-1 rounded hover:bg-muted/50 text-muted-foreground hover:text-foreground transition-colors"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Content */}
          <ScrollArea className="flex-1 min-h-0">
            <div className="p-3 space-y-2.5">
              {/* Iterations */}
              {state.iterations.map((iter, idx) => {
                const iterData: IterationData = {
                  type: iter.type,
                  iteration: iter.iteration,
                  displayIteration: idx + 1,
                  tool_name: iter.tool_name,
                  tool_args: iter.tool_args,
                  reasoning: iter.reasoning,
                  observation: iter.observation,
                  error: iter.error,
                  loading: iter.loading,
                }
                return (
                  <IterationCard
                    key={idx}
                    data={iterData}
                    variant="inline"
                    size="compact"
                    defaultCollapsed={true}
                  />
                )
              })}

              {/* Result — opens drawer on click */}
              {state.result && (
                <ResultBlock content={state.result} />
              )}

              {state.iterations.length === 0 && !state.result && (
                <p className="text-xs text-muted-foreground text-center py-4">
                  {t("noActivityYet")}
                </p>
              )}
            </div>
          </ScrollArea>
        </>
      )}
    </div>
  )
}
