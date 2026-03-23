"use client"

import { useMemo } from "react"
import { useTranslations } from "next-intl"
import {
  CheckCircle2,
  XCircle,
  Loader2,
  CircleDashed,
  Clock,
  SkipForward,
  Ban,
  RotateCw,
  ArrowUp,
  ArrowDown,
  Equal,
} from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { fmtDuration } from "@/lib/utils"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import type {
  WorkflowRunResponse,
  NodeRunResult,
  NodeRunStatus,
  WorkflowNodeType,
} from "@/types/workflow"

interface RunComparisonDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  runA: WorkflowRunResponse | null
  runB: WorkflowRunResponse | null
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

/** Format milliseconds into a readable duration string */
function fmtMs(ms: number | null): string {
  if (ms == null) return "-"
  return fmtDuration(ms / 1000)
}

interface NodeComparisonRow {
  nodeId: string
  resultA: NodeRunResult | null
  resultB: NodeRunResult | null
  statusDiffers: boolean
  durationDiffMs: number | null
}

interface InputDiffEntry {
  key: string
  category: "only_a" | "only_b" | "differed" | "same"
  valueA?: unknown
  valueB?: unknown
}

export function RunComparisonDialog({
  open,
  onOpenChange,
  runA,
  runB,
  nodeTypeMap,
}: RunComparisonDialogProps) {
  const t = useTranslations("workflows")
  const { formatRelativeTime } = useDateFormatter()

  // Build the union of all node IDs from both runs
  const nodeRows = useMemo<NodeComparisonRow[]>(() => {
    if (!runA || !runB) return []
    const nodesA = runA.node_results ?? {}
    const nodesB = runB.node_results ?? {}
    const allNodeIds = new Set([...Object.keys(nodesA), ...Object.keys(nodesB)])

    return Array.from(allNodeIds).map((nodeId) => {
      const rA = nodesA[nodeId] ?? null
      const rB = nodesB[nodeId] ?? null
      const statusDiffers = rA != null && rB != null && rA.status !== rB.status
      let durationDiffMs: number | null = null
      if (rA?.duration_ms != null && rB?.duration_ms != null) {
        durationDiffMs = rB.duration_ms - rA.duration_ms
      }
      return { nodeId, resultA: rA, resultB: rB, statusDiffers, durationDiffMs }
    })
  }, [runA, runB])

  // Build inputs diff
  const inputDiffs = useMemo<InputDiffEntry[]>(() => {
    if (!runA || !runB) return []
    const inputsA = runA.inputs ?? {}
    const inputsB = runB.inputs ?? {}
    const allKeys = new Set([...Object.keys(inputsA), ...Object.keys(inputsB)])
    const entries: InputDiffEntry[] = []

    for (const key of allKeys) {
      const inA = key in inputsA
      const inB = key in inputsB
      if (inA && !inB) {
        entries.push({ key, category: "only_a", valueA: inputsA[key] })
      } else if (!inA && inB) {
        entries.push({ key, category: "only_b", valueB: inputsB[key] })
      } else {
        const same = JSON.stringify(inputsA[key]) === JSON.stringify(inputsB[key])
        entries.push({
          key,
          category: same ? "same" : "differed",
          valueA: inputsA[key],
          valueB: inputsB[key],
        })
      }
    }
    return entries
  }, [runA, runB])

  const hasInputDiffs = inputDiffs.some((d) => d.category !== "same")

  const getNodeLabel = (nodeId: string): string => {
    const nodeType = nodeTypeMap[nodeId]
    if (nodeType) {
      return t(`nodeType_${nodeType}` as Parameters<typeof t>[0])
    }
    return nodeId
  }

  if (!runA || !runB) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="text-base">{t("compareTitle")}</DialogTitle>
          <DialogDescription className="text-xs">
            {t("compareDescription")}
          </DialogDescription>
        </DialogHeader>

        <ScrollArea className="flex-1 min-h-0">
          <div className="space-y-6 pb-4">
            {/* Run summary header: two columns */}
            <div className="grid grid-cols-2 gap-4">
              <RunSummaryCard
                label={t("compareRunA")}
                run={runA}
                formatRelativeTime={formatRelativeTime}
                t={t}
              />
              <RunSummaryCard
                label={t("compareRunB")}
                run={runB}
                formatRelativeTime={formatRelativeTime}
                t={t}
              />
            </div>

            {/* Inputs diff section */}
            {inputDiffs.length > 0 && (
              <div className="space-y-2">
                <h3 className="text-xs font-semibold">
                  {t("compareInputsDiff")}
                </h3>
                {!hasInputDiffs ? (
                  <p className="text-xs text-muted-foreground">
                    {t("compareIdenticalInputs")}
                  </p>
                ) : (
                  <div className="rounded-md border border-border overflow-hidden">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-border bg-muted/50">
                          <th className="text-left font-medium text-muted-foreground px-3 py-1.5">
                            {t("configKey")}
                          </th>
                          <th className="text-left font-medium text-muted-foreground px-3 py-1.5">
                            {t("compareRunA")}
                          </th>
                          <th className="text-left font-medium text-muted-foreground px-3 py-1.5">
                            {t("compareRunB")}
                          </th>
                          <th className="text-right font-medium text-muted-foreground px-3 py-1.5">
                            {t("compareDiff")}
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {inputDiffs
                          .filter((d) => d.category !== "same")
                          .map((entry) => (
                            <tr
                              key={entry.key}
                              className="border-b last:border-b-0 border-border"
                            >
                              <td className="px-3 py-1.5 font-mono text-[10px]">
                                {entry.key}
                              </td>
                              <td className="px-3 py-1.5 font-mono text-[10px] max-w-[200px] truncate">
                                {entry.valueA != null
                                  ? truncateValue(entry.valueA)
                                  : <span className="text-muted-foreground">-</span>}
                              </td>
                              <td className="px-3 py-1.5 font-mono text-[10px] max-w-[200px] truncate">
                                {entry.valueB != null
                                  ? truncateValue(entry.valueB)
                                  : <span className="text-muted-foreground">-</span>}
                              </td>
                              <td className="px-3 py-1.5 text-right">
                                <InputDiffBadge category={entry.category} t={t} />
                              </td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {/* Node comparison table */}
            <div className="space-y-2">
              <h3 className="text-xs font-semibold">
                {t("compareNodeResults")}
              </h3>
              {nodeRows.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  {t("compareNoNodes")}
                </p>
              ) : (
                <div className="rounded-md border border-border overflow-hidden">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-border bg-muted/50">
                        <th className="text-left font-medium text-muted-foreground px-3 py-1.5">
                          {t("compareNodeId")}
                        </th>
                        <th className="text-center font-medium text-muted-foreground px-3 py-1.5">
                          {t("compareRunA")} {t("compareNodeStatus")}
                        </th>
                        <th className="text-center font-medium text-muted-foreground px-3 py-1.5">
                          {t("compareRunA")} {t("compareNodeDuration")}
                        </th>
                        <th className="text-center font-medium text-muted-foreground px-3 py-1.5">
                          {t("compareRunB")} {t("compareNodeStatus")}
                        </th>
                        <th className="text-center font-medium text-muted-foreground px-3 py-1.5">
                          {t("compareRunB")} {t("compareNodeDuration")}
                        </th>
                        <th className="text-right font-medium text-muted-foreground px-3 py-1.5">
                          {t("compareDiff")}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {nodeRows.map((row) => (
                        <tr
                          key={row.nodeId}
                          className={cn(
                            "border-b last:border-b-0 border-border",
                            row.statusDiffers && "bg-amber-500/5",
                          )}
                        >
                          <td className="px-3 py-1.5">
                            <div className="flex flex-col">
                              <span className="font-medium text-foreground">
                                {getNodeLabel(row.nodeId)}
                              </span>
                              {nodeTypeMap[row.nodeId] && (
                                <span className="text-[10px] text-muted-foreground">
                                  {row.nodeId}
                                </span>
                              )}
                            </div>
                          </td>
                          <td className="px-3 py-1.5 text-center">
                            {row.resultA ? (
                              <div className="flex items-center justify-center gap-1">
                                {nodeStatusIcons[row.resultA.status]}
                                <span className="text-[10px]">
                                  {t(
                                    `runStatus_${row.resultA.status}` as Parameters<typeof t>[0],
                                  )}
                                </span>
                              </div>
                            ) : (
                              <span className="text-muted-foreground">-</span>
                            )}
                          </td>
                          <td className="px-3 py-1.5 text-center tabular-nums">
                            {row.resultA?.duration_ms != null
                              ? fmtMs(row.resultA.duration_ms)
                              : <span className="text-muted-foreground">{t("compareNoDuration")}</span>}
                          </td>
                          <td className="px-3 py-1.5 text-center">
                            {row.resultB ? (
                              <div className="flex items-center justify-center gap-1">
                                {nodeStatusIcons[row.resultB.status]}
                                <span className="text-[10px]">
                                  {t(
                                    `runStatus_${row.resultB.status}` as Parameters<typeof t>[0],
                                  )}
                                </span>
                              </div>
                            ) : (
                              <span className="text-muted-foreground">-</span>
                            )}
                          </td>
                          <td className="px-3 py-1.5 text-center tabular-nums">
                            {row.resultB?.duration_ms != null
                              ? fmtMs(row.resultB.duration_ms)
                              : <span className="text-muted-foreground">{t("compareNoDuration")}</span>}
                          </td>
                          <td className="px-3 py-1.5 text-right">
                            <DurationDiffCell row={row} t={t} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}

// --- Sub-components ---

function RunSummaryCard({
  label,
  run,
  formatRelativeTime,
  t,
}: {
  label: string
  run: WorkflowRunResponse
  formatRelativeTime: (dateStr: string | null | undefined, fallback?: string) => string
  t: ReturnType<typeof useTranslations<"workflows">>
}) {
  return (
    <div className="rounded-md border border-border p-3 space-y-2">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <div className="flex items-center gap-2">
        {runStatusIcons[run.status]}
        <Badge
          variant="secondary"
          className={cn(
            "text-[10px] px-1.5 py-0 h-5",
            statusBadgeClass[run.status],
          )}
        >
          {t(`runStatus_${run.status}` as Parameters<typeof t>[0])}
        </Badge>
      </div>
      <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
        <span className="flex items-center gap-1">
          <Clock className="h-3 w-3" />
          {run.duration_ms != null ? fmtMs(run.duration_ms) : t("compareNoDuration")}
        </span>
        <span>{formatRelativeTime(run.created_at)}</span>
      </div>
    </div>
  )
}

function InputDiffBadge({
  category,
  t,
}: {
  category: InputDiffEntry["category"]
  t: ReturnType<typeof useTranslations<"workflows">>
}) {
  switch (category) {
    case "only_a":
      return (
        <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-5 bg-amber-500/15 text-amber-600 dark:text-amber-400">
          {t("compareOnlyInA")}
        </Badge>
      )
    case "only_b":
      return (
        <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-5 bg-blue-500/15 text-blue-600 dark:text-blue-400">
          {t("compareOnlyInB")}
        </Badge>
      )
    case "differed":
      return (
        <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-5 bg-orange-500/15 text-orange-600 dark:text-orange-400">
          {t("compareDiffered")}
        </Badge>
      )
    default:
      return null
  }
}

function DurationDiffCell({
  row,
  t,
}: {
  row: NodeComparisonRow
  t: ReturnType<typeof useTranslations<"workflows">>
}) {
  // Node only in one run
  if (!row.resultA && row.resultB) {
    return (
      <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-5 bg-blue-500/15 text-blue-600 dark:text-blue-400">
        {t("compareNew")}
      </Badge>
    )
  }
  if (row.resultA && !row.resultB) {
    return (
      <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-5 bg-amber-500/15 text-amber-600 dark:text-amber-400">
        {t("compareMissing")}
      </Badge>
    )
  }

  // Status differs
  if (row.statusDiffers) {
    // Determine if improved or regressed
    const statusA = row.resultA!.status
    const statusB = row.resultB!.status
    const isImprovement = statusA === "failed" && statusB === "completed"
    const isRegression = statusA === "completed" && statusB === "failed"

    if (isImprovement) {
      return (
        <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-5 bg-green-500/15 text-green-600 dark:text-green-400">
          <ArrowUp className="h-3 w-3 mr-0.5" />
          {t("compareImproved")}
        </Badge>
      )
    }
    if (isRegression) {
      return (
        <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-5 bg-red-500/15 text-red-600 dark:text-red-400">
          <ArrowDown className="h-3 w-3 mr-0.5" />
          {t("compareRegressed")}
        </Badge>
      )
    }
    // Other status difference
    return (
      <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-5 bg-amber-500/15 text-amber-600 dark:text-amber-400">
        {t("compareDiffered")}
      </Badge>
    )
  }

  // Duration diff
  if (row.durationDiffMs != null) {
    const absDiff = Math.abs(row.durationDiffMs)
    // If diff is tiny (< 10ms), consider same
    if (absDiff < 10) {
      return (
        <span className="text-[10px] text-muted-foreground flex items-center justify-end gap-0.5">
          <Equal className="h-3 w-3" />
          {t("compareSame")}
        </span>
      )
    }
    const isImproved = row.durationDiffMs < 0 // Run B was faster
    const diffStr = fmtMs(absDiff)

    return (
      <span
        className={cn(
          "text-[10px] tabular-nums flex items-center justify-end gap-0.5 font-medium",
          isImproved
            ? "text-green-600 dark:text-green-400"
            : "text-red-600 dark:text-red-400",
        )}
      >
        {isImproved ? (
          <ArrowDown className="h-3 w-3" />
        ) : (
          <ArrowUp className="h-3 w-3" />
        )}
        {isImproved ? `-${diffStr}` : `+${diffStr}`}
      </span>
    )
  }

  // No useful diff
  return <span className="text-muted-foreground">-</span>
}

function truncateValue(val: unknown): string {
  const str = typeof val === "string" ? val : JSON.stringify(val)
  return str.length > 50 ? str.slice(0, 50) + "..." : str
}
