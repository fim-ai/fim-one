"use client"

import { useState } from "react"
import { useTranslations } from "next-intl"
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
import { useToolCatalog } from "@/hooks/use-tool-catalog"
import { MarkdownContent } from "@/lib/markdown"
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
  const t = useTranslations("dag")
  const { data: catalog } = useToolCatalog()
  const [activeTab, setActiveTab] = useState<TabKey>("args")

  const open = !!data
  const isTool = !!data?.tool_name
  const displayName = isTool && data?.tool_name
    ? getToolDisplayName(data.tool_name, catalog?.tools)
    : t("thinking")

  const hasArgs = data?.tool_args && Object.keys(data.tool_args).length > 0
  const hasObs = !!data?.observation
  const hasTabs = hasArgs || hasObs

  // Reset to first available tab when data changes
  const effectiveTab = activeTab === "obs" && !hasObs && hasArgs ? "args"
    : activeTab === "args" && !hasArgs && hasObs ? "obs"
    : activeTab

  const tabs: { key: TabKey; label: string; icon: typeof ArrowUpRight; available: boolean }[] = [
    { key: "args", label: t("input"), icon: ArrowUpRight, available: !!hasArgs },
    { key: "obs", label: t("output"), icon: ArrowDownLeft, available: hasObs },
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
            <div className="shrink-0 px-6 pt-5 pb-3 border-b border-border/40 space-y-3">
              <SheetHeader>
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
                <SheetDescription className="sr-only">
                  {t("toolDetails", { name: displayName })}
                </SheetDescription>
              </SheetHeader>

              {/* Tab bar + stats in one row */}
              {hasTabs && (
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-px rounded-lg border border-border/40 bg-muted/20 overflow-hidden">
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
                  <div className="flex items-center gap-2.5 text-[11px] text-muted-foreground">
                    {summary && <span>{summary}</span>}
                    {data.duration != null && (
                      <span className="flex items-center gap-1 tabular-nums">
                        <Clock className="h-3 w-3" />
                        {fmtDuration(data.duration)}
                      </span>
                    )}
                  </div>
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
                      {t("reasoning")}
                    </p>
                    <MarkdownContent
                      content={data.reasoning}
                      className="prose-sm text-sm text-muted-foreground italic"
                    />
                  </div>
                )}

                {/* Tab content */}
                {effectiveTab === "args" && hasArgs && (
                  <ToolArgsBlock args={data.tool_args!} hideLabel />
                )}
                {effectiveTab === "obs" && hasObs && (
                  <ObservationBlock
                    observation={data.observation!}
                    hideLabel
                    contentType={data.content_type}
                    artifacts={data.artifacts}
                  />
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
