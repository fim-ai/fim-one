"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations, useLocale } from "next-intl"
import Link from "next/link"
import { toast } from "sonner"
import {
  Loader2,
  MoreHorizontal,
  Pause,
  Play,
  Eye,
  Calendar,
  Clock,
  AlertTriangle,
  Zap,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { adminApi, type AdminSchedule, type AdminScheduleStats } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"

const PAGE_SIZE = 20

export function AdminSchedules() {
  const t = useTranslations("admin.schedules")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const locale = useLocale()

  // --- State ---
  const [schedules, setSchedules] = useState<AdminSchedule[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(1)
  const [isLoading, setIsLoading] = useState(true)
  const [stats, setStats] = useState<AdminScheduleStats | null>(null)

  // --- Load ---
  const loadSchedules = useCallback(async () => {
    setIsLoading(true)
    try {
      const data = await adminApi.listSchedules({ page, size: PAGE_SIZE })
      setSchedules(data.items)
      setTotal(data.total)
      setPages(Math.max(1, Math.ceil(data.total / PAGE_SIZE)))
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [page, tError])

  const loadStats = useCallback(async () => {
    try {
      const data = await adminApi.getScheduleStats()
      setStats(data)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }, [tError])

  useEffect(() => { loadSchedules() }, [loadSchedules])
  useEffect(() => { loadStats() }, [loadStats])

  // --- Toggle ---
  const handleToggle = async (schedule: AdminSchedule) => {
    try {
      const result = await adminApi.toggleScheduleActive(schedule.workflow_id)
      toast.success(result.is_active ? t("scheduleResumed") : t("schedulePaused"))
      loadSchedules()
      loadStats()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  const formatDateTime = (dateStr: string | null): string => {
    if (!dateStr) return "--"
    return new Date(dateStr).toLocaleString(locale, {
      month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    })
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {/* Stats cards */}
      {stats && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Zap className="h-4 w-4" />
              <p className="text-xs font-medium">{t("activeSchedules")}</p>
            </div>
            <p className="text-2xl font-semibold tabular-nums text-green-600 dark:text-green-400">{stats.active ?? 0}</p>
          </div>
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Calendar className="h-4 w-4" />
              <p className="text-xs font-medium">{t("totalSchedules")}</p>
            </div>
            <p className="text-2xl font-semibold tabular-nums">{stats.total ?? 0}</p>
          </div>
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Clock className="h-4 w-4" />
              <p className="text-xs font-medium">{t("nextRun")}</p>
            </div>
            <p className="text-sm font-semibold tabular-nums">
              {stats.next_run_at ? formatDateTime(stats.next_run_at) : t("noNextRun")}
            </p>
          </div>
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <AlertTriangle className="h-4 w-4" />
              <p className="text-xs font-medium">{t("failedIn24h")}</p>
            </div>
            <p className="text-2xl font-semibold tabular-nums text-red-600 dark:text-red-400">{stats.failed_24h ?? 0}</p>
          </div>
        </div>
      )}

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : schedules.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {t("noSchedules")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colWorkflow")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colOwner")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colCron")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colTimezone")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colStatus")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colNextRun")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colLastRun")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{tc("actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {schedules.map((sched) => (
                <tr key={sched.id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3 font-medium text-foreground">{sched.workflow_name}</td>
                  <td className="px-4 py-3 text-muted-foreground">{sched.username || sched.email || "--"}</td>
                  <td className="px-4 py-3">
                    <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
                      {sched.cron_expression}
                    </code>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">{sched.timezone}</td>
                  <td className="px-4 py-3">
                    {sched.is_active ? (
                      <Badge variant="outline" className="border-green-500/40 text-green-600 dark:text-green-400">
                        {t("statusActive")}
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="border-yellow-500/40 text-yellow-600 dark:text-yellow-400">
                        {t("statusPaused")}
                      </Badge>
                    )}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {sched.next_run_at ? formatDateTime(sched.next_run_at) : t("noNextRun")}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {sched.last_run_at ? formatDateTime(sched.last_run_at) : t("neverRun")}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => handleToggle(sched)}>
                          {sched.is_active ? (
                            <>
                              <Pause className="mr-2 h-4 w-4" />
                              {t("pause")}
                            </>
                          ) : (
                            <>
                              <Play className="mr-2 h-4 w-4" />
                              {t("resume")}
                            </>
                          )}
                        </DropdownMenuItem>
                        <DropdownMenuItem asChild>
                          <Link href={`/workflows/${sched.workflow_id}`}>
                            <Eye className="mr-2 h-4 w-4" />
                            {t("viewWorkflow")}
                          </Link>
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {!isLoading && schedules.length > 0 && (
        <div className="flex items-center justify-end text-sm text-muted-foreground">
          <span className="mr-auto">{t("totalItems", { count: total })}</span>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
              {tc("back")}
            </Button>
            <span>{page} / {pages}</span>
            <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => setPage((p) => Math.min(pages, p + 1))}>
              {tc("next")}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
