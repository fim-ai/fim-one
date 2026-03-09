"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations, useLocale } from "next-intl"
import { Shield, Plus, Loader2, MoreHorizontal, Power, Trash2 } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { adminApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface LoginHistoryEntry {
  id: string
  user_id: string | null
  username: string | null
  email: string | null
  ip_address: string | null
  user_agent: string | null
  success: boolean
  failure_reason: string | null
  created_at: string
}

interface LoginStats {
  total_attempts: number
  successful: number
  failed: number
  unique_ips: number
  unique_users: number
  recent_failures: number
}

interface IpRule {
  id: string
  ip_address: string
  rule_type: string
  note: string | null
  is_active: boolean
  created_at: string
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const PAGE_SIZE = 20

function truncateUA(ua: string | null, maxLen = 60): string {
  if (!ua) return "\u2014"
  return ua.length > maxLen ? `${ua.slice(0, maxLen)}...` : ua
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AdminSecurity() {
  const t = useTranslations("admin.security")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const locale = useLocale()

  // --- Login history state ---
  const [historyLoading, setHistoryLoading] = useState(true)
  const [history, setHistory] = useState<LoginHistoryEntry[]>([])
  const [historyTotal, setHistoryTotal] = useState(0)
  const [historyPage, setHistoryPage] = useState(1)
  const [historyPages, setHistoryPages] = useState(1)
  const [statusFilter, setStatusFilter] = useState<string>("__all__")

  // --- Login stats state ---
  const [stats, setStats] = useState<LoginStats | null>(null)

  // --- IP rules state ---
  const [rulesLoading, setRulesLoading] = useState(true)
  const [rules, setRules] = useState<IpRule[]>([])

  // --- Add rule dialog ---
  const [addRuleOpen, setAddRuleOpen] = useState(false)
  const [newIp, setNewIp] = useState("")
  const [newRuleType, setNewRuleType] = useState("deny")
  const [newNote, setNewNote] = useState("")
  const [isMutating, setIsMutating] = useState(false)

  // --- Delete rule confirmation ---
  const [deleteTarget, setDeleteTarget] = useState<IpRule | null>(null)

  // ---------------------------------------------------------------------------
  // Data loading
  // ---------------------------------------------------------------------------

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true)
    try {
      const params: { page?: number; size?: number; success?: boolean } = {
        page: historyPage,
        size: PAGE_SIZE,
      }
      if (statusFilter === "success") params.success = true
      if (statusFilter === "failed") params.success = false

      const data = await adminApi.getLoginHistory(params)
      setHistory(data.items)
      setHistoryTotal(data.total)
      setHistoryPages(Math.max(1, Math.ceil(data.total / PAGE_SIZE)))
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setHistoryLoading(false)
    }
  }, [historyPage, statusFilter, tError])

  const loadStats = useCallback(async () => {
    try {
      const data = await adminApi.getLoginStats()
      setStats(data)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }, [tError])

  const loadRules = useCallback(async () => {
    setRulesLoading(true)
    try {
      const data = await adminApi.listIpRules()
      setRules(data)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setRulesLoading(false)
    }
  }, [tError])

  useEffect(() => {
    loadStats()
    loadRules()
  }, [loadStats, loadRules])

  useEffect(() => {
    loadHistory()
  }, [loadHistory])

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  const handleStatusFilterChange = (value: string) => {
    setStatusFilter(value)
    setHistoryPage(1)
  }

  const handleCreateRule = async () => {
    if (!newIp.trim()) return
    setIsMutating(true)
    try {
      await adminApi.createIpRule({
        ip_address: newIp.trim(),
        rule_type: newRuleType,
        note: newNote.trim() || undefined,
      })
      toast.success(t("ruleCreated"))
      setAddRuleOpen(false)
      setNewIp("")
      setNewRuleType("deny")
      setNewNote("")
      loadRules()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  const handleToggleRule = async (rule: IpRule) => {
    try {
      await adminApi.toggleIpRule(rule.id, !rule.is_active)
      toast.success(t("ruleToggled"))
      loadRules()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  const handleDeleteRule = async () => {
    if (!deleteTarget) return
    setIsMutating(true)
    try {
      await adminApi.deleteIpRule(deleteTarget.id)
      toast.success(t("ruleDeleted"))
      setDeleteTarget(null)
      loadRules()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="space-y-8">
      {/* ================================================================= */}
      {/* Page header                                                       */}
      {/* ================================================================= */}
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {/* ================================================================= */}
      {/* Stats bar                                                         */}
      {/* ================================================================= */}
      {stats && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <p className="text-xs font-medium text-muted-foreground">{t("statsTotal")}</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums">{stats.total_attempts.toLocaleString()}</p>
          </div>
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <p className="text-xs font-medium text-muted-foreground">{t("statsSuccess")}</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums text-green-600 dark:text-green-400">
              {stats.successful.toLocaleString()}
            </p>
          </div>
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <p className="text-xs font-medium text-muted-foreground">{t("statsFailed")}</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums text-red-600 dark:text-red-400">
              {stats.failed.toLocaleString()}
            </p>
          </div>
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <p className="text-xs font-medium text-muted-foreground">{t("statsUniqueIps")}</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums">{stats.unique_ips.toLocaleString()}</p>
          </div>
        </div>
      )}

      {/* ================================================================= */}
      {/* Section 1: Login History                                          */}
      {/* ================================================================= */}
      <div className="space-y-4">
        <div className="flex items-start justify-between">
          <div>
            <h3 className="text-sm font-semibold">{t("historyTitle")}</h3>
            <p className="text-xs text-muted-foreground">{t("historySubtitle")}</p>
          </div>
          <Select value={statusFilter} onValueChange={handleStatusFilterChange}>
            <SelectTrigger className="w-[140px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">{tc("all")}</SelectItem>
              <SelectItem value="success">{t("statusSuccess")}</SelectItem>
              <SelectItem value="failed">{t("statusFailed")}</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {historyLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : history.length === 0 ? (
          <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
            {t("noHistory")}
          </div>
        ) : (
          <>
            <div className="rounded-md border border-border overflow-x-auto">
              <table className="w-full min-w-max text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/40">
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground whitespace-nowrap">
                      {t("colTime")}
                    </th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                      {t("colUser")}
                    </th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                      {t("colIp")}
                    </th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                      {t("colStatus")}
                    </th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                      {t("colUserAgent")}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {history.map((entry) => (
                    <tr key={entry.id} className="hover:bg-muted/20 transition-colors">
                      <td className="px-4 py-2.5 text-xs text-muted-foreground whitespace-nowrap tabular-nums">
                        {new Date(entry.created_at).toLocaleString(locale, {
                          year: "numeric",
                          month: "short",
                          day: "numeric",
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                        })}
                      </td>
                      <td className="px-4 py-2.5 font-medium text-foreground">
                        {entry.username || entry.email || "\u2014"}
                      </td>
                      <td className="px-4 py-2.5 text-muted-foreground font-mono text-xs">
                        {entry.ip_address || "\u2014"}
                      </td>
                      <td className="px-4 py-2.5">
                        {entry.success ? (
                          <Badge
                            variant="outline"
                            className="border-green-500/40 text-green-600 dark:text-green-400"
                          >
                            {t("statusSuccess")}
                          </Badge>
                        ) : (
                          <Badge
                            variant="outline"
                            className="border-red-500/40 text-red-600 dark:text-red-400"
                          >
                            {t("statusFailed")}
                          </Badge>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-xs text-muted-foreground max-w-[260px] truncate">
                        {truncateUA(entry.user_agent)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            <div className="flex items-center justify-between text-sm text-muted-foreground">
              <span>{t("totalRecords", { count: historyTotal })}</span>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={historyPage <= 1}
                  onClick={() => setHistoryPage((p) => Math.max(1, p - 1))}
                >
                  {t("previous")}
                </Button>
                <span>
                  {t("pageOf", { page: historyPage, pages: historyPages })}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={historyPage >= historyPages}
                  onClick={() => setHistoryPage((p) => Math.min(historyPages, p + 1))}
                >
                  {tc("next")}
                </Button>
              </div>
            </div>
          </>
        )}
      </div>

      {/* ================================================================= */}
      {/* Section 2: IP Rules                                               */}
      {/* ================================================================= */}
      <div className="space-y-4">
        <div className="flex items-start justify-between">
          <div>
            <h3 className="text-sm font-semibold">{t("ipRulesTitle")}</h3>
            <p className="text-xs text-muted-foreground">{t("ipRulesSubtitle")}</p>
          </div>
          <Button onClick={() => setAddRuleOpen(true)} className="gap-1.5">
            <Plus className="h-4 w-4" />
            {t("addRule")}
          </Button>
        </div>

        {rulesLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : rules.length === 0 ? (
          <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
            {t("noRules")}
          </div>
        ) : (
          <div className="rounded-md border border-border overflow-x-auto">
            <table className="w-full min-w-max text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/40">
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                    {t("ipAddress")}
                  </th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                    {t("ruleType")}
                  </th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                    {t("note")}
                  </th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                    {tc("status")}
                  </th>
                  <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{tc("actions")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rules.map((rule) => (
                  <tr key={rule.id} className="hover:bg-muted/20 transition-colors">
                    <td className="px-4 py-3 font-mono text-xs text-foreground">
                      {rule.ip_address}
                    </td>
                    <td className="px-4 py-3">
                      {rule.rule_type === "allow" ? (
                        <Badge
                          variant="outline"
                          className="border-green-500/40 text-green-600 dark:text-green-400"
                        >
                          {t("ruleAllow")}
                        </Badge>
                      ) : (
                        <Badge
                          variant="outline"
                          className="border-red-500/40 text-red-600 dark:text-red-400"
                        >
                          {t("ruleDeny")}
                        </Badge>
                      )}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground text-xs max-w-[200px] truncate">
                      {rule.note || "\u2014"}
                    </td>
                    <td className="px-4 py-3">
                      {rule.is_active ? (
                        <Badge variant="outline" className="border-green-500/40 text-green-600 dark:text-green-400">{tc("active")}</Badge>
                      ) : (
                        <Badge variant="outline" className="border-red-500/40 text-red-600 dark:text-red-400">{tc("disabled")}</Badge>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => handleToggleRule(rule)}>
                            <Power className="mr-2 h-4 w-4" />
                            {rule.is_active ? tc("disable") : tc("enable")}
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            variant="destructive"
                            onClick={() => setDeleteTarget(rule)}
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
      </div>

      {/* ================================================================= */}
      {/* Add Rule Dialog                                                   */}
      {/* ================================================================= */}
      <Dialog open={addRuleOpen} onOpenChange={setAddRuleOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("addRule")}</DialogTitle>
            <DialogDescription>
              {t("ipRulesSubtitle")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label>{t("ipAddress")} <span className="text-destructive">*</span></Label>
              <Input
                value={newIp}
                onChange={(e) => setNewIp(e.target.value)}
                placeholder="192.168.1.0/24"
              />
            </div>
            <div className="space-y-2">
              <Label>{t("ruleType")}</Label>
              <Select value={newRuleType} onValueChange={setNewRuleType}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="allow">{t("ruleAllow")}</SelectItem>
                  <SelectItem value="deny">{t("ruleDeny")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>{t("note")}</Label>
              <Textarea
                value={newNote}
                onChange={(e) => setNewNote(e.target.value)}
                placeholder={t("note")}
                rows={3}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddRuleOpen(false)}>
              {tc("cancel")}
            </Button>
            <Button
              onClick={handleCreateRule}
              disabled={isMutating || !newIp.trim()}
            >
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ================================================================= */}
      {/* Delete Rule AlertDialog (sibling of Dialog, never nested)         */}
      {/* ================================================================= */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteRuleTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteRuleDesc", { ip: deleteTarget?.ip_address ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteRule}
              disabled={isMutating}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
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
