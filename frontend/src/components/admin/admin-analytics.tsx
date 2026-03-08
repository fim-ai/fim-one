"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import {
  BarChart3,
  Download,
  Megaphone,
  Plus,
  Loader2,
  Trash2,
  Pencil,
  FileText,
  MessageSquare,
  Database,
} from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
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
import { cn } from "@/lib/utils"
import { adminApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import { getApiBaseUrl, ACCESS_TOKEN_KEY } from "@/lib/constants"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface UsageEntry {
  user_id: string
  username: string | null
  email: string | null
  total_tokens: number
  conversation_count: number
  token_quota: number | null
}

interface TrendEntry {
  date: string
  total_tokens: number
  conversation_count: number
  active_users: number
}

interface CostEstimate {
  total_tokens: number
  estimated_cost: number
  by_model: { model: string; tokens: number; cost: number }[]
}

interface AnnouncementInfo {
  id: string
  title: string
  content: string
  level: string
  is_active: boolean
  starts_at: string | null
  ends_at: string | null
  target_group: string | null
  created_at: string
}

// ---------------------------------------------------------------------------
// Sub-tab type
// ---------------------------------------------------------------------------

type SubTab = "analytics" | "export" | "announcements"

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AdminAnalytics() {
  const t = useTranslations("admin.analytics")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  const [activeTab, setActiveTab] = useState<SubTab>("analytics")

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {/* Sub-tab toggle */}
      <div className="flex items-center gap-1 rounded-md border border-border bg-muted/40 p-1 w-fit">
        <Button
          variant={activeTab === "analytics" ? "default" : "ghost"}
          size="sm"
          className="gap-1.5"
          onClick={() => setActiveTab("analytics")}
        >
          <BarChart3 className="h-4 w-4" />
          {t("analyticsTab")}
        </Button>
        <Button
          variant={activeTab === "export" ? "default" : "ghost"}
          size="sm"
          className="gap-1.5"
          onClick={() => setActiveTab("export")}
        >
          <Download className="h-4 w-4" />
          {t("exportTab")}
        </Button>
        <Button
          variant={activeTab === "announcements" ? "default" : "ghost"}
          size="sm"
          className="gap-1.5"
          onClick={() => setActiveTab("announcements")}
        >
          <Megaphone className="h-4 w-4" />
          {t("announcementsTab")}
        </Button>
      </div>

      {/* Sub-tab content */}
      {activeTab === "analytics" && <AnalyticsSection t={t} tc={tc} tError={tError} />}
      {activeTab === "export" && <ExportSection t={t} tError={tError} />}
      {activeTab === "announcements" && <AnnouncementsSection t={t} tc={tc} tError={tError} />}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Analytics Section
// ---------------------------------------------------------------------------

function AnalyticsSection({
  t,
  tc,
  tError,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  t: (key: string, args?: any) => string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  tc: (key: string, args?: any) => string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  tError: (key: string, args?: any) => string
}) {
  const [period, setPeriod] = useState<"week" | "month" | "all">("month")
  const [usage, setUsage] = useState<UsageEntry[]>([])
  const [trends, setTrends] = useState<TrendEntry[]>([])
  const [cost, setCost] = useState<CostEstimate | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const [usageData, trendData, costData] = await Promise.all([
        adminApi.getUsageAnalytics({ period, top_n: 20 }),
        adminApi.getUsageTrends(),
        adminApi.getCostEstimate(),
      ])
      setUsage(usageData as UsageEntry[])
      setTrends(trendData as TrendEntry[])
      setCost(costData as CostEstimate)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [period, tError])

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load() }, [period])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const maxTrendTokens = Math.max(...trends.map((d) => d.total_tokens), 1)

  return (
    <div className="space-y-6">
      {/* Period selector */}
      <div className="flex items-center gap-1 rounded-md border border-border bg-muted/40 p-1 w-fit">
        {(["week", "month", "all"] as const).map((p) => (
          <Button
            key={p}
            variant={period === p ? "default" : "ghost"}
            size="sm"
            onClick={() => setPeriod(p)}
          >
            {t(p === "week" ? "periodWeek" : p === "month" ? "periodMonth" : "periodAll")}
          </Button>
        ))}
      </div>

      {/* Cost summary */}
      {cost && (
        <div className="rounded-md border border-border p-4 space-y-3">
          <h3 className="text-sm font-semibold">{t("costTitle")}</h3>
          <div className="flex items-center gap-6 text-sm">
            <div>
              <span className="text-muted-foreground">{t("totalTokens")}: </span>
              <span className="font-medium tabular-nums">{cost.total_tokens.toLocaleString()}</span>
            </div>
            <div>
              <span className="text-muted-foreground">{t("estimatedCost")}: </span>
              <span className="font-medium tabular-nums">${cost.estimated_cost.toFixed(2)}</span>
            </div>
          </div>
          {cost.by_model.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground font-medium">{t("costByModel")}</p>
              <div className="flex flex-wrap gap-3 text-xs">
                {cost.by_model.map((m) => (
                  <span key={m.model} className="text-muted-foreground">
                    {m.model}: {m.tokens.toLocaleString()} (${m.cost.toFixed(2)})
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Top users table */}
      {usage.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {t("noUsageData")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colUser")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colEmail")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("colConversations")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("colTokens")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("colQuota")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("colCost")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {usage.map((u) => {
                const estimatedCost = (u.total_tokens / 1000) * 0.01
                return (
                  <tr key={u.user_id} className="hover:bg-muted/20 transition-colors">
                    <td className="px-4 py-3 font-medium text-foreground">{u.username || "--"}</td>
                    <td className="px-4 py-3 text-muted-foreground">{u.email || "--"}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{u.conversation_count.toLocaleString()}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{u.total_tokens.toLocaleString()}</td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {u.token_quota !== null ? u.token_quota.toLocaleString() : <span className="text-muted-foreground/50">--</span>}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">${estimatedCost.toFixed(2)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* 30-day trend chart */}
      {trends.length > 0 && (
        <div className="rounded-md border border-border p-4 space-y-3">
          <div>
            <h3 className="text-sm font-semibold">{t("trendTitle")}</h3>
            <p className="text-xs text-muted-foreground">{t("trendSubtitle")}</p>
          </div>
          <div className="space-y-1.5">
            {trends.map((d) => {
              const pct = (d.total_tokens / maxTrendTokens) * 100
              return (
                <div key={d.date} className="flex items-center gap-3 text-xs">
                  <span className="w-20 shrink-0 text-muted-foreground tabular-nums">{d.date}</span>
                  <div className="flex-1 flex items-center gap-2">
                    <div
                      className="h-4 bg-primary rounded"
                      style={{ width: `${Math.max(pct, 1)}%` }}
                    />
                    <span className="shrink-0 text-muted-foreground tabular-nums">
                      {d.total_tokens.toLocaleString()}
                    </span>
                  </div>
                  <span className="shrink-0 text-muted-foreground/70 tabular-nums" title={t("trendConvs")}>
                    {d.conversation_count}c
                  </span>
                  <span className="shrink-0 text-muted-foreground/70 tabular-nums" title={t("trendUsers")}>
                    {d.active_users}u
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Export Section
// ---------------------------------------------------------------------------

function ExportSection({
  t,
  tError,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  t: (key: string, args?: any) => string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  tError: (key: string, args?: any) => string
}) {
  const [downloading, setDownloading] = useState<string | null>(null)

  const handleExport = async (endpoint: string, filename: string) => {
    setDownloading(endpoint)
    try {
      const res = await fetch(`${getApiBaseUrl()}${endpoint}`, {
        headers: { Authorization: `Bearer ${localStorage.getItem(ACCESS_TOKEN_KEY)}` },
      })
      if (!res.ok) throw new Error("Export failed")
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
      toast.success(t("exportSuccess"))
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setDownloading(null)
    }
  }

  const cards = [
    {
      key: "users",
      icon: FileText,
      title: t("exportUsers"),
      desc: t("exportUsersDesc"),
      endpoint: "/api/admin/export/users",
      filename: `users-${new Date().toISOString().slice(0, 10)}.csv`,
    },
    {
      key: "conversations",
      icon: MessageSquare,
      title: t("exportConversations"),
      desc: t("exportConversationsDesc"),
      endpoint: "/api/admin/export/conversations",
      filename: `conversations-${new Date().toISOString().slice(0, 10)}.csv`,
    },
    {
      key: "backup",
      icon: Database,
      title: t("exportBackup"),
      desc: t("exportBackupDesc"),
      endpoint: "/api/admin/export/full-backup",
      filename: `backup-${new Date().toISOString().slice(0, 10)}.json`,
    },
  ]

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-sm font-semibold">{t("exportTitle")}</h3>
        <p className="text-xs text-muted-foreground">{t("exportSubtitle")}</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {cards.map((c) => {
          const Icon = c.icon
          const isActive = downloading === c.endpoint
          return (
            <div
              key={c.key}
              className="rounded-md border border-border p-4 space-y-3 flex flex-col"
            >
              <div className="flex items-center gap-2">
                <Icon className="h-5 w-5 text-muted-foreground" />
                <h4 className="text-sm font-medium">{c.title}</h4>
              </div>
              <p className="text-xs text-muted-foreground flex-1">{c.desc}</p>
              <Button
                variant="outline"
                size="sm"
                className="w-full gap-1.5"
                disabled={isActive}
                onClick={() => handleExport(c.endpoint, c.filename)}
              >
                {isActive ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Download className="h-4 w-4" />
                )}
                {t("download")}
              </Button>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Announcements Section
// ---------------------------------------------------------------------------

function AnnouncementsSection({
  t,
  tc,
  tError,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  t: (key: string, args?: any) => string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  tc: (key: string, args?: any) => string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  tError: (key: string, args?: any) => string
}) {
  const [announcements, setAnnouncements] = useState<AnnouncementInfo[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [isMutating, setIsMutating] = useState(false)

  // Dialog state
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<AnnouncementInfo | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<AnnouncementInfo | null>(null)

  // Form fields
  const [formTitle, setFormTitle] = useState("")
  const [formContent, setFormContent] = useState("")
  const [formLevel, setFormLevel] = useState("info")
  const [formStartsAt, setFormStartsAt] = useState("")
  const [formEndsAt, setFormEndsAt] = useState("")

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const data = await adminApi.listAnnouncements()
      setAnnouncements(data as AnnouncementInfo[])
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [tError])

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load() }, [])

  const resetForm = () => {
    setFormTitle("")
    setFormContent("")
    setFormLevel("info")
    setFormStartsAt("")
    setFormEndsAt("")
  }

  const openCreate = () => {
    resetForm()
    setEditTarget(null)
    setDialogOpen(true)
  }

  const openEdit = (ann: AnnouncementInfo) => {
    setEditTarget(ann)
    setFormTitle(ann.title)
    setFormContent(ann.content)
    setFormLevel(ann.level)
    setFormStartsAt(ann.starts_at ?? "")
    setFormEndsAt(ann.ends_at ?? "")
    setDialogOpen(true)
  }

  const handleSubmit = async () => {
    if (!formTitle.trim() || !formContent.trim()) return
    setIsMutating(true)
    try {
      if (editTarget) {
        await adminApi.updateAnnouncement(editTarget.id, {
          title: formTitle.trim(),
          content: formContent.trim(),
          level: formLevel,
          starts_at: formStartsAt || null,
          ends_at: formEndsAt || null,
        })
        toast.success(t("annUpdated"))
      } else {
        await adminApi.createAnnouncement({
          title: formTitle.trim(),
          content: formContent.trim(),
          level: formLevel,
          starts_at: formStartsAt || undefined,
          ends_at: formEndsAt || undefined,
        })
        toast.success(t("annCreated"))
      }
      setDialogOpen(false)
      resetForm()
      setEditTarget(null)
      await load()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setIsMutating(true)
    try {
      await adminApi.deleteAnnouncement(deleteTarget.id)
      toast.success(t("annDeleted"))
      setDeleteTarget(null)
      await load()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  const levelBadge = (level: string) => {
    const styles: Record<string, string> = {
      info: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
      warning: "bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 border-yellow-500/20",
      error: "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20",
    }
    const labels: Record<string, string> = {
      info: t("levelInfo"),
      warning: t("levelWarning"),
      error: t("levelError"),
    }
    return (
      <Badge variant="secondary" className={cn("text-[10px] px-1.5 py-0", styles[level] ?? "")}>
        {labels[level] ?? level}
      </Badge>
    )
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h3 className="text-sm font-semibold">{t("annTitle")}</h3>
          <p className="text-xs text-muted-foreground">{t("annSubtitle")}</p>
        </div>
        <Button onClick={openCreate} className="gap-1.5" size="sm">
          <Plus className="h-4 w-4" />
          {t("createAnn")}
        </Button>
      </div>

      {/* Table */}
      {announcements.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {t("noAnnouncements")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colAnnTitle")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colLevel")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colActive")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colSchedule")}</th>
                <th className="px-4 py-2.5 w-20" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {announcements.map((ann) => (
                <tr key={ann.id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3 font-medium text-foreground max-w-[200px] truncate">
                    {ann.title}
                  </td>
                  <td className="px-4 py-3">{levelBadge(ann.level)}</td>
                  <td className="px-4 py-3">
                    {ann.is_active ? (
                      <Badge variant="outline" className="border-green-500/40 text-green-600 dark:text-green-400">
                        {tc("active")}
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="border-red-500/40 text-red-600 dark:text-red-400">
                        {tc("disabled")}
                      </Badge>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {ann.starts_at || ann.ends_at ? (
                      <span>
                        {ann.starts_at ? new Date(ann.starts_at).toLocaleDateString() : "--"}
                        {" ~ "}
                        {ann.ends_at ? new Date(ann.ends_at).toLocaleDateString() : "--"}
                      </span>
                    ) : (
                      <span className="text-muted-foreground/50">--</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1 justify-end">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 p-0"
                        onClick={() => openEdit(ann)}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 p-0 text-destructive"
                        onClick={() => setDeleteTarget(ann)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create / Edit Dialog */}
      <Dialog
        open={dialogOpen}
        onOpenChange={(open) => {
          if (!open) {
            setDialogOpen(false)
            setEditTarget(null)
            resetForm()
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editTarget ? t("editAnn") : t("createAnn")}</DialogTitle>
            <DialogDescription>
              {editTarget ? t("editAnn") : t("createAnn")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label className="text-sm font-medium">
                {t("annTitleLabel")} <span className="text-destructive">*</span>
              </Label>
              <Input
                value={formTitle}
                onChange={(e) => setFormTitle(e.target.value)}
                placeholder={t("annTitleLabel")}
              />
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">
                {t("annContent")} <span className="text-destructive">*</span>
              </Label>
              <Textarea
                value={formContent}
                onChange={(e) => setFormContent(e.target.value)}
                placeholder={t("annContent")}
                rows={4}
              />
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">{t("annLevel")}</Label>
              <Select value={formLevel} onValueChange={setFormLevel}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="info">{t("levelInfo")}</SelectItem>
                  <SelectItem value="warning">{t("levelWarning")}</SelectItem>
                  <SelectItem value="error">{t("levelError")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label className="text-sm font-medium">{t("startsAt")}</Label>
                <Input
                  type="datetime-local"
                  value={formStartsAt}
                  onChange={(e) => setFormStartsAt(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label className="text-sm font-medium">{t("endsAt")}</Label>
                <Input
                  type="datetime-local"
                  value={formEndsAt}
                  onChange={(e) => setFormEndsAt(e.target.value)}
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setDialogOpen(false)
                setEditTarget(null)
                resetForm()
              }}
            >
              {tc("cancel")}
            </Button>
            <Button
              onClick={handleSubmit}
              disabled={isMutating || !formTitle.trim() || !formContent.trim()}
            >
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete AlertDialog */}
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteAnnTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteAnnDesc")}
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
