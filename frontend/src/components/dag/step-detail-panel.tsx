"use client"

import { useState } from "react"
import { X, Wrench, Brain, AlertCircle, Clock, ChevronDown, ChevronUp } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { MarkdownContent } from "@/lib/markdown"
import { cn, fmtDuration } from "@/lib/utils"
import type { StepState } from "@/hooks/use-dag-steps"

interface StepDetailPanelProps {
  state: StepState | null
  onClose: () => void
}

/** Collapsible wrapper — collapsed by default, click to expand. */
function Collapsible({
  label,
  labelClass,
  children,
}: {
  label: string
  labelClass?: string
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(false)

  return (
    <div className="rounded bg-muted/30 border border-border/30">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left hover:bg-muted/40 transition-colors"
      >
        <p className={cn("text-[9px] font-medium uppercase tracking-wider flex-1", labelClass ?? "text-muted-foreground")}>
          {label}
        </p>
        {open
          ? <ChevronUp className="h-2.5 w-2.5 text-muted-foreground shrink-0" />
          : <ChevronDown className="h-2.5 w-2.5 text-muted-foreground shrink-0" />
        }
      </button>
      {open && (
        <div className="px-2 pb-2">
          {children}
        </div>
      )}
    </div>
  )
}

export function StepDetailPanel({ state, onClose }: StepDetailPanelProps) {
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
                className="text-[10px] font-mono border-blue-500/30 text-blue-400"
              >
                {state.step_id}
              </Badge>
              <p
                className="text-sm font-medium text-foreground leading-snug line-clamp-2"
                title={state.task}
              >
                {state.task}
              </p>
              {state.duration != null && (
                <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
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
              {state.iterations.map((iter, idx) => (
                <div
                  key={idx}
                  className="rounded-md border border-border/30 bg-muted/20 p-2.5 space-y-1.5"
                >
                  {/* Iteration header */}
                  <div className="flex items-center gap-2 flex-wrap">
                    {iter.type === "tool_call" ? (
                      <>
                        <Wrench className="h-3 w-3 text-blue-500" />
                        <Badge
                          variant="outline"
                          className="border-blue-500/30 text-blue-500 text-[10px] uppercase tracking-wider"
                        >
                          Tool
                        </Badge>
                        {iter.tool_name && (
                          <span className="text-xs font-medium text-foreground">
                            {iter.tool_name}
                          </span>
                        )}
                      </>
                    ) : (
                      <>
                        <Brain className="h-3 w-3 text-amber-500" />
                        <Badge
                          variant="outline"
                          className="border-amber-500/30 text-amber-500 text-[10px] uppercase tracking-wider"
                        >
                          Think
                        </Badge>
                      </>
                    )}
                    <span className="text-[10px] text-muted-foreground ml-auto">
                      #{idx + 1}
                    </span>
                  </div>

                  {/* Reasoning — always visible, italic */}
                  {iter.reasoning && (
                    <p className="text-xs italic text-muted-foreground leading-relaxed">
                      {iter.reasoning}
                    </p>
                  )}

                  {/* Tool args — collapsed by default */}
                  {iter.tool_args &&
                    Object.keys(iter.tool_args).length > 0 && (
                      <Collapsible label="Arguments" labelClass="text-muted-foreground">
                        <pre className="overflow-x-auto rounded bg-muted/40 p-1.5 text-[10px] font-mono leading-relaxed max-h-[200px] overflow-y-auto">
                          {JSON.stringify(iter.tool_args, null, 2)}
                        </pre>
                      </Collapsible>
                    )}

                  {/* Observation — collapsed by default */}
                  {iter.observation && (
                    <Collapsible label="Observation">
                      <pre className="whitespace-pre-wrap text-[10px] text-foreground/90 font-mono leading-relaxed max-h-[200px] overflow-y-auto">
                        {iter.observation}
                      </pre>
                    </Collapsible>
                  )}

                  {/* Error — always visible, highlighted */}
                  {iter.error && (
                    <div className="rounded border border-destructive/30 bg-destructive/5 p-2">
                      <div className="flex items-center gap-1 mb-0.5">
                        <AlertCircle className="h-2.5 w-2.5 text-destructive" />
                        <p className="text-[9px] font-medium text-destructive uppercase tracking-wider">
                          Error
                        </p>
                      </div>
                      <pre className="whitespace-pre-wrap text-[10px] text-destructive/90 font-mono">
                        {iter.error}
                      </pre>
                    </div>
                  )}
                </div>
              ))}

              {/* Result — collapsed by default */}
              {state.result && (
                <Collapsible label="Result" labelClass="text-green-500">
                  <MarkdownContent
                    content={state.result}
                    className="prose-sm text-xs text-foreground/90"
                  />
                </Collapsible>
              )}

              {state.iterations.length === 0 && !state.result && (
                <p className="text-xs text-muted-foreground text-center py-4">
                  No activity yet
                </p>
              )}
            </div>
          </ScrollArea>
        </>
      )}
    </div>
  )
}
