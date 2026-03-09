"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations, useLocale } from "next-intl"
import { format } from "date-fns"
import { Loader2, RefreshCw, Download, CalendarIcon, MoreHorizontal, FileText } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { Calendar } from "@/components/ui/calendar"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"
import { apiFetch } from "@/lib/api"
import { getApiBaseUrl, ACCESS_TOKEN_KEY } from "@/lib/constants"
import { getErrorMessage } from "@/lib/error-utils"
import { toast } from "sonner"

interface AuditEntry {
  id: string
  admin_id: string
  admin_username: string
  action: string
  target_type: string | null
  target_id: string | null
  target_label: string | null
  detail: string | null
  created_at: string
}

interface AuditPage {
  items: AuditEntry[]
  total: number
  page: number
  size: number
  pages: number
}

const KNOWN_ACTIONS = [
  "user.create",
  "user.delete",
  "user.disable",
  "user.enable",
  "user.grant_admin",
  "user.revoke_admin",
  "user.reset_password",
  "user.force_logout",
  "user.set_quota",
  "auth.force_logout_all",
  "settings.update",
  "conversation.delete",
  "conversation.viewed",
  "invite_code.create",
  "invite_code.revoke",
  "storage.clear_user",
  "storage.cleanup_orphaned",
  "mcp_server.create_global",
  "mcp_server.delete_global",
  "model.create",
  "model.update",
  "model.delete",
  "model.enable",
  "model.disable",
  "model.set_role",
  "account.self_delete",
]

const ACTION_COLORS: Record<string, string> = {
  "user.create": "bg-green-500/15 text-green-700 dark:text-green-400 border-green-500/30",
  "user.delete": "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30",
  "user.disable": "bg-orange-500/15 text-orange-700 dark:text-orange-400 border-orange-500/30",
  "user.enable": "bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30",
  "user.grant_admin": "bg-purple-500/15 text-purple-700 dark:text-purple-400 border-purple-500/30",
  "user.revoke_admin": "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400 border-yellow-500/30",
  "user.reset_password": "bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30",
  "auth.force_logout_all": "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30",
  "settings.update": "bg-sky-500/15 text-sky-700 dark:text-sky-400 border-sky-500/30",
  "account.self_delete": "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30",
}

function actionColor(action: string): string {
  return ACTION_COLORS[action] ?? "bg-muted text-muted-foreground border-border"
}

function formatTime(iso: string, locale: string): string {
  try {
    return new Date(iso).toLocaleString(locale, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    })
  } catch {
    return iso
  }
}

function formatDateParam(date: Date | undefined): string {
  return date ? format(date, "yyyy-MM-dd") : ""
}

/** Build query string from filter + pagination state. */
function buildParams(
  page: number,
  actionFilter: string,
  dateFrom: Date | undefined,
  dateTo: Date | undefined,
): URLSearchParams {
  const params = new URLSearchParams()
  params.set("page", String(page))
  params.set("size", "50")
  if (actionFilter && actionFilter !== "__all__") params.set("action", actionFilter)
  const from = formatDateParam(dateFrom)
  const to = formatDateParam(dateTo)
  if (from) params.set("date_from", from)
  if (to) params.set("date_to", to)
  return params
}

function useActionLabel() {
  const t = useTranslations("admin.audit.actionLabels")
  return (action: string) => {
    // Convert "user.create" → "user__create" because next-intl uses dots as nesting separators
    const key = action.replace(/\./g, "__") as Parameters<typeof t>[0]
    try {
      return t(key)
    } catch {
      return action
    }
  }
}

export function AdminAudit() {
  const t = useTranslations("admin.audit")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const locale = useLocale()
  const getActionLabel = useActionLabel()
  const [data, setData] = useState<AuditPage | null>(null)
  const [page, setPage] = useState(1)
  const [isLoading, setIsLoading] = useState(true)
  const [isExporting, setIsExporting] = useState(false)
  const [selected, setSelected] = useState<AuditEntry | null>(null)

  // Filters
  const [actionFilter, setActionFilter] = useState("__all__")
  const [dateFrom, setDateFrom] = useState<Date | undefined>()
  const [dateTo, setDateTo] = useState<Date | undefined>()
  const [dateFromOpen, setDateFromOpen] = useState(false)
  const [dateToOpen, setDateToOpen] = useState(false)

  const hasActiveFilters = (actionFilter && actionFilter !== "__all__") || dateFrom || dateTo

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const params = buildParams(page, actionFilter, dateFrom, dateTo)
      const res = await apiFetch<AuditPage>(`/api/admin/audit-log?${params}`)
      setData(res)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [page, actionFilter, dateFrom, dateTo, tError])

  useEffect(() => { load() }, [load])

  // Reset to page 1 when filters change
  const handleActionChange = (value: string) => {
    setActionFilter(value)
    setPage(1)
  }
  const handleDateFromChange = (date: Date | undefined) => {
    setDateFrom(date)
    setDateFromOpen(false)
    setPage(1)
  }
  const handleDateToChange = (date: Date | undefined) => {
    setDateTo(date)
    setDateToOpen(false)
    setPage(1)
  }

  const handleExport = async () => {
    setIsExporting(true)
    try {
      const params = new URLSearchParams()
      if (actionFilter && actionFilter !== "__all__") params.set("action", actionFilter)
      const from = formatDateParam(dateFrom)
      const to = formatDateParam(dateTo)
      if (from) params.set("date_from", from)
      if (to) params.set("date_to", to)

      const token = typeof window !== "undefined" ? localStorage.getItem(ACCESS_TOKEN_KEY) : null
      const headers: Record<string, string> = {}
      if (token) headers["Authorization"] = `Bearer ${token}`

      const res = await fetch(`${getApiBaseUrl()}/api/admin/audit-log/export?${params}`, { headers })
      if (!res.ok) {
        throw new Error(res.statusText)
      }
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `audit-log-${new Date().toISOString().slice(0, 10)}.csv`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      toast.error(t("exportError"))
      console.error(err)
    } finally {
      setIsExporting(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-sm text-muted-foreground">
            {t("subtitle")}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={isLoading}>
          <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${isLoading ? "animate-spin" : ""}`} />
          {tc("refresh")}
        </Button>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">{t("filterAction")}</label>
          <Select value={actionFilter} onValueChange={handleActionChange}>
            <SelectTrigger className="w-[200px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">{t("filterActionAll")}</SelectItem>
              {KNOWN_ACTIONS.map((a) => (
                <SelectItem key={a} value={a}>
                  {getActionLabel(a)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">{t("filterDateFrom")}</label>
          <Popover open={dateFromOpen} onOpenChange={setDateFromOpen}>
            <PopoverTrigger asChild>
              <button
                className={cn(
                  "flex items-center gap-2 rounded-md border border-input bg-transparent px-3 h-9 text-sm shadow-xs transition-[color,box-shadow,border-color] outline-none hover:border-ring/30 dark:bg-input/30 dark:hover:bg-input/50",
                  !dateFrom && "text-muted-foreground",
                )}
              >
                <CalendarIcon className="size-4 text-muted-foreground" />
                {dateFrom ? format(dateFrom, "yyyy-MM-dd") : t("filterDateFrom")}
              </button>
            </PopoverTrigger>
            <PopoverContent className="w-auto overflow-hidden p-0" align="start">
              <Calendar
                mode="single"
                captionLayout="dropdown"
                selected={dateFrom}
                onSelect={handleDateFromChange}
                defaultMonth={dateFrom}
                disabled={(date) => (dateTo ? date > dateTo : false)}
                startMonth={new Date(2024, 0)}
                endMonth={new Date(new Date().getFullYear() + 1, 11)}
              />
            </PopoverContent>
          </Popover>
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">{t("filterDateTo")}</label>
          <Popover open={dateToOpen} onOpenChange={setDateToOpen}>
            <PopoverTrigger asChild>
              <button
                className={cn(
                  "flex items-center gap-2 rounded-md border border-input bg-transparent px-3 h-9 text-sm shadow-xs transition-[color,box-shadow,border-color] outline-none hover:border-ring/30 dark:bg-input/30 dark:hover:bg-input/50",
                  !dateTo && "text-muted-foreground",
                )}
              >
                <CalendarIcon className="size-4 text-muted-foreground" />
                {dateTo ? format(dateTo, "yyyy-MM-dd") : t("filterDateTo")}
              </button>
            </PopoverTrigger>
            <PopoverContent className="w-auto overflow-hidden p-0" align="start">
              <Calendar
                mode="single"
                captionLayout="dropdown"
                selected={dateTo}
                onSelect={handleDateToChange}
                defaultMonth={dateTo}
                disabled={(date) => (dateFrom ? date < dateFrom : false)}
                startMonth={new Date(2024, 0)}
                endMonth={new Date(new Date().getFullYear() + 1, 11)}
              />
            </PopoverContent>
          </Popover>
        </div>

        <Button variant="outline" size="sm" onClick={handleExport} disabled={isExporting} className="h-9">
          {isExporting
            ? <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
            : <Download className="h-3.5 w-3.5 mr-1.5" />}
          {t("exportCsv")}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : !data || data.items.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-6 text-sm text-muted-foreground text-center">
          {hasActiveFilters ? t("noResults") : t("noEntries")}
        </div>
      ) : (
        <>
          <div className="rounded-md border border-border overflow-x-auto">
            <table className="w-full min-w-max text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/40">
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground whitespace-nowrap">{t("timeColumn")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("adminColumn")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("actionColumn")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("targetColumn")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("detailColumn")}</th>
                  <th className="px-4 py-2.5 w-12 text-right font-medium text-muted-foreground">{tc("actions")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.items.map((entry) => (
                  <tr key={entry.id} className="hover:bg-muted/20 transition-colors">
                    <td className="px-4 py-2.5 text-xs text-muted-foreground whitespace-nowrap tabular-nums">
                      {formatTime(entry.created_at, locale)}
                    </td>
                    <td className="px-4 py-2.5 font-medium text-foreground">
                      {entry.admin_username}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${actionColor(entry.action)}`}>
                        {getActionLabel(entry.action)}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-muted-foreground text-xs">
                      {entry.target_label
                        ? <span className="font-medium text-foreground">{entry.target_label}</span>
                        : <span className="text-muted-foreground/50">&mdash;</span>}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted-foreground max-w-[260px] truncate">
                      {entry.detail ?? "\u2014"}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => setSelected(entry)}>
                            <FileText className="mr-2 h-4 w-4" />
                            {tc("details")}
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>{t("totalEntries", { count: data.total })}</span>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                {t("previous")}
              </Button>
              <span>{t("pageOf", { page, pages: data.pages })}</span>
              <Button variant="outline" size="sm" disabled={page >= data.pages} onClick={() => setPage((p) => p + 1)}>
                {tc("next")}
              </Button>
            </div>
          </div>
        </>
      )}

      {/* Detail dialog */}
      <Dialog open={!!selected} onOpenChange={(open) => { if (!open) setSelected(null) }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("detailDialogTitle")}</DialogTitle>
            <DialogDescription>
              {selected && formatTime(selected.created_at, locale)}
            </DialogDescription>
          </DialogHeader>
          {selected && (
            <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-3 text-sm">
              <span className="text-muted-foreground">{t("adminColumn")}</span>
              <span className="font-medium">{selected.admin_username}</span>

              <span className="text-muted-foreground">{t("actionColumn")}</span>
              <span>
                <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${actionColor(selected.action)}`}>
                  {getActionLabel(selected.action)}
                </span>
              </span>

              <span className="text-muted-foreground">{t("targetColumn")}</span>
              <span className="font-medium">
                {selected.target_label || "\u2014"}
              </span>

              {selected.target_id && (
                <>
                  <span className="text-muted-foreground">ID</span>
                  <span className="font-mono text-xs text-muted-foreground break-all">{selected.target_id}</span>
                </>
              )}

              <span className="text-muted-foreground">{t("detailColumn")}</span>
              <span className="whitespace-pre-wrap break-words">
                {selected.detail || "\u2014"}
              </span>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
