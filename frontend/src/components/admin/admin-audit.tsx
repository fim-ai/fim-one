"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import { format } from "date-fns"
import { Loader2, RefreshCw, Download, CalendarIcon, MoreHorizontal, FileText } from "lucide-react"
import { useDateFormatter } from "@/hooks/use-date-formatter"
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
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Input } from "@/components/ui/input"
import { CallRecordsPanel } from "@/components/audit/call-records-panel"
import { useAuditActionLabel } from "@/hooks/use-audit-action-label"
import { ALL, AUDIT_OUTCOMES, buildAuditQuery, formatAuditChange } from "@/lib/audit-records"
import { cn } from "@/lib/utils"
import { apiFetch, adminApi } from "@/lib/api"
import { getApiBaseUrl, ACCESS_TOKEN_KEY } from "@/lib/constants"
import { getErrorMessage } from "@/lib/error-utils"
import { toast } from "sonner"
import type { ReviewLogItem, AdminOrganization } from "@/types/admin"

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
  // Empty on entries written before these were recorded.
  actor_kind?: string | null
  org_id?: string | null
  outcome?: string
  changes?: Record<string, { from?: unknown; to?: unknown; changed?: boolean }> | null
  ip_address?: string | null
  user_agent?: string | null
}

/** What the list and the export are filtered by. */
interface AuditFilters {
  action: string
  orgId: string
  outcome: string
  ipAddress: string
  dateFrom: Date | undefined
  dateTo: Date | undefined
}

interface AuditPage {
  items: AuditEntry[]
  total: number
  page: number
  size: number
  pages: number
}

/** The registered audit actions, grouped by domain (GET /api/admin/audit-log/actions). */
interface ActionGroup {
  domain: string
  actions: string[]
}

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

function formatDateParam(date: Date | undefined): string {
  return date ? format(date, "yyyy-MM-dd") : ""
}

/** The query string of the list (with a page) or of the export (without). */
function buildParams(filters: AuditFilters, page?: number): URLSearchParams {
  return buildAuditQuery({
    page,
    size: page === undefined ? undefined : 50,
    action: filters.action,
    orgId: filters.orgId,
    outcome: filters.outcome,
    ipAddress: filters.ipAddress,
    dateFrom: formatDateParam(filters.dateFrom),
    dateTo: formatDateParam(filters.dateTo),
  })
}

function outcomeBadgeClass(outcome: string): string {
  return outcome === "success"
    ? "border-green-500/30 bg-green-500/15 text-green-700 dark:text-green-400"
    : "border-red-500/30 bg-red-500/15 text-red-700 dark:text-red-400"
}

const REVIEW_ACTIONS = ["submitted", "approved", "rejected", "unpublished", "resubmitted"] as const
const REVIEW_RESOURCE_TYPES = ["agent", "connector", "kb", "mcp_server"] as const

type ReviewAction = (typeof REVIEW_ACTIONS)[number]

const REVIEW_ACTION_BADGE: Record<ReviewAction, string> = {
  approved: "bg-green-500/15 text-green-700 dark:text-green-400 border-green-500/30",
  rejected: "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30",
  submitted: "bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30",
  resubmitted: "bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30",
  unpublished: "bg-muted text-muted-foreground border-border",
}

function reviewActionBadgeClass(action: string): string {
  return REVIEW_ACTION_BADGE[action as ReviewAction] ?? "bg-muted text-muted-foreground border-border"
}

const REVIEW_PAGE_SIZE = 50

interface ReviewLogPage {
  items: ReviewLogItem[]
  total: number
  limit: number
  offset: number
}

function ReviewLogPanel() {
  const t = useTranslations("admin.audit")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const { formatDateTimeFull } = useDateFormatter()

  const [data, setData] = useState<ReviewLogPage | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [offset, setOffset] = useState(0)
  const [resourceTypeFilter, setResourceTypeFilter] = useState("__all__")
  const [actionFilter, setActionFilter] = useState("__all__")
  const [orgFilter, setOrgFilter] = useState("__all__")
  const [orgs, setOrgs] = useState<Array<{ id: string; name: string }>>([])

  useEffect(() => {
    adminApi.listOrganizations(1, 200).then((res) => {
      setOrgs(res.items.map((o: AdminOrganization) => ({ id: o.id, name: o.name })))
    }).catch(() => {
      // silently fail — org filter will just be empty
    })
  }, [])

  const page = Math.floor(offset / REVIEW_PAGE_SIZE) + 1
  const pages = data ? Math.max(1, Math.ceil(data.total / REVIEW_PAGE_SIZE)) : 1
  const hasFilters = (resourceTypeFilter && resourceTypeFilter !== "__all__") || (actionFilter && actionFilter !== "__all__") || (orgFilter && orgFilter !== "__all__")

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const params: Record<string, string | number> = { limit: REVIEW_PAGE_SIZE, offset }
      if (resourceTypeFilter && resourceTypeFilter !== "__all__") params.resource_type = resourceTypeFilter
      if (actionFilter && actionFilter !== "__all__") params.action = actionFilter
      if (orgFilter && orgFilter !== "__all__") params.org_id = orgFilter
      const res = await adminApi.listReviewLog(params as Parameters<typeof adminApi.listReviewLog>[0])
      setData(res)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [offset, resourceTypeFilter, actionFilter, orgFilter, tError])

  useEffect(() => { load() }, [load])

  const handleResourceTypeChange = (val: string) => {
    setResourceTypeFilter(val)
    setOffset(0)
  }

  const handleActionChange = (val: string) => {
    setActionFilter(val)
    setOffset(0)
  }

  const handleOrgFilterChange = (val: string) => {
    setOrgFilter(val)
    setOffset(0)
  }

  return (
    <div className="space-y-4">
      {/* Filter bar */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">{t("filterByOrg")}</label>
          <Select value={orgFilter} onValueChange={handleOrgFilterChange}>
            <SelectTrigger className="w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">{t("allOrgs")}</SelectItem>
              {orgs.map((org) => (
                <SelectItem key={org.id} value={org.id}>
                  {org.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">{t("filterByType")}</label>
          <Select value={resourceTypeFilter} onValueChange={handleResourceTypeChange}>
            <SelectTrigger className="w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">{t("allTypes")}</SelectItem>
              {REVIEW_RESOURCE_TYPES.map((rt) => (
                <SelectItem key={rt} value={rt}>
                  {t(`resourceTypes.${rt}` as Parameters<typeof t>[0])}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">{t("filterByAction")}</label>
          <Select value={actionFilter} onValueChange={handleActionChange}>
            <SelectTrigger className="w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">{t("allActions")}</SelectItem>
              {REVIEW_ACTIONS.map((a) => (
                <SelectItem key={a} value={a}>
                  {t(`reviewActionLabels.${a}` as Parameters<typeof t>[0])}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <Button variant="outline" size="sm" onClick={load} disabled={isLoading} className="h-9 self-end">
          <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${isLoading ? "animate-spin" : ""}`} />
          {tc("refresh")}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : !data || data.items.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-6 text-sm text-muted-foreground text-center">
          {hasFilters ? t("noReviewResults") : t("noReviewEntries")}
        </div>
      ) : (
        <>
          <div className="rounded-md border border-border overflow-x-auto">
            <table className="w-full min-w-max text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/40">
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground whitespace-nowrap">{t("timeColumn")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colOrg")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colResourceType")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colResource")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colAction")}</th>
                  <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("colActor")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.items.map((entry) => (
                  <tr key={entry.id} className="hover:bg-muted/20 transition-colors">
                    <td className="px-4 py-2.5 text-xs text-muted-foreground whitespace-nowrap tabular-nums">
                      {formatDateTimeFull(entry.created_at)}
                    </td>
                    <td className="px-4 py-2.5 font-medium text-foreground">
                      {entry.org_name ?? <span className="text-muted-foreground/50 font-mono text-xs">{entry.org_id.slice(0, 8)}</span>}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted-foreground">
                      {t(`resourceTypes.${entry.resource_type}` as Parameters<typeof t>[0])}
                    </td>
                    <td className="px-4 py-2.5 text-muted-foreground text-xs">
                      {entry.resource_name
                        ? <span className="font-medium text-foreground">{entry.resource_name}</span>
                        : <span className="font-mono">{entry.resource_id.slice(0, 8)}</span>}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${reviewActionBadgeClass(entry.action)}`}>
                        {t(`reviewActionLabels.${entry.action}` as Parameters<typeof t>[0])}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted-foreground text-right">
                      {entry.actor_name ?? <span className="text-muted-foreground/50">&mdash;</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>{t("totalReviewEntries", { count: data.total })}</span>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset((o) => Math.max(0, o - REVIEW_PAGE_SIZE))}
              >
                {t("previous")}
              </Button>
              <span>{t("pageOf", { page, pages })}</span>
              <Button
                variant="outline"
                size="sm"
                disabled={offset + REVIEW_PAGE_SIZE >= (data.total ?? 0)}
                onClick={() => setOffset((o) => o + REVIEW_PAGE_SIZE)}
              >
                {tc("next")}
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

export function AdminAudit() {
  const t = useTranslations("admin.audit")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const { formatDateTimeFull } = useDateFormatter()
  const getActionLabel = useAuditActionLabel()
  const [data, setData] = useState<AuditPage | null>(null)
  const [page, setPage] = useState(1)
  const [isLoading, setIsLoading] = useState(true)
  const [isExporting, setIsExporting] = useState(false)
  const [selected, setSelected] = useState<AuditEntry | null>(null)
  const [activeTab, setActiveTab] = useState<"system" | "review" | "calls">("system")
  // Organization names, for the filter and for the column.
  const [orgs, setOrgs] = useState<Array<{ id: string; name: string }>>([])
  // What can be filtered for is what can be recorded: read from the server's registry.
  const [actionGroups, setActionGroups] = useState<ActionGroup[]>([])
  const tGroups = useTranslations("admin.audit.actionGroups")
  const getGroupLabel = (domain: string) => {
    try {
      return tGroups(domain as Parameters<typeof tGroups>[0])
    } catch {
      return domain
    }
  }

  useEffect(() => {
    apiFetch<ActionGroup[]>("/api/admin/audit-log/actions")
      .then(setActionGroups)
      .catch(() => setActionGroups([]))
  }, [])

  useEffect(() => {
    adminApi.listOrganizations(1, 200).then((res) => {
      setOrgs(res.items.map((o: AdminOrganization) => ({ id: o.id, name: o.name })))
    }).catch(() => {
      // The filter stays empty and the column shows the id.
    })
  }, [])
  const orgName = (orgId: string | null | undefined): string | null =>
    orgId ? (orgs.find((o) => o.id === orgId)?.name ?? orgId.slice(0, 8)) : null
  const outcomeLabel = (outcome: string | undefined): string =>
    outcome === "denied"
      ? t("outcomeDenied")
      : outcome === "failed"
        ? t("outcomeFailed")
        : !outcome || outcome === "success"
          ? t("outcomeSuccess")
          : outcome

  // Filters
  const [actionFilter, setActionFilter] = useState("__all__")
  const [orgFilter, setOrgFilter] = useState(ALL)
  const [outcomeFilter, setOutcomeFilter] = useState(ALL)
  // What is typed, and what the list is filtered by (applied on Enter or on leaving the field).
  const [ipInput, setIpInput] = useState("")
  const [ipFilter, setIpFilter] = useState("")
  const [dateFrom, setDateFrom] = useState<Date | undefined>()
  const [dateTo, setDateTo] = useState<Date | undefined>()
  const [dateFromOpen, setDateFromOpen] = useState(false)
  const [dateToOpen, setDateToOpen] = useState(false)

  const hasActiveFilters =
    (actionFilter && actionFilter !== "__all__") || orgFilter !== ALL || outcomeFilter !== ALL || ipFilter || dateFrom || dateTo

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const params = buildParams(
        { action: actionFilter, orgId: orgFilter, outcome: outcomeFilter, ipAddress: ipFilter, dateFrom, dateTo },
        page,
      )
      const res = await apiFetch<AuditPage>(`/api/admin/audit-log?${params}`)
      setData(res)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [page, actionFilter, orgFilter, outcomeFilter, ipFilter, dateFrom, dateTo, tError])

  useEffect(() => { load() }, [load])

  // Reset to page 1 when filters change
  const handleActionChange = (value: string) => {
    setActionFilter(value)
    setPage(1)
  }
  const handleOrgChange = (value: string) => {
    setOrgFilter(value)
    setPage(1)
  }
  const handleOutcomeChange = (value: string) => {
    setOutcomeFilter(value)
    setPage(1)
  }
  const applyIpFilter = () => {
    const next = ipInput.trim()
    if (next !== ipFilter) {
      setIpFilter(next)
      setPage(1)
    }
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
      // The same filters as the list on screen.
      const params = buildParams({
        action: actionFilter, orgId: orgFilter, outcome: outcomeFilter, ipAddress: ipFilter, dateFrom, dateTo,
      })

      const token = typeof window !== "undefined" ? localStorage.getItem(ACCESS_TOKEN_KEY) : null
      const headers: Record<string, string> = {}
      if (token) headers["Authorization"] = `Bearer ${token}`

      const res = await fetch(`${getApiBaseUrl()}/api/admin/audit-log/export?${params}`, { headers })
      if (!res.ok) {
        throw new Error(res.statusText)
      }
      if (res.headers.get("X-Export-Truncated") === "true") {
        toast.warning(
          t("exportTruncated", {
            rows: Number(res.headers.get("X-Export-Rows") ?? 0),
            total: Number(res.headers.get("X-Export-Total") ?? 0),
          }),
        )
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
        {activeTab === "system" && (
          <Button variant="outline" size="sm" onClick={load} disabled={isLoading}>
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${isLoading ? "animate-spin" : ""}`} />
            {tc("refresh")}
          </Button>
        )}
      </div>

      {/* Segmented toggle: System Log | Review Log */}
      <div className="inline-flex items-center rounded-md border border-border bg-muted/40 p-0.5 gap-0.5">
        <button
          onClick={() => setActiveTab("system")}
          className={cn(
            "px-3 py-1.5 text-sm rounded-sm transition-colors font-medium",
            activeTab === "system"
              ? "bg-background shadow-xs text-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {t("systemLog")}
        </button>
        <button
          onClick={() => setActiveTab("review")}
          className={cn(
            "px-3 py-1.5 text-sm rounded-sm transition-colors font-medium",
            activeTab === "review"
              ? "bg-background shadow-xs text-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {t("reviewLog")}
        </button>
        <button
          onClick={() => setActiveTab("calls")}
          className={cn(
            "px-3 py-1.5 text-sm rounded-sm transition-colors font-medium",
            activeTab === "calls"
              ? "bg-background shadow-xs text-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {t("callLog")}
        </button>
      </div>

      {activeTab === "review" ? (
        <ReviewLogPanel />
      ) : activeTab === "calls" ? (
        <CallRecordsPanel endpoint="/api/admin/call-log" orgName={orgName} />
      ) : (
        <>
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
              {actionGroups.map((group) => (
                <SelectGroup key={group.domain}>
                  <SelectLabel>{getGroupLabel(group.domain)}</SelectLabel>
                  {group.actions.map((a) => (
                    <SelectItem key={a} value={a}>
                      {getActionLabel(a)}
                    </SelectItem>
                  ))}
                </SelectGroup>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">{t("filterByOrg")}</label>
          <Select value={orgFilter} onValueChange={handleOrgChange}>
            <SelectTrigger className="w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("allOrgs")}</SelectItem>
              {orgs.map((org) => (
                <SelectItem key={org.id} value={org.id}>
                  {org.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">{t("filterOutcome")}</label>
          <Select value={outcomeFilter} onValueChange={handleOutcomeChange}>
            <SelectTrigger className="w-[150px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("filterOutcomeAll")}</SelectItem>
              {AUDIT_OUTCOMES.map((outcome) => (
                <SelectItem key={outcome} value={outcome}>
                  {outcomeLabel(outcome)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <label htmlFor="audit-ip-filter" className="text-xs font-medium text-muted-foreground">{t("filterIp")}</label>
          <Input
            id="audit-ip-filter"
            value={ipInput}
            onChange={(e) => setIpInput(e.target.value)}
            onBlur={applyIpFilter}
            onKeyDown={(e) => { if (e.key === "Enter") applyIpFilter() }}
            placeholder={t("filterIpPlaceholder")}
            className="h-9 w-[170px] font-mono text-xs"
          />
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
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("outcomeColumn")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("targetColumn")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colOrg")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground whitespace-nowrap">{t("ipColumn")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("detailColumn")}</th>
                  <th className="px-4 py-2.5 w-12 text-right font-medium text-muted-foreground">{tc("actions")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.items.map((entry) => (
                  <tr key={entry.id} className="hover:bg-muted/20 transition-colors">
                    <td className="px-4 py-2.5 text-xs text-muted-foreground whitespace-nowrap tabular-nums">
                      {formatDateTimeFull(entry.created_at)}
                    </td>
                    <td className="px-4 py-2.5 font-medium text-foreground">
                      {entry.admin_username}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${actionColor(entry.action)}`}>
                        {getActionLabel(entry.action)}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${outcomeBadgeClass(entry.outcome ?? "success")}`}>
                        {outcomeLabel(entry.outcome)}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-muted-foreground text-xs">
                      {entry.target_label
                        ? <span className="font-medium text-foreground">{entry.target_label}</span>
                        : <span className="text-muted-foreground/50">&mdash;</span>}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted-foreground">
                      {orgName(entry.org_id) ?? <span className="text-muted-foreground/50">&mdash;</span>}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground whitespace-nowrap">
                      {entry.ip_address ?? <span className="text-muted-foreground/50">&mdash;</span>}
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
              {selected && formatDateTimeFull(selected.created_at)}
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

              {selected.changes && Object.keys(selected.changes).length > 0 && (
                <>
                  <span className="text-muted-foreground">{t("changesColumn")}</span>
                  <span className="space-y-1">
                    {Object.entries(selected.changes).map(([field, change]) => (
                      <span key={field} className="block break-words">
                        <span className="font-mono text-xs text-muted-foreground">{field}</span>{" "}
                        {formatAuditChange(change, t("changedValue"))}
                      </span>
                    ))}
                  </span>
                </>
              )}

              <span className="text-muted-foreground">{t("outcomeColumn")}</span>
              <span className="font-medium">{outcomeLabel(selected.outcome)}</span>

              {selected.org_id && (
                <>
                  <span className="text-muted-foreground">{t("colOrg")}</span>
                  <span>
                    <span className="font-medium">{orgName(selected.org_id)}</span>{" "}
                    <span className="font-mono text-xs text-muted-foreground break-all">{selected.org_id}</span>
                  </span>
                </>
              )}

              {selected.ip_address && (
                <>
                  <span className="text-muted-foreground">{t("ipColumn")}</span>
                  <span className="font-mono text-xs">{selected.ip_address}</span>
                </>
              )}

              {selected.user_agent && (
                <>
                  <span className="text-muted-foreground">{t("userAgentColumn")}</span>
                  <span className="text-xs text-muted-foreground break-words">{selected.user_agent}</span>
                </>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
        </>
      )}
    </div>
  )
}
