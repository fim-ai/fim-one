"use client"

import { useState, useEffect, useCallback, useMemo } from "react"
import { useTranslations } from "next-intl"
import {
  Activity,
  BarChart3,
  Clock,
  Loader2,
  TrendingUp,
  AlertTriangle,
  Zap,
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
import { workflowApi } from "@/lib/api"
import { fmtDuration } from "@/lib/utils"
import type {
  WorkflowAnalyticsResponse,
  WorkflowNodeType,
} from "@/types/workflow"

interface AnalyticsPanelProps {
  workflowId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  nodeTypeMap?: Record<string, WorkflowNodeType>
}

type DaysRange = 7 | 14 | 30

function formatDurationMs(ms: number): string {
  const seconds = ms / 1000
  if (seconds < 60) return fmtDuration(seconds)
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = seconds % 60
  if (minutes < 60) {
    return remainingSeconds > 0
      ? `${minutes}m ${fmtDuration(remainingSeconds)}`
      : `${minutes}m`
  }
  const hours = Math.floor(minutes / 60)
  const remainingMinutes = minutes % 60
  return remainingMinutes > 0
    ? `${hours}h ${remainingMinutes}m`
    : `${hours}h`
}

export function AnalyticsPanel({
  workflowId,
  open,
  onOpenChange,
  nodeTypeMap,
}: AnalyticsPanelProps) {
  const t = useTranslations("workflows")

  const [data, setData] = useState<WorkflowAnalyticsResponse | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [days, setDays] = useState<DaysRange>(14)

  const fetchAnalytics = useCallback(
    (range: DaysRange) => {
      if (!workflowId) return
      setIsLoading(true)
      workflowApi
        .getAnalytics(workflowId, range)
        .then((resp) => setData(resp))
        .catch(() => toast.error(t("analyticsLoadFailed")))
        .finally(() => setIsLoading(false))
    },
    [workflowId, t],
  )

  useEffect(() => {
    if (open) fetchAnalytics(days)
  }, [open, days, fetchAnalytics])

  const handleDaysChange = (newDays: DaysRange) => {
    setDays(newDays)
  }

  const hasRuns = data !== null && data.total_runs > 0

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="sm:max-w-md p-0 flex flex-col">
        <SheetHeader className="px-6 pt-6 pb-3 border-b border-border/40 shrink-0">
          <SheetTitle className="text-sm flex items-center gap-2">
            <BarChart3 className="h-4 w-4" />
            {t("analyticsTitle")}
          </SheetTitle>
          <SheetDescription className="text-xs">
            {t("analyticsDescription")}
          </SheetDescription>
        </SheetHeader>

        {/* Days range selector */}
        <div className="px-6 py-2 border-b border-border/40 shrink-0">
          <DaysRangeSelector days={days} onChange={handleDaysChange} t={t} />
        </div>

        <ScrollArea className="flex-1 min-h-0">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : !hasRuns ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Activity className="h-8 w-8 mb-2 opacity-40" />
              <p className="text-sm text-center px-6">{t("analyticsNoRuns")}</p>
            </div>
          ) : (
            <div className="p-4 space-y-5">
              {/* Top stat cards */}
              <TopStatCards data={data} t={t} />

              {/* Status distribution */}
              <StatusDistribution data={data} t={t} />

              {/* Runs per day chart */}
              <RunsPerDayChart data={data} days={days} t={t} />

              {/* Duration percentiles */}
              <DurationPercentiles data={data} t={t} />

              {/* Most failed nodes */}
              <MostFailedNodes data={data} nodeTypeMap={nodeTypeMap} t={t} />
            </div>
          )}
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}

/* ---------- Sub-components ---------- */

function DaysRangeSelector({
  days,
  onChange,
  t,
}: {
  days: DaysRange
  onChange: (d: DaysRange) => void
  t: ReturnType<typeof useTranslations<"workflows">>
}) {
  const options: DaysRange[] = [7, 14, 30]
  const labels: Record<DaysRange, string> = {
    7: t("analyticsRangeLast7d"),
    14: t("analyticsRangeLast14d"),
    30: t("analyticsRangeLast30d"),
  }

  return (
    <div className="flex items-center gap-1">
      {options.map((d) => (
        <button
          key={d}
          onClick={() => onChange(d)}
          className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
            days === d
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground hover:bg-muted"
          }`}
        >
          {labels[d]}
        </button>
      ))}
    </div>
  )
}

function TopStatCards({
  data,
  t,
}: {
  data: WorkflowAnalyticsResponse
  t: ReturnType<typeof useTranslations<"workflows">>
}) {
  return (
    <div className="grid grid-cols-2 gap-2">
      <StatCard
        icon={<Activity className="h-3.5 w-3.5 text-blue-500" />}
        label={t("analyticsTotalRuns")}
        value={String(data.total_runs)}
      />
      <StatCard
        icon={<TrendingUp className="h-3.5 w-3.5 text-emerald-500" />}
        label={t("analyticsSuccessRate")}
        value={`${data.success_rate.toFixed(1)}%`}
      />
      <StatCard
        icon={<Clock className="h-3.5 w-3.5 text-amber-500" />}
        label={t("analyticsAvgDuration")}
        value={formatDurationMs(data.avg_duration_ms)}
      />
      <StatCard
        icon={<Zap className="h-3.5 w-3.5 text-purple-500" />}
        label={t("analyticsP95Duration")}
        value={formatDurationMs(data.p95_duration_ms)}
      />
    </div>
  )
}

function StatCard({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode
  label: string
  value: string
}) {
  return (
    <div className="rounded-md border border-border p-3">
      <div className="flex items-center gap-1.5 mb-1">
        {icon}
        <span className="text-[11px] text-muted-foreground truncate">
          {label}
        </span>
      </div>
      <span className="text-lg font-semibold text-foreground tabular-nums">
        {value}
      </span>
    </div>
  )
}

function StatusDistribution({
  data,
  t,
}: {
  data: WorkflowAnalyticsResponse
  t: ReturnType<typeof useTranslations<"workflows">>
}) {
  const dist = data.status_distribution
  const total = data.total_runs
  if (total === 0) return null

  const segments: { key: string; count: number; color: string; label: string }[] = [
    {
      key: "completed",
      count: dist.completed ?? 0,
      color: "bg-emerald-500",
      label: t("analyticsCompleted"),
    },
    {
      key: "failed",
      count: dist.failed ?? 0,
      color: "bg-red-500",
      label: t("analyticsFailed"),
    },
    {
      key: "cancelled",
      count: dist.cancelled ?? 0,
      color: "bg-zinc-400",
      label: t("analyticsCancelled"),
    },
  ]

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-medium text-muted-foreground">
        {t("analyticsStatusDistribution")}
      </h3>

      {/* Stacked bar */}
      <div className="flex h-2.5 w-full rounded-full overflow-hidden bg-muted">
        {segments.map(
          (seg) =>
            seg.count > 0 && (
              <div
                key={seg.key}
                className={`${seg.color} transition-all duration-500`}
                style={{ width: `${(seg.count / total) * 100}%` }}
              />
            ),
        )}
      </div>

      {/* Legend */}
      <div className="flex items-center gap-3 flex-wrap">
        {segments.map(
          (seg) =>
            seg.count > 0 && (
              <div
                key={seg.key}
                className="flex items-center gap-1.5 text-xs text-muted-foreground"
              >
                <span
                  className={`inline-block h-2 w-2 rounded-full ${seg.color}`}
                />
                <span>
                  {seg.label} {seg.count}
                </span>
              </div>
            ),
        )}
      </div>
    </div>
  )
}

function RunsPerDayChart({
  data,
  days,
  t,
}: {
  data: WorkflowAnalyticsResponse
  days: DaysRange
  t: ReturnType<typeof useTranslations<"workflows">>
}) {
  const runsPerDay = data.runs_per_day
  // Take last N days based on selected range
  const displayDays = useMemo(
    () => (runsPerDay ? runsPerDay.slice(-days) : []),
    [runsPerDay, days],
  )
  const maxCount = useMemo(
    () => Math.max(...displayDays.map((d) => d.count), 1),
    [displayDays],
  )

  if (!runsPerDay || runsPerDay.length === 0) return null

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-medium text-muted-foreground">
        {t("analyticsRunsPerDay")}
      </h3>

      <div className="flex items-end gap-[2px] h-24">
        {displayDays.map((day) => {
          const heightPct = (day.count / maxCount) * 100
          const completedPct =
            day.count > 0 ? (day.completed / day.count) * 100 : 0
          const failedPct =
            day.count > 0 ? (day.failed / day.count) * 100 : 0

          // Format date label — show only day of month
          const dayLabel = new Date(day.date + "T00:00:00").getDate()

          return (
            <div
              key={day.date}
              className="flex-1 flex flex-col items-center gap-0.5 min-w-0 group relative"
            >
              {/* Bar */}
              <div
                className="w-full rounded-t-sm overflow-hidden flex flex-col-reverse transition-all duration-300"
                style={{ height: `${Math.max(heightPct, 2)}%` }}
              >
                {/* Completed portion */}
                <div
                  className="w-full bg-emerald-500/80"
                  style={{ height: `${completedPct}%` }}
                />
                {/* Failed portion */}
                <div
                  className="w-full bg-red-500/80"
                  style={{ height: `${failedPct}%` }}
                />
                {/* Remaining (cancelled etc) */}
                <div
                  className="w-full bg-zinc-400/60 flex-1"
                />
              </div>

              {/* Date label — show selectively to avoid clutter */}
              {(displayDays.length <= 14 ||
                displayDays.indexOf(day) % Math.ceil(displayDays.length / 10) === 0 ||
                displayDays.indexOf(day) === displayDays.length - 1) && (
                <span className="text-[9px] text-muted-foreground tabular-nums">
                  {dayLabel}
                </span>
              )}

              {/* Tooltip on hover */}
              <div className="absolute -top-8 left-1/2 -translate-x-1/2 hidden group-hover:flex items-center bg-popover border border-border rounded px-1.5 py-0.5 shadow-sm z-10 whitespace-nowrap">
                <span className="text-[10px] text-foreground tabular-nums">
                  {day.date}: {day.count} {t("analyticsRuns")}
                </span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function DurationPercentiles({
  data,
  t,
}: {
  data: WorkflowAnalyticsResponse
  t: ReturnType<typeof useTranslations<"workflows">>
}) {
  const items = [
    { label: t("analyticsP50Duration"), value: data.p50_duration_ms },
    { label: t("analyticsAvgDuration"), value: data.avg_duration_ms },
    { label: t("analyticsP95Duration"), value: data.p95_duration_ms },
    { label: t("analyticsP99Duration"), value: data.p99_duration_ms },
  ]

  const maxMs = Math.max(...items.map((i) => i.value), 1)

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
        <Clock className="h-3 w-3" />
        {t("analyticsAvgDuration")}
      </h3>

      <div className="space-y-1.5">
        {items.map((item) => (
          <div key={item.label} className="flex items-center gap-2">
            <span className="text-[11px] text-muted-foreground w-20 shrink-0 truncate">
              {item.label}
            </span>
            <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-amber-500/70 transition-all duration-500"
                style={{ width: `${(item.value / maxMs) * 100}%` }}
              />
            </div>
            <span className="text-[11px] text-foreground tabular-nums w-14 text-right shrink-0">
              {formatDurationMs(item.value)}
            </span>
          </div>
        ))}
      </div>

      {/* Avg nodes per run */}
      <div className="flex items-center justify-between text-xs pt-1 border-t border-border/40">
        <span className="text-muted-foreground">
          {t("analyticsAvgNodesPerRun")}
        </span>
        <span className="font-medium tabular-nums">
          {data.avg_nodes_per_run.toFixed(1)}
        </span>
      </div>
    </div>
  )
}

function MostFailedNodes({
  data,
  nodeTypeMap,
  t,
}: {
  data: WorkflowAnalyticsResponse
  nodeTypeMap?: Record<string, WorkflowNodeType>
  t: ReturnType<typeof useTranslations<"workflows">>
}) {
  const nodes = data.most_failed_nodes
  if (!nodes || nodes.length === 0) return null

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
        <AlertTriangle className="h-3 w-3" />
        {t("analyticsMostFailedNodes")}
      </h3>

      <div className="rounded-md border border-border overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border/40 bg-muted/30">
              <th className="text-left font-medium text-muted-foreground px-3 py-1.5">
                {t("analyticsNodeId")}
              </th>
              <th className="text-right font-medium text-muted-foreground px-3 py-1.5">
                {t("analyticsFailures")}
              </th>
            </tr>
          </thead>
          <tbody>
            {nodes.map((node) => {
              const nodeType = nodeTypeMap?.[node.node_id]
              const nodeLabel = nodeType
                ? t(`nodeType_${nodeType}` as Parameters<typeof t>[0])
                : null

              return (
                <tr
                  key={node.node_id}
                  className="border-b border-border/20 last:border-0"
                >
                  <td className="px-3 py-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className="text-foreground truncate max-w-[120px]">
                        {node.node_id}
                      </span>
                      {nodeLabel && (
                        <Badge
                          variant="secondary"
                          className="text-[9px] px-1 py-0 h-4 shrink-0"
                        >
                          {nodeLabel}
                        </Badge>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    <span className="text-red-500 font-medium tabular-nums">
                      {node.failure_count}
                    </span>
                    <span className="text-muted-foreground ml-1">
                      {t("analyticsOfTotal", { total: node.total_runs })}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
