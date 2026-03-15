"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import { Loader2, BarChart3 } from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { apiFetch } from "@/lib/api"

interface DailyUsage {
  date: string
  tokens: number
}

interface AgentUsage {
  agent_name: string
  tokens: number
}

interface UsageData {
  total_tokens: number
  quota: number | null
  quota_used_pct: number | null
  daily: DailyUsage[]
  by_agent: AgentUsage[]
}

const PERIOD_OPTIONS = ["7d", "30d", "90d"] as const
type Period = (typeof PERIOD_OPTIONS)[number]

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return n.toLocaleString()
}

export function UsageSettings() {
  const t = useTranslations("settings.usage")
  const [period, setPeriod] = useState<Period>("7d")
  const [data, setData] = useState<UsageData | null>(null)
  const [loading, setLoading] = useState(true)

  const loadUsage = useCallback(async () => {
    setLoading(true)
    try {
      const result = await apiFetch<UsageData>(`/api/me/usage?period=${period}`)
      setData(result)
    } catch {
      toast.error(t("loadFailed"))
    } finally {
      setLoading(false)
    }
  }, [period, t])

  useEffect(() => {
    loadUsage()
  }, [loadUsage])

  const periodLabelKey: Record<Period, string> = {
    "7d": "period7d",
    "30d": "period30d",
    "90d": "period90d",
  }

  // Calculate max daily tokens for bar chart scaling
  const maxDaily = data?.daily?.length ? Math.max(...data.daily.map((d) => d.tokens ?? 0), 1) : 1

  return (
    <div className="space-y-6">
      {/* Header + Period Selector */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-sm text-muted-foreground">{t("description")}</p>
        </div>
        <div className="flex items-center gap-1 rounded-md border border-border p-0.5">
          {PERIOD_OPTIONS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={cn(
                "rounded px-3 py-1 text-xs font-medium transition-colors",
                period === p
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent/50",
              )}
            >
              {t(periodLabelKey[p])}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : !data ? (
        <div className="rounded-md border border-border bg-muted/30 p-8 text-center">
          <BarChart3 className="mx-auto h-8 w-8 text-muted-foreground/50 mb-2" />
          <p className="text-sm text-muted-foreground">{t("noUsageData")}</p>
        </div>
      ) : (
        <>
          {/* Summary Cards */}
          <div className="grid grid-cols-3 gap-4">
            <div className="rounded-md border border-border p-4 space-y-1">
              <p className="text-xs font-medium text-muted-foreground">{t("totalTokens")}</p>
              <p className="text-2xl font-bold tabular-nums">{formatNumber(data.total_tokens ?? 0)}</p>
            </div>
            <div className="rounded-md border border-border p-4 space-y-1">
              <p className="text-xs font-medium text-muted-foreground">{t("monthlyQuota")}</p>
              <p className="text-2xl font-bold tabular-nums">
                {data.quota !== null ? formatNumber(data.quota) : t("unlimited")}
              </p>
            </div>
            <div className="rounded-md border border-border p-4 space-y-1">
              <p className="text-xs font-medium text-muted-foreground">{t("quotaUsed")}</p>
              {data.quota_used_pct != null ? (
                <div className="space-y-1.5">
                  <p className="text-2xl font-bold tabular-nums">{(data.quota_used_pct ?? 0).toFixed(1)}%</p>
                  <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                    <div
                      className={cn(
                        "h-full rounded-full transition-all",
                        data.quota_used_pct > 90
                          ? "bg-destructive"
                          : data.quota_used_pct > 70
                            ? "bg-amber-500"
                            : "bg-primary",
                      )}
                      style={{ width: `${Math.min(data.quota_used_pct, 100)}%` }}
                    />
                  </div>
                </div>
              ) : (
                <p className="text-2xl font-bold">{t("notApplicable")}</p>
              )}
            </div>
          </div>

          {/* Daily Usage */}
          {data.daily && data.daily.length > 0 && (
            <div className="space-y-3">
              <h3 className="text-sm font-medium">{t("dailyUsageTitle")}</h3>
              <div className="rounded-md border border-border overflow-x-auto">
                <table className="w-full min-w-max text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/40">
                      <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("dailyDate")}</th>
                      <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("dailyTokens")}</th>
                      <th className="px-4 py-2.5 text-left font-medium text-muted-foreground w-1/2"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {data.daily.map((d) => (
                      <tr key={d.date} className="hover:bg-muted/20 transition-colors">
                        <td className="px-4 py-2 text-muted-foreground text-xs">{d.date}</td>
                        <td className="px-4 py-2 tabular-nums text-foreground">{formatNumber(d.tokens ?? 0)}</td>
                        <td className="px-4 py-2">
                          <div className="h-3 w-full rounded-full bg-muted overflow-hidden">
                            <div
                              className="h-full rounded-full bg-primary/60 transition-all"
                              style={{ width: `${((d.tokens ?? 0) / maxDaily) * 100}%` }}
                            />
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Usage by Agent */}
          {data.by_agent && data.by_agent.length > 0 && (
            <div className="space-y-3">
              <h3 className="text-sm font-medium">{t("byAgentTitle")}</h3>
              <div className="rounded-md border border-border overflow-x-auto">
                <table className="w-full min-w-max text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/40">
                      <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("agentName")}</th>
                      <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("agentTokens")}</th>
                      <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("agentPercent")}</th>
                      <th className="px-4 py-2.5 text-left font-medium text-muted-foreground w-1/4"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {data.by_agent.map((a) => {
                      const pct = data.total_tokens > 0 ? ((a.tokens ?? 0) / data.total_tokens) * 100 : 0
                      return (
                        <tr key={a.agent_name} className="hover:bg-muted/20 transition-colors">
                          <td className="px-4 py-2 font-medium text-foreground">{a.agent_name}</td>
                          <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">{formatNumber(a.tokens ?? 0)}</td>
                          <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">{pct.toFixed(1)}%</td>
                          <td className="px-4 py-2">
                            <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                              <div
                                className="h-full rounded-full bg-primary/60 transition-all"
                                style={{ width: `${Math.min(pct, 100)}%` }}
                              />
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
