"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useTranslations } from "next-intl"
import Link from "next/link"
import { toast } from "sonner"
import {
  Loader2,
  Search,
  MoreHorizontal,
  Eye,
  Power,
  Trash2,
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { adminApi, type AdminWorkflowInfo } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import { useDateFormatter } from "@/hooks/use-date-formatter"

function successRateColor(rate: number | null): string {
  if (rate === null) return "text-muted-foreground"
  if (rate >= 90) return "text-green-600 dark:text-green-400"
  if (rate >= 70) return "text-yellow-600 dark:text-yellow-400"
  return "text-red-600 dark:text-red-400"
}

export function AdminWorkflows() {
  const t = useTranslations("admin.workflows")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const { formatRelativeTime } = useDateFormatter()

  // --- List state ---
  const [workflows, setWorkflows] = useState<AdminWorkflowInfo[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(1)
  const [search, setSearch] = useState("")
  const [statusFilter, setStatusFilter] = useState("__default__")
  const [isLoading, setIsLoading] = useState(true)

  // --- Debounce ref ---
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // --- Dialog states ---
  const [toggleTarget, setToggleTarget] = useState<AdminWorkflowInfo | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<AdminWorkflowInfo | null>(null)

  // --- Mutation loading ---
  const [isMutating, setIsMutating] = useState(false)

  // --- Load workflows ---
  const loadWorkflows = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await adminApi.listAllWorkflows({
        page,
        size: 20,
        search: search || undefined,
        status: statusFilter !== "__default__" ? statusFilter : undefined,
      })
      setWorkflows(data.items)
      setTotal(data.total)
      setPages(data.pages)
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [page, search, statusFilter, tError])

  useEffect(() => {
    loadWorkflows()
  }, [loadWorkflows])

  // --- Search with debounce ---
  const handleSearchChange = (value: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setSearch(value)
      setPage(1)
    }, 300)
  }

  // --- Status filter ---
  const handleStatusChange = (value: string) => {
    setStatusFilter(value)
    setPage(1)
  }

  // --- Toggle active ---
  const handleToggleActive = async () => {
    if (!toggleTarget) return
    setIsMutating(true)
    try {
      const result = await adminApi.toggleWorkflowActive(toggleTarget.id)
      toast.success(result.is_active ? t("workflowEnabled") : t("workflowDisabled"))
      setToggleTarget(null)
      await loadWorkflows()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Delete workflow ---
  const handleDelete = async () => {
    if (!deleteTarget) return
    setIsMutating(true)
    try {
      await adminApi.adminDeleteWorkflow(deleteTarget.id)
      toast.success(t("workflowDeleted"))
      setDeleteTarget(null)
      await loadWorkflows()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder={t("searchPlaceholder")}
            className="pl-9"
            onChange={(e) => handleSearchChange(e.target.value)}
          />
        </div>
        <Select value={statusFilter} onValueChange={handleStatusChange}>
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__default__">{t("statusAll")}</SelectItem>
            <SelectItem value="draft">{t("statusDraft")}</SelectItem>
            <SelectItem value="active">{t("statusActive")}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : workflows.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {t("noWorkflows")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {tc("name")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("owner")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {tc("status")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("nodeCount")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("totalRuns")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("successRate")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("lastRun")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {tc("createdAt")}
                </th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">
                  {tc("actions")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {workflows.map((wf) => (
                <tr key={wf.id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3 font-medium text-foreground">
                    <div className="flex items-center gap-2">
                      {wf.icon && !wf.name?.startsWith(wf.icon) && <span className="text-base">{wf.icon}</span>}
                      <span className="truncate max-w-[200px]">{wf.name}</span>
                      {!wf.is_active && (
                        <Badge variant="outline" className="border-red-500/40 text-red-600 dark:text-red-400 text-[10px] px-1.5 py-0">
                          {tc("disabled")}
                        </Badge>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="cursor-default">
                            {wf.username || wf.email || wf.user_id}
                          </span>
                        </TooltipTrigger>
                        {wf.email && (
                          <TooltipContent side="top" className="text-xs">
                            {wf.email}
                          </TooltipContent>
                        )}
                      </Tooltip>
                    </TooltipProvider>
                  </td>
                  <td className="px-4 py-3">
                    {wf.status === "active" ? (
                      <Badge variant="secondary" className="bg-green-500/10 text-green-600 border-green-500/20">
                        {t("statusActive")}
                      </Badge>
                    ) : (
                      <Badge variant="secondary">
                        {t("statusDraft")}
                      </Badge>
                    )}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {wf.node_count}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {wf.total_runs}
                  </td>
                  <td className="px-4 py-3">
                    <span className={successRateColor(wf.success_rate)}>
                      {wf.success_rate !== null ? `${wf.success_rate}%` : "--"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {wf.last_run_at
                      ? formatRelativeTime(wf.last_run_at)
                      : t("noRuns")}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {formatRelativeTime(wf.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem asChild>
                          <Link href={`/workflows/${wf.id}`}>
                            <Eye className="mr-2 h-4 w-4" />
                            {tc("view")}
                          </Link>
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => setToggleTarget(wf)}>
                          <Power className="mr-2 h-4 w-4" />
                          {wf.is_active ? tc("disable") : tc("enable")}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          variant="destructive"
                          onClick={() => setDeleteTarget(wf)}
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

      {/* Pagination */}
      {!isLoading && workflows.length > 0 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>{t("totalWorkflows", { count: total })}</span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              {t("previous")}
            </Button>
            <span>
              {t("pageOf", { page, pages })}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= pages}
              onClick={() => setPage((p) => Math.min(pages, p + 1))}
            >
              {tc("next")}
            </Button>
          </div>
        </div>
      )}

      {/* --- Toggle Active AlertDialog --- */}
      <AlertDialog
        open={toggleTarget !== null}
        onOpenChange={(open) => { if (!open) setToggleTarget(null) }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("toggleConfirm")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("toggleConfirmDesc", { name: toggleTarget?.name || "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleToggleActive} disabled={isMutating}>
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {toggleTarget?.is_active ? tc("disable") : tc("enable")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* --- Delete Workflow AlertDialog --- */}
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteConfirm")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteConfirmDesc", {
                name: deleteTarget?.name || "",
                owner: deleteTarget?.username || deleteTarget?.email || "",
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive hover:bg-destructive/90"
              onClick={handleDelete}
              disabled={isMutating}
            >
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
