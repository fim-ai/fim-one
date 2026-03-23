"use client"

import { useState, useEffect } from "react"
import { useTranslations } from "next-intl"
import {
  CheckCircle2,
  XCircle,
  Loader2,
  Ban,
  BarChart3,
  Clock,
  TrendingUp,
  Activity,
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
import { workflowApi } from "@/lib/api"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import { fmtDuration } from "@/lib/utils"
import type { WorkflowStats } from "@/types/workflow"

interface WorkflowStatsPanelProps {
  workflowId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

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

export function WorkflowStatsPanel({
  workflowId,
  open,
  onOpenChange,
}: WorkflowStatsPanelProps) {
  const t = useTranslations("workflows")
  const { formatRelativeTime } = useDateFormatter()

  const [stats, setStats] = useState<WorkflowStats | null>(null)
  const [isLoading, setIsLoading] = useState(false)

  // Fetch stats when sheet opens
  useEffect(() => {
    if (!open || !workflowId) return
    let cancelled = false
    setIsLoading(true)
    workflowApi
      .getStats(workflowId)
      .then((data) => {
        if (!cancelled) setStats(data)
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

  const hasRuns = stats !== null && stats.total_runs > 0

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="sm:max-w-sm p-0 flex flex-col">
        <SheetHeader className="px-6 pt-6 pb-3 border-b border-border/40 shrink-0">
          <SheetTitle className="text-sm flex items-center gap-2">
            <BarChart3 className="h-4 w-4" />
            {t("statsTitle")}
          </SheetTitle>
          <SheetDescription className="text-xs">
            {hasRuns
              ? t("statsTotalRuns") + ": " + stats.total_runs
              : t("statsNoRuns")}
          </SheetDescription>
        </SheetHeader>

        <ScrollArea className="flex-1 min-h-0">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : !hasRuns ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Activity className="h-8 w-8 mb-2 opacity-40" />
              <p className="text-sm">{t("statsNoRuns")}</p>
            </div>
          ) : (
            <div className="p-4 space-y-3">
              {/* Total Runs */}
              <StatCard
                icon={<Activity className="h-4 w-4 text-blue-500" />}
                label={t("statsTotalRuns")}
                value={String(stats.total_runs)}
              />

              {/* Completed */}
              <StatCard
                icon={<CheckCircle2 className="h-4 w-4 text-green-500" />}
                label={t("statsCompleted")}
                value={String(stats.completed)}
              />

              {/* Failed */}
              <StatCard
                icon={<XCircle className="h-4 w-4 text-red-500" />}
                label={t("statsFailed")}
                value={String(stats.failed)}
              />

              {/* Cancelled */}
              <StatCard
                icon={<Ban className="h-4 w-4 text-zinc-400" />}
                label={t("statsCancelled")}
                value={String(stats.cancelled)}
              />

              {/* Success Rate */}
              {stats.success_rate !== null && (
                <StatCard
                  icon={<TrendingUp className="h-4 w-4 text-emerald-500" />}
                  label={t("statsSuccessRate")}
                  value={`${(stats.success_rate * 100).toFixed(1)}%`}
                  bar={
                    <div className="mt-1.5 h-1.5 w-full rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-emerald-500 transition-all duration-500"
                        style={{ width: `${Math.min(stats.success_rate * 100, 100)}%` }}
                      />
                    </div>
                  }
                />
              )}

              {/* Avg Duration */}
              {stats.avg_duration_ms !== null && (
                <StatCard
                  icon={<Clock className="h-4 w-4 text-amber-500" />}
                  label={t("statsAvgDuration")}
                  value={formatDurationMs(stats.avg_duration_ms)}
                />
              )}

              {/* Last Run */}
              {stats.last_run_at && (
                <StatCard
                  icon={<Clock className="h-4 w-4 text-muted-foreground" />}
                  label={t("statsLastRun")}
                  value={formatRelativeTime(stats.last_run_at)}
                />
              )}
            </div>
          )}
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}

function StatCard({
  icon,
  label,
  value,
  bar,
}: {
  icon: React.ReactNode
  label: string
  value: string
  bar?: React.ReactNode
}) {
  return (
    <div className="rounded-md border border-border p-3">
      <div className="flex items-center gap-2">
        {icon}
        <span className="text-xs text-muted-foreground flex-1">{label}</span>
        <span className="text-sm font-medium text-foreground tabular-nums">
          {value}
        </span>
      </div>
      {bar}
    </div>
  )
}
