"use client"

import { useState, useEffect } from "react"
import { useTranslations } from "next-intl"
import { Activity, ArrowUpRight, CheckCircle, Clock, Plug } from "lucide-react"
import { Separator } from "@/components/ui/separator"
import { adminApi } from "@/lib/api"
import type { ConnectorStats } from "@/lib/api"
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts"

const CHART_COLORS = [
  "hsl(217, 91%, 60%)",
  "hsl(217, 91%, 72%)",
  "hsl(217, 91%, 50%)",
  "hsl(199, 89%, 60%)",
  "hsl(245, 75%, 65%)",
]

const TICK_STYLE = { fill: "currentColor", fontSize: 11 } as const

function BarTooltip({ active, payload, label }: { active?: boolean; payload?: { name: string; value: number }[]; label?: string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-1.5 text-xs text-popover-foreground shadow-md">
      <p className="mb-0.5 font-medium">{label}</p>
      {payload.map((p, i) => (
        <p key={i} className="text-muted-foreground">{p.name}: <span className="text-popover-foreground font-medium">{p.value.toLocaleString()}</span></p>
      ))}
    </div>
  )
}

function StatCard({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ElementType
  label: string
  value: string | number
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-2">
      <div className="flex items-center gap-2 text-muted-foreground">
        <Icon className="h-4 w-4" />
        <span className="text-xs font-medium uppercase tracking-wide">{label}</span>
      </div>
      <p className="text-2xl font-semibold text-foreground">{value}</p>
    </div>
  )
}

function SkeletonCard() {
  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-2 animate-pulse">
      <div className="h-4 w-24 rounded bg-muted" />
      <div className="h-8 w-16 rounded bg-muted" />
    </div>
  )
}

function formatDate(dateStr: string): string {
  try {
    const d = new Date(dateStr)
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
  } catch {
    return dateStr
  }
}

export function AdminConnectors() {
  const t = useTranslations("admin.connectors")
  const [stats, setStats] = useState<ConnectorStats | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    adminApi.connectorStats()
      .then((data) => setStats(data))
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load connector stats"))
      .finally(() => setIsLoading(false))
  }, [])

  if (error) {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
        {error}
      </div>
    )
  }

  const recentDays = stats
    ? stats.recent_days.map((d) => ({ ...d, label: formatDate(d.date) }))
    : []

  const topConnectors = stats
    ? stats.top_connectors.map((c) => ({
        name: c.connector_name,
        calls: c.call_count,
      }))
    : []

  return (
    <div className="space-y-8">
      {/* Page header */}
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {/* Section 1 -- Connector Stats */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">{t("connectorStats")}</h3>
          <p className="text-sm text-muted-foreground">{t("connectorStatsDesc")}</p>
        </div>

        {isLoading ? (
          <div className="grid grid-cols-2 gap-4">
            {Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4">
            <StatCard icon={Activity} label={t("totalCalls")} value={(stats?.total_calls ?? 0).toLocaleString()} />
            <StatCard icon={ArrowUpRight} label={t("todayCalls")} value={(stats?.today_calls ?? 0).toLocaleString()} />
            <StatCard
              icon={CheckCircle}
              label={t("successRate")}
              value={`${((stats?.success_rate ?? 0) * 100).toFixed(1)}%`}
            />
            <StatCard
              icon={Clock}
              label={t("avgResponseTime")}
              value={`${Math.round(stats?.avg_response_time_ms ?? 0)}ms`}
            />
          </div>
        )}
      </div>

      <Separator />

      {/* Section 2 -- Top Connectors (horizontal bar chart) */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">{t("topConnectors")}</h3>
          <p className="text-sm text-muted-foreground">{t("topConnectorsDesc")}</p>
        </div>

        {isLoading ? (
          <div className="h-[200px] rounded bg-muted animate-pulse" />
        ) : topConnectors.length === 0 ? (
          <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
            {t("noConnectorUsage")}
          </div>
        ) : (
          <div className="text-muted-foreground" style={{ height: Math.max(180, topConnectors.length * 36) }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={topConnectors} layout="vertical" margin={{ top: 4, right: 4, left: 0, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-border" horizontal={false} />
                <XAxis type="number" tick={TICK_STYLE} tickLine={false} axisLine={false} allowDecimals={false} />
                <YAxis type="category" dataKey="name" width={120} tick={TICK_STYLE} tickLine={false} axisLine={false} />
                <Tooltip content={<BarTooltip />} cursor={{ fill: "rgba(128,128,128,0.1)" }} />
                <Bar dataKey="calls" name={t("chartCalls")} fill={CHART_COLORS[0]} radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      <Separator />

      {/* Section 3 -- Top Actions */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">{t("topActions")}</h3>
          <p className="text-sm text-muted-foreground">{t("topActionsDesc")}</p>
        </div>

        {isLoading ? (
          <div className="space-y-2 animate-pulse">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-10 rounded bg-muted" />
            ))}
          </div>
        ) : !stats?.top_actions?.length ? (
          <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
            {t("noActionUsage")}
          </div>
        ) : (
          <div className="divide-y divide-border rounded-md border border-border">
            {stats.top_actions.map((action, i) => (
              <div key={i} className="flex items-center justify-between px-4 py-2.5">
                <div className="flex items-center gap-2">
                  <Plug className="h-4 w-4 text-muted-foreground shrink-0" />
                  <span className="text-sm text-foreground">
                    <span className="text-muted-foreground">{action.connector_name}</span>
                    {" / "}
                    <span className="font-medium">{action.action_name}</span>
                  </span>
                </div>
                <span className="text-sm text-muted-foreground tabular-nums">
                  {t("callCount", { count: action.call_count.toLocaleString() })}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <Separator />

      {/* Section 4 -- 14-day Call Trend */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">{t("callTrend")}</h3>
          <p className="text-sm text-muted-foreground">{t("callTrendDesc")}</p>
        </div>

        {isLoading ? (
          <div className="h-[180px] rounded bg-muted animate-pulse" />
        ) : recentDays.length === 0 ? (
          <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
            {t("noRecentActivity")}
          </div>
        ) : (
          <div className="h-[180px] text-muted-foreground">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={recentDays} margin={{ top: 4, right: 4, left: 0, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-border" vertical={false} />
                <XAxis dataKey="label" tick={TICK_STYLE} tickLine={false} axisLine={false} />
                <YAxis width={28} tick={TICK_STYLE} tickLine={false} axisLine={false} allowDecimals={false} />
                <Tooltip content={<BarTooltip />} cursor={{ fill: "rgba(128,128,128,0.1)" }} />
                <Bar dataKey="count" name={t("chartCalls")} fill={CHART_COLORS[3]} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  )
}
