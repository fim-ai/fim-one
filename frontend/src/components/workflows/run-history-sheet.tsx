"use client"

import { useState, useEffect, useCallback, useMemo } from "react"
import { useTranslations } from "next-intl"
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
  GitCompareArrows,
  Trash2,
  Download,
  Eye,
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Checkbox } from "@/components/ui/checkbox"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { cn } from "@/lib/utils"
import { fmtDuration } from "@/lib/utils"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import { workflowApi } from "@/lib/api"
import { RunComparisonDialog } from "@/components/workflows/run-comparison-dialog"
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
  /** Callback to overlay a past run's node results on the canvas */
  onViewRunOnCanvas?: (nodeResults: Record<string, NodeRunResult>) => void
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
  onViewRunOnCanvas,
}: RunHistorySheetProps) {
  const t = useTranslations("workflows")
  const tc = useTranslations("common")
  const { formatRelativeTime } = useDateFormatter()

  const [runs, setRuns] = useState<WorkflowRunResponse[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [selectedRun, setSelectedRun] = useState<WorkflowRunResponse | null>(null)
  const [isLoadingDetail, setIsLoadingDetail] = useState(false)
  const [statusFilter, setStatusFilter] = useState<string>("__all__")

  // --- Comparison state ---
  const [selectedForCompare, setSelectedForCompare] = useState<Set<string>>(new Set())
  const [compareDialogOpen, setCompareDialogOpen] = useState(false)
  const [compareRunA, setCompareRunA] = useState<WorkflowRunResponse | null>(null)
  const [compareRunB, setCompareRunB] = useState<WorkflowRunResponse | null>(null)
  const [isLoadingCompare, setIsLoadingCompare] = useState(false)

  // Reset comparison selection when sheet closes or filter changes
  useEffect(() => {
    if (!open) {
      setSelectedForCompare(new Set())
    }
  }, [open])

  const handleToggleCompare = useCallback((runId: string, checked: boolean) => {
    setSelectedForCompare((prev) => {
      const next = new Set(prev)
      if (checked) {
        if (next.size >= 2) return prev // max 2
        next.add(runId)
      } else {
        next.delete(runId)
      }
      return next
    })
  }, [])

  const handleCompare = useCallback(async () => {
    const ids = Array.from(selectedForCompare)
    if (ids.length !== 2) return
    setIsLoadingCompare(true)
    try {
      const [detailA, detailB] = await Promise.all([
        workflowApi.getRun(workflowId, ids[0]),
        workflowApi.getRun(workflowId, ids[1]),
      ])
      setCompareRunA(detailA)
      setCompareRunB(detailB)
      setCompareDialogOpen(true)
    } catch {
      toast.error(t("compareLoadFailed"))
    } finally {
      setIsLoadingCompare(false)
    }
  }, [selectedForCompare, workflowId, t])

  const handleDeleteRun = useCallback(
    async (runId: string) => {
      try {
        await workflowApi.deleteRun(workflowId, runId)
        setRuns((prev) => prev.filter((r) => r.id !== runId))
        setSelectedForCompare((prev) => {
          const next = new Set(prev)
          next.delete(runId)
          return next
        })
        toast.success(t("historyRunDeleted"))
      } catch {
        toast.error(t("historyRunDeleteFailed"))
      }
    },
    [workflowId, t],
  )

  const handleClearRuns = useCallback(async () => {
    try {
      const res = await workflowApi.clearRuns(workflowId)
      const count = res.data?.deleted_count ?? 0
      setRuns((prev) => prev.filter((r) => r.status === "running" || r.status === "pending"))
      setSelectedForCompare(new Set())
      toast.success(t("historyRunsCleared", { count }))
    } catch {
      toast.error(t("historyRunsClearFailed"))
    }
  }, [workflowId, t])

  const [isExporting, setIsExporting] = useState(false)

  const handleExportRuns = useCallback(async () => {
    setIsExporting(true)
    try {
      const data = await workflowApi.exportRuns(workflowId)
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      })
      const url = URL.createObjectURL(blob)
      const dateStr = new Date().toISOString().slice(0, 10)
      const a = document.createElement("a")
      a.href = url
      a.download = `workflow-runs-${workflowId}-${dateStr}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      toast.success(t("exportRunsSuccess"))
    } catch {
      toast.error(t("historyLoadFailed"))
    } finally {
      setIsExporting(false)
    }
  }, [workflowId, t])

  const statusOptions = useMemo(
    () =>
      [
        { value: "__all__", label: t("historyFilterAll") },
        { value: "completed", label: t("historyFilterCompleted") },
        { value: "failed", label: t("historyFilterFailed") },
        { value: "cancelled", label: t("historyFilterCancelled") },
        { value: "running", label: t("historyFilterRunning") },
      ] as const,
    [t],
  )

  // Load runs when sheet opens or filter changes
  useEffect(() => {
    if (!open || !workflowId) return
    let cancelled = false
    setIsLoading(true)
    setSelectedRun(null)
    const apiStatus = statusFilter === "__all__" ? undefined : statusFilter
    workflowApi
      .getRuns(workflowId, 1, 20, apiStatus)
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
  }, [open, workflowId, statusFilter, t])

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
    <>
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
                  {formatRelativeTime(selectedRun.created_at)}
                </SheetTitle>
                <SheetDescription className="text-xs">
                  {t(`runStatus_${selectedRun.status}` as Parameters<typeof t>[0])}
                  {selectedRun.duration_ms != null &&
                    ` -- ${fmtDuration(selectedRun.duration_ms / 1000)}`}
                </SheetDescription>
              </div>
              {onViewRunOnCanvas && selectedRun.node_results && Object.keys(selectedRun.node_results).length > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 gap-1.5 text-xs shrink-0"
                  onClick={() => {
                    onViewRunOnCanvas(selectedRun.node_results!)
                    onOpenChange(false)
                  }}
                >
                  <Eye className="h-3.5 w-3.5" />
                  {t("viewRunOnCanvas")}
                </Button>
              )}
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

        {/* Status filter + Compare button — only visible in list view */}
        {!selectedRun && (
          <div className="px-4 py-2 border-b border-border/40 shrink-0 flex items-center gap-2">
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger size="sm" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {statusOptions.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              size="sm"
              disabled={selectedForCompare.size !== 2 || isLoadingCompare}
              onClick={handleCompare}
              className="shrink-0"
            >
              {isLoadingCompare ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
              ) : (
                <GitCompareArrows className="h-3.5 w-3.5 mr-1.5" />
              )}
              {t("compareButton")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={runs.length === 0 || isExporting}
              onClick={handleExportRuns}
              className="shrink-0"
            >
              {isExporting ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
              ) : (
                <Download className="h-3.5 w-3.5 mr-1.5" />
              )}
              {t("exportRuns")}
            </Button>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={runs.length === 0}
                  className="shrink-0"
                >
                  <Trash2 className="h-3.5 w-3.5 mr-1.5" />
                  {t("historyClearAll")}
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent size="sm">
                <AlertDialogHeader>
                  <AlertDialogTitle>{t("historyClearAllTitle")}</AlertDialogTitle>
                  <AlertDialogDescription>
                    {t("historyClearAllDescription")}
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
                  <AlertDialogAction variant="destructive" onClick={handleClearRuns}>
                    {t("historyClearAll")}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        )}

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
              <p className="text-sm">
                {statusFilter === "__all__"
                  ? t("historyEmpty")
                  : t("historyEmptyFiltered")}
              </p>
            </div>
          ) : (
            /* Run list */
            <div className="p-2 space-y-1">
              {runs.map((run) => (
                <div
                  key={run.id}
                  className="group flex items-center gap-2 rounded-md border border-border p-3 hover:bg-accent/50 transition-colors"
                >
                  <Checkbox
                    checked={selectedForCompare.has(run.id)}
                    disabled={
                      !selectedForCompare.has(run.id) &&
                      selectedForCompare.size >= 2
                    }
                    onCheckedChange={(checked) =>
                      handleToggleCompare(run.id, !!checked)
                    }
                    aria-label={t("compareButton")}
                    className="shrink-0"
                  />
                  <button
                    type="button"
                    onClick={() => handleSelectRun(run)}
                    className="flex-1 min-w-0 text-left"
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
                            {formatRelativeTime(run.created_at)}
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
                  {run.status !== "running" && run.status !== "pending" && (
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-destructive"
                      onClick={(e) => {
                        e.stopPropagation()
                        handleDeleteRun(run.id)
                      }}
                      aria-label={t("historyDeleteRun")}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </div>
              ))}
            </div>
          )}
        </ScrollArea>
      </SheetContent>
    </Sheet>

    <RunComparisonDialog
      open={compareDialogOpen}
      onOpenChange={setCompareDialogOpen}
      runA={compareRunA}
      runB={compareRunB}
      nodeTypeMap={nodeTypeMap}
    />
    </>
  )
}
