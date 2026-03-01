"use client"

import { useState } from "react"
import { Wrench, Brain, Clock, ArrowUpRight, ArrowDownLeft } from "lucide-react"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn, fmtDuration } from "@/lib/utils"
import type { IterationData } from "./types"
import { getToolDisplayName } from "./step-summary"
import { ToolArgsBlock } from "./tool-args-block"
import { ObservationBlock } from "./observation-block"
import { ErrorBlock } from "./error-block"

type TabKey = "args" | "obs"

interface IterationDetailDrawerProps {
  data: IterationData | null
  summary?: string
  onClose: () => void
}

export function IterationDetailDrawer({ data, summary, onClose }: IterationDetailDrawerProps) {
  const [activeTab, setActiveTab] = useState<TabKey>("args")

  const open = !!data
  const isTool = data?.type === "tool_call" || data?.type === "tool_start"
  const displayName = isTool && data?.tool_name
    ? getToolDisplayName(data.tool_name)
    : "Thinking"

  const hasArgs = data?.tool_args && Object.keys(data.tool_args).length > 0
  const hasObs = !!data?.observation
  const hasTabs = hasArgs || hasObs

  // Reset to first available tab when data changes
  const effectiveTab = activeTab === "obs" && !hasObs && hasArgs ? "args"
    : activeTab === "args" && !hasArgs && hasObs ? "obs"
    : activeTab

  const tabs: { key: TabKey; label: string; icon: typeof ArrowUpRight; available: boolean }[] = [
    { key: "args", label: "Input", icon: ArrowUpRight, available: !!hasArgs },
    { key: "obs", label: "Output", icon: ArrowDownLeft, available: hasObs },
  ]

  return (
    <Sheet open={open} onOpenChange={(v) => { if (!v) { onClose(); setActiveTab("args") } }}>
      <SheetContent
        side="right"
        className="sm:max-w-2xl w-full flex flex-col p-0 gap-0"
      >
        {data && (
          <>
            {/* Header */}
            <div className="shrink-0 px-6 pt-6 pb-4 border-b border-border/40">
              <SheetHeader className="gap-1">
                <SheetTitle className="flex items-center gap-2.5 text-base">
                  <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-amber-500/10">
                    {isTool ? (
                      <Wrench className="h-3.5 w-3.5 text-amber-500" />
                    ) : (
                      <Brain className="h-3.5 w-3.5 text-amber-500" />
                    )}
                  </div>
                  <span className="truncate font-semibold">{displayName}</span>
                </SheetTitle>
                <SheetDescription asChild>
                  <div className="flex items-center gap-3 flex-wrap pl-9">
                    {summary && (
                      <span className="text-xs text-muted-foreground">{summary}</span>
                    )}
                    {data.duration != null && (
                      <span className="flex items-center gap-1 text-xs text-muted-foreground">
                        <Clock className="h-3 w-3" />
                        {fmtDuration(data.duration)}
                      </span>
                    )}
                  </div>
                </SheetDescription>
              </SheetHeader>

              {/* Tab bar */}
              {hasTabs && (
                <div className="flex items-center gap-px mt-4 rounded-lg border border-border/40 bg-muted/20 w-fit overflow-hidden">
                  {tabs.filter((t) => t.available).map((t) => {
                    const TabIcon = t.icon
                    return (
                      <button
                        key={t.key}
                        type="button"
                        onClick={() => setActiveTab(t.key)}
                        className={cn(
                          "flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-medium transition-colors",
                          effectiveTab === t.key
                            ? "bg-amber-500/15 text-amber-500"
                            : "text-muted-foreground hover:bg-muted/40",
                        )}
                      >
                        <TabIcon className="h-3 w-3" />
                        {t.label}
                      </button>
                    )
                  })}
                </div>
              )}
            </div>

            {/* Scrollable content */}
            <ScrollArea className="flex-1 min-h-0">
              <div className="px-6 py-4 space-y-4 overflow-hidden">
                {/* Reasoning */}
                {data.reasoning && (
                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-1.5 uppercase tracking-wider">
                      Reasoning
                    </p>
                    <p className="text-sm text-muted-foreground leading-relaxed italic">
                      {data.reasoning}
                    </p>
                  </div>
                )}

                {/* Tab content */}
                {effectiveTab === "args" && hasArgs && (
                  <ToolArgsBlock args={data.tool_args!} hideLabel />
                )}
                {effectiveTab === "obs" && hasObs && (
                  <ObservationBlock observation={data.observation!} hideLabel />
                )}

                {/* Error — always visible */}
                {data.error && (
                  <ErrorBlock error={data.error} />
                )}
              </div>
            </ScrollArea>
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}
