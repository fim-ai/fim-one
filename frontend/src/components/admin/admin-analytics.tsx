"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import {
  BarChart3,
  Download,
  Loader2,
  FileText,
  MessageSquare,
  Database,
  Bot,
  Plug,
  GitBranch,
  TrendingUp,
} from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { adminApi } from "@/lib/api"
import type {
  AdminAnalyticsByAgent,
  AdminAnalyticsByConnector,
  AdminAnalyticsByWorkflow,
  AdminCostProjection,
} from "@/lib/api"
import { formatTokens } from "@/lib/utils"
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

// ---------------------------------------------------------------------------
// Sub-tab type
// ---------------------------------------------------------------------------

type SubTab = "analytics" | "byAgent" | "byConnector" | "byWorkflow" | "costProjection" | "export"

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AdminAnalytics() {
  const t = useTranslations("admin.analytics")
  const te = useTranslations("admin.analyticsEnhanced")
  const tError = useTranslations("errors")

  const [activeTab, setActiveTab] = useState<SubTab>("analytics")

  const tabs: { key: SubTab; icon: React.ElementType; label: string }[] = [
    { key: "analytics", icon: BarChart3, label: t("analyticsTab") },
    { key: "byAgent", icon: Bot, label: te("byAgentTab") },
    { key: "byConnector", icon: Plug, label: te("byConnectorTab") },
    { key: "byWorkflow", icon: GitBranch, label: te("byWorkflowTab") },
    { key: "costProjection", icon: TrendingUp, label: te("costProjectionTab") },
    { key: "export", icon: Download, label: t("exportTab") },
  ]

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {/* Sub-tab toggle */}
      <div className="inline-flex items-center rounded-md border border-border bg-muted/40 p-0.5 gap-0.5 flex-wrap">
        {tabs.map(({ key, icon: Icon, label }) => (
          <button
            key={key}
            onClick={() => setActiveTab(key)}
            className={cn(
              "inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-sm transition-colors font-medium",
              activeTab === key
                ? "bg-background shadow-xs text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        ))}
      </div>

      {/* Sub-tab content */}
      {activeTab === "analytics" && <AnalyticsSection t={t} tError={tError} />}
      {activeTab === "byAgent" && <ByAgentSection te={te} tError={tError} />}
      {activeTab === "byConnector" && <ByConnectorSection te={te} tError={tError} />}
      {activeTab === "byWorkflow" && <ByWorkflowSection te={te} tError={tError} />}
      {activeTab === "costProjection" && <CostProjectionSection te={te} tError={tError} />}
      {activeTab === "export" && <ExportSection t={t} tError={tError} />}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Analytics Section
// ---------------------------------------------------------------------------

function AnalyticsSection({
  t,
  tError,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  t: (key: string, args?: any) => string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  tError: (key: string, args?: any) => string
}) {
  const [period, setPeriod] = useState<"week" | "month" | "all">("month")
  const [usage, setUsage] = useState<UsageEntry[]>([])
  const [trends, setTrends] = useState<TrendEntry[]>([])
  const [isLoading, setIsLoading] = useState(true)

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const [usageData, trendData] = await Promise.all([
        adminApi.getUsageAnalytics({ period, top_n: 20 }),
        adminApi.getUsageTrends(),
      ])
      setUsage(usageData as UsageEntry[])
      setTrends(trendData as TrendEntry[])
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
      <div className="inline-flex items-center rounded-md border border-border bg-muted/40 p-0.5 gap-0.5">
        {(["week", "month", "all"] as const).map((p) => (
          <button
            key={p}
            onClick={() => setPeriod(p)}
            className={cn(
              "px-3 py-1.5 text-sm rounded-sm transition-colors font-medium",
              period === p ? "bg-background shadow-xs text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t(p === "week" ? "periodWeek" : p === "month" ? "periodMonth" : "periodAll")}
          </button>
        ))}
      </div>

      {/* Top users table */}
      {usage.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {t("noUsageData")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colUser")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colEmail")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("colConversations")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("colTokens")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("colQuota")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {usage.map((u) => (
                  <tr key={u.user_id} className="hover:bg-muted/20 transition-colors">
                    <td className="px-4 py-3 font-medium text-foreground">{u.username || "--"}</td>
                    <td className="px-4 py-3 text-muted-foreground">{u.email || "--"}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{u.conversation_count.toLocaleString()}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{formatTokens(u.total_tokens)}</td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {u.token_quota !== null ? formatTokens(u.token_quota) : <span className="text-muted-foreground/50">--</span>}
                    </td>
                  </tr>
              ))}
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
                      {formatTokens(d.total_tokens)}
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
// By Agent Section
// ---------------------------------------------------------------------------

function ByAgentSection({
  te,
  tError,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  te: (key: string, args?: any) => string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  tError: (key: string, args?: any) => string
}) {
  const [period, setPeriod] = useState<"7d" | "30d" | "90d">("7d")
  const [data, setData] = useState<AdminAnalyticsByAgent[]>([])
  const [isLoading, setIsLoading] = useState(true)

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const result = await adminApi.getAnalyticsByAgent(period)
      setData(result)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [period, tError])

  useEffect(() => { load() }, [load])

  return (
    <div className="space-y-4">
      <div className="inline-flex items-center rounded-md border border-border bg-muted/40 p-0.5 gap-0.5">
        {(["7d", "30d", "90d"] as const).map((p) => (
          <button key={p} onClick={() => setPeriod(p)} className={cn(
            "px-3 py-1.5 text-sm rounded-sm transition-colors font-medium",
            period === p ? "bg-background shadow-xs text-foreground" : "text-muted-foreground hover:text-foreground",
          )}>
            {te(`period${p}`)}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : data.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {te("noAgentData")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{te("colAgentName")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{te("colOwner")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{te("colConversations")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{te("colTotalTokens")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{te("colAvgTokens")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {data.map((item, i) => (
                <tr key={i} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3 font-medium text-foreground">{item.agent_name}</td>
                  <td className="px-4 py-3 text-muted-foreground">{item.owner || "--"}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{(item.conversations ?? 0).toLocaleString()}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{formatTokens(item.total_tokens ?? 0)}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{formatTokens(item.avg_tokens_per_conv ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// By Connector Section
// ---------------------------------------------------------------------------

function ByConnectorSection({
  te,
  tError,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  te: (key: string, args?: any) => string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  tError: (key: string, args?: any) => string
}) {
  const [period, setPeriod] = useState<"7d" | "30d" | "90d">("7d")
  const [data, setData] = useState<AdminAnalyticsByConnector[]>([])
  const [isLoading, setIsLoading] = useState(true)

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const result = await adminApi.getAnalyticsByConnector(period)
      setData(result)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [period, tError])

  useEffect(() => { load() }, [load])

  return (
    <div className="space-y-4">
      <div className="inline-flex items-center rounded-md border border-border bg-muted/40 p-0.5 gap-0.5">
        {(["7d", "30d", "90d"] as const).map((p) => (
          <button key={p} onClick={() => setPeriod(p)} className={cn(
            "px-3 py-1.5 text-sm rounded-sm transition-colors font-medium",
            period === p ? "bg-background shadow-xs text-foreground" : "text-muted-foreground hover:text-foreground",
          )}>
            {te(`period${p}`)}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : data.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {te("noConnectorData")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{te("colConnectorName")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{te("colTotalCalls")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{te("colSuccessRate")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{te("colAvgResponseTime")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{te("colErrors")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {data.map((item, i) => (
                <tr key={i} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3 font-medium text-foreground">{item.connector_name}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{(item.total_calls ?? 0).toLocaleString()}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="h-2 w-16 rounded-full bg-muted overflow-hidden">
                        <div
                          className="h-full rounded-full bg-green-500"
                          style={{ width: `${item.success_rate ?? 0}%` }}
                        />
                      </div>
                      <span className="text-xs tabular-nums">{(item.success_rate ?? 0).toFixed(1)}%</span>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">{Math.round(item.avg_response_time_ms ?? 0)}ms</td>
                  <td className="px-4 py-3 text-right tabular-nums text-red-600 dark:text-red-400">{item.errors ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// By Workflow Section
// ---------------------------------------------------------------------------

function ByWorkflowSection({
  te,
  tError,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  te: (key: string, args?: any) => string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  tError: (key: string, args?: any) => string
}) {
  const [period, setPeriod] = useState<"7d" | "30d" | "90d">("7d")
  const [data, setData] = useState<AdminAnalyticsByWorkflow[]>([])
  const [isLoading, setIsLoading] = useState(true)

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const result = await adminApi.getAnalyticsByWorkflow(period)
      setData(result)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [period, tError])

  useEffect(() => { load() }, [load])

  return (
    <div className="space-y-4">
      <div className="inline-flex items-center rounded-md border border-border bg-muted/40 p-0.5 gap-0.5">
        {(["7d", "30d", "90d"] as const).map((p) => (
          <button key={p} onClick={() => setPeriod(p)} className={cn(
            "px-3 py-1.5 text-sm rounded-sm transition-colors font-medium",
            period === p ? "bg-background shadow-xs text-foreground" : "text-muted-foreground hover:text-foreground",
          )}>
            {te(`period${p}`)}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : data.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {te("noWorkflowData")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{te("colWorkflowName")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{te("colOwner")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{te("colTotalRuns")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{te("colSuccessRate")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{te("colAvgDuration")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {data.map((item, i) => (
                <tr key={i} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3 font-medium text-foreground">{item.workflow_name}</td>
                  <td className="px-4 py-3 text-muted-foreground">{item.owner || "--"}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{(item.total_runs ?? 0).toLocaleString()}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="h-2 w-16 rounded-full bg-muted overflow-hidden">
                        <div
                          className="h-full rounded-full bg-green-500"
                          style={{ width: `${item.success_rate ?? 0}%` }}
                        />
                      </div>
                      <span className="text-xs tabular-nums">{(item.success_rate ?? 0).toFixed(1)}%</span>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">{Math.round(item.avg_duration_ms ?? 0)}ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Cost Projection Section
// ---------------------------------------------------------------------------

function CostProjectionSection({
  te,
  tError,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  te: (key: string, args?: any) => string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  tError: (key: string, args?: any) => string
}) {
  const [data, setData] = useState<AdminCostProjection | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    adminApi.getCostProjection()
      .then(setData)
      .catch((err) => toast.error(getErrorMessage(err, tError)))
      .finally(() => setIsLoading(false))
  }, [tError])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!data || (data.projected_tokens === 0 && data.daily_avg === 0)) {
    return (
      <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
        {te("noCostData")}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-sm font-semibold">{te("costProjectionTitle")}</h3>
        <p className="text-xs text-muted-foreground">{te("costProjectionSubtitle")}</p>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="rounded-md border border-border bg-muted/30 p-4">
          <p className="text-xs font-medium text-muted-foreground">{te("projectedTokens")}</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{formatTokens(data.projected_tokens ?? 0)}</p>
        </div>
        <div className="rounded-md border border-border bg-muted/30 p-4">
          <p className="text-xs font-medium text-muted-foreground">{te("dailyAverage")}</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{formatTokens(data.daily_avg ?? 0)}</p>
        </div>
        <div className="rounded-md border border-border bg-muted/30 p-4">
          <p className="text-xs font-medium text-muted-foreground">{te("trailingTotal")}</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{formatTokens(data.trailing_total ?? 0)}</p>
        </div>
      </div>
    </div>
  )
}

