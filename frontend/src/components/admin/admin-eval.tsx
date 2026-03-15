"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations, useLocale } from "next-intl"
import { cn } from "@/lib/utils"
import { toast } from "sonner"
import {
  Loader2,
  MoreHorizontal,
  Eye,
  Trash2,
  Database,
  Play,
  BarChart3,
  Coins,
  Eraser,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Label } from "@/components/ui/label"
import { adminApi, type AdminEvalDataset, type AdminEvalRun, type AdminEvalStats } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import { formatTokens } from "@/lib/utils"

const PAGE_SIZE = 20

type SubView = "datasets" | "runs"

export function AdminEval() {
  const t = useTranslations("admin.evaluations")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const locale = useLocale()

  const [view, setView] = useState<SubView>("datasets")
  const [stats, setStats] = useState<AdminEvalStats | null>(null)

  // --- Datasets ---
  const [datasets, setDatasets] = useState<AdminEvalDataset[]>([])
  const [dsTotal, setDsTotal] = useState(0)
  const [dsPage, setDsPage] = useState(1)
  const [dsPages, setDsPages] = useState(1)
  const [dsLoading, setDsLoading] = useState(true)

  // --- Runs ---
  const [runs, setRuns] = useState<AdminEvalRun[]>([])
  const [runTotal, setRunTotal] = useState(0)
  const [runPage, setRunPage] = useState(1)
  const [runPages, setRunPages] = useState(1)
  const [runLoading, setRunLoading] = useState(true)

  // --- Dialogs ---
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; type: "dataset" | "run" } | null>(null)
  const [cleanupOpen, setCleanupOpen] = useState(false)
  const [cleanupDays, setCleanupDays] = useState("")
  const [isMutating, setIsMutating] = useState(false)

  // --- Load stats ---
  const loadStats = useCallback(async () => {
    try {
      const data = await adminApi.getEvalStats()
      setStats(data)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }, [tError])

  // --- Load datasets ---
  const loadDatasets = useCallback(async () => {
    setDsLoading(true)
    try {
      const data = await adminApi.listEvalDatasets({ page: dsPage, size: PAGE_SIZE })
      setDatasets(data.items)
      setDsTotal(data.total)
      setDsPages(Math.max(1, Math.ceil(data.total / PAGE_SIZE)))
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setDsLoading(false)
    }
  }, [dsPage, tError])

  // --- Load runs ---
  const loadRuns = useCallback(async () => {
    setRunLoading(true)
    try {
      const data = await adminApi.listEvalRuns({ page: runPage, size: PAGE_SIZE })
      setRuns(data.items)
      setRunTotal(data.total)
      setRunPages(Math.max(1, Math.ceil(data.total / PAGE_SIZE)))
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setRunLoading(false)
    }
  }, [runPage, tError])

  useEffect(() => { loadStats() }, [loadStats])
  useEffect(() => { if (view === "datasets") loadDatasets() }, [view, loadDatasets])
  useEffect(() => { if (view === "runs") loadRuns() }, [view, loadRuns])

  // --- Delete ---
  const handleDelete = async () => {
    if (!deleteTarget) return
    setIsMutating(true)
    try {
      if (deleteTarget.type === "dataset") {
        await adminApi.deleteEvalDataset(deleteTarget.id)
        toast.success(t("datasetDeleted"))
        loadDatasets()
      } else {
        await adminApi.deleteEvalRun(deleteTarget.id)
        toast.success(t("runDeleted"))
        loadRuns()
      }
      setDeleteTarget(null)
      loadStats()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Cleanup ---
  const handleCleanup = async () => {
    const days = parseInt(cleanupDays, 10)
    if (!days || days <= 0) return
    setIsMutating(true)
    try {
      await adminApi.cleanupEvalRuns(days)
      toast.success(t("cleanupSuccess"))
      setCleanupOpen(false)
      setCleanupDays("")
      loadRuns()
      loadStats()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        <Button variant="outline" size="sm" className="gap-1.5" onClick={() => setCleanupOpen(true)}>
          <Eraser className="h-4 w-4" />
          {t("cleanupTitle")}
        </Button>
      </div>

      {/* Stats cards */}
      {stats && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Database className="h-4 w-4" />
              <p className="text-xs font-medium">{t("totalDatasets")}</p>
            </div>
            <p className="text-2xl font-semibold tabular-nums">{stats.total_datasets}</p>
          </div>
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Play className="h-4 w-4" />
              <p className="text-xs font-medium">{t("totalRuns")}</p>
            </div>
            <p className="text-2xl font-semibold tabular-nums">{stats.total_runs}</p>
          </div>
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <BarChart3 className="h-4 w-4" />
              <p className="text-xs font-medium">{t("avgPassRate")}</p>
            </div>
            <p className="text-2xl font-semibold tabular-nums">
              {stats.avg_pass_rate != null ? `${stats.avg_pass_rate.toFixed(1)}%` : "--"}
            </p>
          </div>
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Coins className="h-4 w-4" />
              <p className="text-xs font-medium">{t("totalTokensUsed")}</p>
            </div>
            <p className="text-2xl font-semibold tabular-nums">{formatTokens(stats.total_tokens ?? 0)}</p>
          </div>
        </div>
      )}

      {/* Sub-tab toggle */}
      <div className="inline-flex items-center rounded-md border border-border bg-muted/40 p-0.5 gap-0.5">
        <button
          onClick={() => setView("datasets")}
          className={cn(
            "inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-sm transition-colors font-medium",
            view === "datasets" ? "bg-background shadow-xs text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Database className="h-3.5 w-3.5" />
          {t("datasetsTab")}
        </button>
        <button
          onClick={() => setView("runs")}
          className={cn(
            "inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-sm transition-colors font-medium",
            view === "runs" ? "bg-background shadow-xs text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Play className="h-3.5 w-3.5" />
          {t("runsTab")}
        </button>
      </div>

      {/* ===================== DATASETS ===================== */}
      {view === "datasets" && (
        <>
          {dsLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : datasets.length === 0 ? (
            <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
              {t("noDatasets")}
            </div>
          ) : (
            <div className="rounded-md border border-border overflow-x-auto">
              <table className="w-full min-w-max text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/40">
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colName")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colOwner")}</th>
                    <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("colCases")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colLastRun")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colCreated")}</th>
                    <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{tc("actions")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {datasets.map((ds) => (
                    <tr key={ds.id} className="hover:bg-muted/20 transition-colors">
                      <td className="px-4 py-3 font-medium text-foreground">{ds.name}</td>
                      <td className="px-4 py-3 text-muted-foreground">{ds.username || ds.email || "--"}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{ds.case_count}</td>
                      <td className="px-4 py-3 text-muted-foreground text-xs">
                        {ds.last_run_at ? new Date(ds.last_run_at).toLocaleDateString(locale) : t("neverRun")}
                      </td>
                      <td className="px-4 py-3 text-muted-foreground text-xs">
                        {new Date(ds.created_at).toLocaleDateString(locale)}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                              <MoreHorizontal className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem
                              variant="destructive"
                              onClick={() => setDeleteTarget({ id: ds.id, type: "dataset" })}
                            >
                              <Trash2 className="mr-2 h-4 w-4" />
                              {tc("delete")}
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

          {!dsLoading && datasets.length > 0 && (
            <div className="flex items-center justify-between text-sm text-muted-foreground">
              <span>{t("totalItems", { count: dsTotal })}</span>
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" disabled={dsPage <= 1} onClick={() => setDsPage((p) => Math.max(1, p - 1))}>
                  {t("previous")}
                </Button>
                <span>{t("pageOf", { page: dsPage, pages: dsPages })}</span>
                <Button variant="outline" size="sm" disabled={dsPage >= dsPages} onClick={() => setDsPage((p) => Math.min(dsPages, p + 1))}>
                  {tc("next")}
                </Button>
              </div>
            </div>
          )}
        </>
      )}

      {/* ===================== RUNS ===================== */}
      {view === "runs" && (
        <>
          {runLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : runs.length === 0 ? (
            <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
              {t("noRuns")}
            </div>
          ) : (
            <div className="rounded-md border border-border overflow-x-auto">
              <table className="w-full min-w-max text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/40">
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colDataset")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colOwner")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colStatus")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colPassRate")}</th>
                    <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("colTokens")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colCreated")}</th>
                    <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{tc("actions")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {runs.map((run) => (
                    <tr key={run.id} className="hover:bg-muted/20 transition-colors">
                      <td className="px-4 py-3 font-medium text-foreground">{run.dataset_name}</td>
                      <td className="px-4 py-3 text-muted-foreground">{run.username || run.email || "--"}</td>
                      <td className="px-4 py-3">
                        {run.status === "pass" ? (
                          <Badge variant="outline" className="border-green-500/40 text-green-600 dark:text-green-400">{t("statusPass")}</Badge>
                        ) : run.status === "fail" ? (
                          <Badge variant="outline" className="border-red-500/40 text-red-600 dark:text-red-400">{t("statusFail")}</Badge>
                        ) : (
                          <Badge variant="secondary">{t("statusRunning")}</Badge>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {run.pass_rate !== null ? (
                          <div className="flex items-center gap-2">
                            <div className="h-2 w-16 rounded-full bg-muted overflow-hidden">
                              <div
                                className="h-full rounded-full bg-green-500"
                                style={{ width: `${run.pass_rate}%` }}
                              />
                            </div>
                            <span className="text-xs tabular-nums">{(run.pass_rate ?? 0).toFixed(1)}%</span>
                          </div>
                        ) : (
                          <span className="text-muted-foreground/50">--</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        {formatTokens(run.tokens_used ?? 0)}
                      </td>
                      <td className="px-4 py-3 text-muted-foreground text-xs">
                        {new Date(run.created_at).toLocaleDateString(locale)}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                              <MoreHorizontal className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem
                              variant="destructive"
                              onClick={() => setDeleteTarget({ id: run.id, type: "run" })}
                            >
                              <Trash2 className="mr-2 h-4 w-4" />
                              {tc("delete")}
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

          {!runLoading && runs.length > 0 && (
            <div className="flex items-center justify-between text-sm text-muted-foreground">
              <span>{t("totalItems", { count: runTotal })}</span>
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" disabled={runPage <= 1} onClick={() => setRunPage((p) => Math.max(1, p - 1))}>
                  {t("previous")}
                </Button>
                <span>{t("pageOf", { page: runPage, pages: runPages })}</span>
                <Button variant="outline" size="sm" disabled={runPage >= runPages} onClick={() => setRunPage((p) => Math.min(runPages, p + 1))}>
                  {tc("next")}
                </Button>
              </div>
            </div>
          )}
        </>
      )}

      {/* --- Delete AlertDialog --- */}
      <AlertDialog open={deleteTarget !== null} onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteConfirm")}</AlertDialogTitle>
            <AlertDialogDescription>{t("deleteConfirmDesc")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction className="bg-destructive hover:bg-destructive/90" onClick={handleDelete} disabled={isMutating}>
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* --- Cleanup Dialog --- */}
      <Dialog open={cleanupOpen} onOpenChange={setCleanupOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("cleanupTitle")}</DialogTitle>
            <DialogDescription>{t("cleanupDesc")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label>{t("cleanupDaysLabel")} <span className="text-destructive">*</span></Label>
              <Input
                type="number"
                min={1}
                value={cleanupDays}
                onChange={(e) => setCleanupDays(e.target.value)}
                placeholder={t("cleanupDaysPlaceholder")}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCleanupOpen(false)}>{tc("cancel")}</Button>
            <Button onClick={handleCleanup} disabled={isMutating || !cleanupDays.trim() || parseInt(cleanupDays, 10) <= 0}>
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("cleanupBtn")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
