"use client"

import { useState, useEffect, useCallback, useMemo } from "react"
import { useTranslations, useLocale } from "next-intl"
import { formatDistanceToNow } from "date-fns"
import { zhCN, enUS } from "date-fns/locale"
import {
  CheckCircle2,
  XCircle,
  Loader2,
  CircleDashed,
  Clock,
  ArrowLeft,
  SkipForward,
  ChevronDown,
  Ban,
  RotateCw,
} from "lucide-react"
import { toast } from "sonner"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { fmtDuration } from "@/lib/utils"
import { workflowApi } from "@/lib/api"
import type {
  WorkflowRunResponse,
  NodeRunResult,
  NodeRunStatus,
  WorkflowNodeType,
} from "@/types/workflow"

interface RunHistorySheetProps {
  workflowId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Map of nodeId -> node type for display labels in detail view */
  nodeTypeMap: Record<string, WorkflowNodeType>
}

const runStatusIcons: Record<WorkflowRunResponse["status"], React.ReactNode> = {
  pending: <CircleDashed className="h-4 w-4 text-zinc-500" />,
  running: <Loader2 className="h-4 w-4 text-blue-500 animate-spin" />,
  completed: <CheckCircle2 className="h-4 w-4 text-green-500" />,
  failed: <XCircle className="h-4 w-4 text-red-500" />,
  cancelled: <Ban className="h-4 w-4 text-zinc-400" />,
}

const nodeStatusIcons: Record<NodeRunStatus, React.ReactNode> = {
  pending: <CircleDashed className="h-3.5 w-3.5 text-zinc-500" />,
  running: <Loader2 className="h-3.5 w-3.5 text-blue-500 animate-spin" />,
  completed: <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />,
  failed: <XCircle className="h-3.5 w-3.5 text-red-500" />,
  skipped: <SkipForward className="h-3.5 w-3.5 text-zinc-400" />,
  retrying: <RotateCw className="h-3.5 w-3.5 text-amber-500 animate-spin" />,
}

const statusBadgeClass: Record<WorkflowRunResponse["status"], string> = {
  pending: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400",
  running: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  completed: "bg-green-500/15 text-green-600 dark:text-green-400",
  failed: "bg-red-500/15 text-red-600 dark:text-red-400",
  cancelled: "bg-zinc-500/15 text-zinc-500 dark:text-zinc-400",
}

/** Collapsible JSON viewer for node outputs (detail view) */
function NodeOutputCollapsible({ output }: { output: unknown }) {
  const t = useTranslations("workflows")
  const [expanded, setExpanded] = useState(false)

  const formatted = useMemo(() => {
    if (typeof output === "string") return output
    return JSON.stringify(output, null, 2)
  }, [output])

  const isLong = formatted.length > 100 || formatted.includes("\n")

  if (!isLong) {
    return (
      <pre className="text-[10px] text-muted-foreground font-mono mt-0.5 whitespace-pre-wrap break-all">
        {formatted}
      </pre>
    )
  }

  return (
    <div className="mt-0.5">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-0.5 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
      >
        <ChevronDown
          className={cn(
            "h-3 w-3 transition-transform duration-200",
            expanded && "rotate-180",
          )}
        />
        {expanded ? t("runPanelHideOutput") : t("runPanelShowOutput")}
      </button>
      {expanded ? (
        <pre className="text-[10px] text-muted-foreground font-mono mt-1 whitespace-pre-wrap break-all p-1.5 rounded border border-border bg-muted/30 max-h-[160px] overflow-auto">
          {formatted}
        </pre>
      ) : (
        <pre className="text-[10px] text-muted-foreground font-mono mt-0.5 line-clamp-2 whitespace-pre-wrap break-all">
          {formatted}
        </pre>
      )}
    </div>
  )
}

function relativeTime(dateStr: string, locale: string): string {
  try {
    const date = new Date(dateStr)
    const dateFnsLocale = locale.startsWith("zh") ? zhCN : enUS
    return formatDistanceToNow(date, { addSuffix: true, locale: dateFnsLocale })
  } catch {
    return dateStr
  }
}

function inputSummary(inputs: Record<string, unknown> | null): string {
  if (!inputs) return ""
  const keys = Object.keys(inputs)
  if (keys.length === 0) return ""
  const entries = keys.slice(0, 3).map((k) => {
    const v = inputs[k]
    const str = typeof v === "string" ? v : JSON.stringify(v)
    return `${k}: ${str.length > 30 ? str.slice(0, 30) + "..." : str}`
  })
  if (keys.length > 3) entries.push(`+${keys.length - 3} more`)
  return entries.join(", ")
}

export function RunHistorySheet({
  workflowId,
  open,
  onOpenChange,
  nodeTypeMap,
}: RunHistorySheetProps) {
  const t = useTranslations("workflows")
  const locale = useLocale()

  const [runs, setRuns] = useState<WorkflowRunResponse[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [selectedRun, setSelectedRun] = useState<WorkflowRunResponse | null>(null)
  const [isLoadingDetail, setIsLoadingDetail] = useState(false)

  // Load runs when sheet opens
  useEffect(() => {
    if (!open || !workflowId) return
    let cancelled = false
    setIsLoading(true)
    setSelectedRun(null)
    workflowApi
      .getRuns(workflowId)
      .then((data) => {
        if (!cancelled) setRuns(data.items)
      })
      .catch(() => {
        if (!cancelled) toast.error(t("historyLoadFailed"))
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, workflowId, t])

  const handleSelectRun = useCallback(
    async (run: WorkflowRunResponse) => {
      setIsLoadingDetail(true)
      try {
        const detail = await workflowApi.getRun(workflowId, run.id)
        setSelectedRun(detail)
      } catch {
        toast.error(t("historyRunDetailFailed"))
      } finally {
        setIsLoadingDetail(false)
      }
    },
    [workflowId, t],
  )

  const getNodeLabel = useCallback(
    (nodeId: string): string => {
      const nodeType = nodeTypeMap[nodeId]
      if (nodeType) {
        return t(`nodeType_${nodeType}` as Parameters<typeof t>[0])
      }
      return nodeId
    },
    [nodeTypeMap, t],
  )

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="sm:max-w-md p-0 flex flex-col">
        <SheetHeader className="px-6 pt-6 pb-3 border-b border-border/40 shrink-0">
          {selectedRun ? (
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => setSelectedRun(null)}
              >
                <ArrowLeft className="h-4 w-4" />
              </Button>
              <div className="flex-1 min-w-0">
                <SheetTitle className="text-sm">
                  {relativeTime(selectedRun.created_at, locale)}
                </SheetTitle>
                <SheetDescription className="text-xs">
                  {t(`runStatus_${selectedRun.status}` as Parameters<typeof t>[0])}
                  {selectedRun.duration_ms != null &&
                    ` -- ${fmtDuration(selectedRun.duration_ms / 1000)}`}
                </SheetDescription>
              </div>
            </div>
          ) : (
            <>
              <SheetTitle className="text-sm">{t("historyTitle")}</SheetTitle>
              <SheetDescription className="text-xs">
                {t("historyDescription")}
              </SheetDescription>
            </>
          )}
        </SheetHeader>

        <ScrollArea className="flex-1 min-h-0">
          {isLoading || isLoadingDetail ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : selectedRun ? (
            /* Detail view */
            <div className="p-4 space-y-4">
              {/* Status badge */}
              <div className="flex items-center gap-2">
                {runStatusIcons[selectedRun.status]}
                <Badge
                  variant="secondary"
                  className={cn(
                    "text-[10px] px-1.5 py-0 h-5",
                    statusBadgeClass[selectedRun.status],
                  )}
                >
                  {t(`runStatus_${selectedRun.status}` as Parameters<typeof t>[0])}
                </Badge>
                {selectedRun.duration_ms != null && (
                  <span className="text-[10px] text-muted-foreground tabular-nums flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {fmtDuration(selectedRun.duration_ms / 1000)}
                  </span>
                )}
              </div>

              {/* Inputs */}
              {selectedRun.inputs &&
                Object.keys(selectedRun.inputs).length > 0 && (
                  <div className="space-y-1.5">
                    <p className="text-xs font-medium">{t("historyInputSummary")}</p>
                    <pre className="text-[10px] p-2 rounded-md border border-border bg-muted/50 font-mono overflow-auto max-h-[100px] whitespace-pre-wrap break-all">
                      {JSON.stringify(selectedRun.inputs, null, 2)}
                    </pre>
                  </div>
                )}

              {/* Node results */}
              {selectedRun.node_results &&
                Object.keys(selectedRun.node_results).length > 0 && (
                  <div className="space-y-2">
                    <p className="text-xs font-medium">{t("runPanelNodeResults")}</p>
                    {Object.entries(selectedRun.node_results).map(
                      ([nodeId, result]: [string, NodeRunResult]) => (
                        <div
                          key={nodeId}
                          className="flex items-start gap-2 rounded-md border border-border p-2"
                        >
                          {nodeStatusIcons[result.status]}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-1.5">
                              <p className="text-xs font-medium text-foreground truncate">
                                {getNodeLabel(nodeId)}
                              </p>
                              {nodeTypeMap[nodeId] && (
                                <span className="text-[10px] text-muted-foreground shrink-0">
                                  ({nodeId})
                                </span>
                              )}
                            </div>
                            {result.duration_ms != null && (
                              <p className="text-[10px] text-muted-foreground tabular-nums">
                                {fmtDuration(result.duration_ms)}
                              </p>
                            )}
                            {result.error && (
                              <p className="text-[10px] text-destructive mt-0.5">
                                {result.error}
                              </p>
                            )}
                            {result.output != null &&
                              result.status === "completed" && (
                                <NodeOutputCollapsible output={result.output} />
                              )}
                          </div>
                        </div>
                      ),
                    )}
                  </div>
                )}

              {/* Final outputs */}
              {selectedRun.outputs &&
                Object.keys(selectedRun.outputs).length > 0 && (
                  <div className="space-y-1.5">
                    <p className="text-xs font-medium">{t("runPanelOutput")}</p>
                    <pre className="text-xs p-2 rounded-md border border-border bg-muted/50 font-mono overflow-auto max-h-[120px] whitespace-pre-wrap break-all">
                      {JSON.stringify(selectedRun.outputs, null, 2)}
                    </pre>
                  </div>
                )}

              {/* Error */}
              {selectedRun.error && (
                <div className="space-y-1.5">
                  <p className="text-xs font-medium text-destructive">
                    {t("runPanelError")}
                  </p>
                  <p className="text-xs text-destructive bg-destructive/10 p-2 rounded-md">
                    {selectedRun.error}
                  </p>
                </div>
              )}
            </div>
          ) : runs.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Clock className="h-8 w-8 mb-2 opacity-40" />
              <p className="text-sm">{t("historyEmpty")}</p>
            </div>
          ) : (
            /* Run list */
            <div className="p-2 space-y-1">
              {runs.map((run) => (
                <button
                  key={run.id}
                  type="button"
                  onClick={() => handleSelectRun(run)}
                  className="w-full text-left rounded-md border border-border p-3 hover:bg-accent/50 transition-colors"
                >
                  <div className="flex items-center gap-2">
                    {runStatusIcons[run.status]}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <Badge
                          variant="secondary"
                          className={cn(
                            "text-[10px] px-1.5 py-0 h-5 shrink-0",
                            statusBadgeClass[run.status],
                          )}
                        >
                          {t(`runStatus_${run.status}` as Parameters<typeof t>[0])}
                        </Badge>
                        <span className="text-[10px] text-muted-foreground">
                          {relativeTime(run.created_at, locale)}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 mt-1">
                        {run.duration_ms != null && (
                          <span className="text-[10px] text-muted-foreground tabular-nums flex items-center gap-0.5">
                            <Clock className="h-2.5 w-2.5" />
                            {fmtDuration(run.duration_ms / 1000)}
                          </span>
                        )}
                        {run.inputs && Object.keys(run.inputs).length > 0 && (
                          <span className="text-[10px] text-muted-foreground truncate">
                            {inputSummary(run.inputs)}
                          </span>
                        )}
                        {(!run.inputs || Object.keys(run.inputs).length === 0) && (
                          <span className="text-[10px] text-muted-foreground italic">
                            {t("historyNoInputs")}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}
