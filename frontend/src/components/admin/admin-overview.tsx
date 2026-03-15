"use client"

import { useState, useEffect } from "react"
import { useTranslations, useLocale } from "next-intl"
import { Users, MessageSquare, Zap, Bot, Database, BookOpen, FileText, Hash, Plug, Package, AlertCircle } from "lucide-react"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { apiFetch } from "@/lib/api"
import { formatTokens } from "@/lib/utils"
import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  Legend,
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
} from "recharts"

interface AdminStats {
  total_users: number
  total_conversations: number
  total_messages: number
  total_tokens: number
  total_fast_llm_tokens: number
  total_agents: number
  total_kbs: number
  total_documents: number
  total_chunks: number
  total_connectors: number
  today_conversations: number
  tokens_by_agent: { agent_id: string; name: string; total_tokens: number }[]
  conversations_by_model: { model: string; count: number }[]
  tokens_by_model: { model: string; count: number }[]
  top_agents: { agent_id: string; name: string; count: number }[]
  recent_days: { date: string; count: number }[]
}

const CHART_COLORS = [
  "hsl(40, 50%, 52%)",
  "hsl(40, 40%, 62%)",
  "hsl(38, 55%, 42%)",
  "hsl(35, 45%, 56%)",
  "hsl(45, 30%, 48%)",
]

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

function PieTooltip({ active, payload, tokenLabel }: { active?: boolean; payload?: { name: string; value: number }[]; tokenLabel: string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-1.5 text-xs text-popover-foreground shadow-md">
      <p className="font-medium">{payload[0].name}</p>
      <p className="text-muted-foreground">{tokenLabel}: <span className="text-popover-foreground font-medium">{formatTokens(payload[0].value)}</span></p>
    </div>
  )
}

const TICK_STYLE = { fill: "currentColor", fontSize: 11 } as const

function StatCard({
  icon: Icon,
  label,
  value,
  secondary,
}: {
  icon: React.ElementType
  label: string
  value: string | number
  secondary?: string
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-2">
      <div className="flex items-center gap-2 text-muted-foreground">
        <Icon className="h-4 w-4" />
        <span className="text-xs font-medium uppercase tracking-wide">{label}</span>
      </div>
      <p className="text-2xl font-semibold text-foreground">{value}</p>
      {secondary && <p className="text-xs text-muted-foreground">{secondary}</p>}
    </div>
  )
}

function SkeletonCard() {
  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-2">
      <div className="h-4 w-24 rounded bg-muted animate-pulse" />
      <div className="h-8 w-16 rounded bg-muted animate-pulse" />
    </div>
  )
}

function formatDate(dateStr: string, locale?: string): string {
  try {
    const d = new Date(dateStr)
    return d.toLocaleDateString(locale, { month: "short", day: "numeric" })
  } catch {
    return dateStr
  }
}

export function AdminOverview() {
  const t = useTranslations("admin.overview")
  const locale = useLocale()
  const [stats, setStats] = useState<AdminStats | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [resourceOverview, setResourceOverview] = useState<{ resources: { resource_type: string; total: number; active: number; inactive: number; stale_count: number }[]; total_resources: number; total_active: number; total_inactive: number } | null>(null)

  useEffect(() => {
    apiFetch<AdminStats>("/api/admin/stats")
      .then((data) => setStats(data))
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load stats"))
      .finally(() => setIsLoading(false))
    apiFetch<typeof resourceOverview>("/api/admin/resources/overview")
      .then((data) => setResourceOverview(data))
      .catch(() => {}) // non-critical
  }, [])

  if (error) {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
        {error}
      </div>
    )
  }

  const topModels = stats
    ? [...(stats.tokens_by_model ?? stats.conversations_by_model)].sort((a, b) => b.count - a.count).slice(0, 5)
    : []

  const topAgents = stats ? stats.top_agents.slice(0, 5) : []

  const recentDays = stats
    ? stats.recent_days.map((d) => ({ ...d, label: formatDate(d.date, locale) }))
    : []

  return (
    <div className="space-y-8">
      {/* Page header */}
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {/* Section 1 — System Stats */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">{t("systemStats")}</h3>
          <p className="text-sm text-muted-foreground">{t("systemStatsDesc")}</p>
        </div>

        {isLoading ? (
          <div className="grid grid-cols-3 gap-4">
            {Array.from({ length: 9 }).map((_, i) => <SkeletonCard key={i} />)}
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-4">
            <StatCard icon={Users} label={t("totalUsers")} value={stats?.total_users ?? 0} />
            <StatCard
              icon={MessageSquare}
              label={t("conversations")}
              value={stats?.total_conversations ?? 0}
              secondary={stats?.today_conversations ? t("todayCount", { count: stats.today_conversations }) : undefined}
            />
            <StatCard icon={Database} label={t("messages")} value={(stats?.total_messages ?? 0).toLocaleString()} />
            <StatCard
              icon={Zap}
              label={t("totalTokens")}
              value={formatTokens(stats?.total_tokens ?? 0)}
              secondary={stats?.total_fast_llm_tokens ? t("fastLlmTokens", { tokens: formatTokens(stats.total_fast_llm_tokens) }) : undefined}
            />
            <StatCard icon={Bot} label={t("agents")} value={stats?.total_agents ?? 0} />
            <StatCard icon={BookOpen} label={t("knowledgeBases")} value={stats?.total_kbs ?? 0} />
            <StatCard icon={FileText} label={t("documents")} value={(stats?.total_documents ?? 0).toLocaleString()} />
            <StatCard icon={Hash} label={t("chunks")} value={(stats?.total_chunks ?? 0).toLocaleString()} />
            <StatCard icon={Plug} label={t("connectors")} value={stats?.total_connectors ?? 0} />
          </div>
        )}
      </div>

      <Separator />

      {/* Section 2 — Recent Activity (bar chart) */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">{t("recentActivity")}</h3>
          <p className="text-sm text-muted-foreground">{t("recentActivityDesc")}</p>
        </div>

        {isLoading ? (
          <div className="h-[180px] flex items-end gap-2">
            {Array.from({ length: 14 }).map((_, i) => (
              <div
                key={i}
                className="flex-1 rounded-sm bg-muted animate-pulse"
                style={{ height: `${20 + Math.sin(i * 0.9) * 40 + 40}px` }}
              />
            ))}
          </div>
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
                <Bar dataKey="count" name={t("chartConversations")} fill={CHART_COLORS[0]} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      <Separator />

      {/* Section 3 — Top Agents */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">{t("topAgents")}</h3>
          <p className="text-sm text-muted-foreground">{t("topAgentsDesc")}</p>
        </div>

        {isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-10 rounded" />
            ))}
          </div>
        ) : topAgents.length === 0 ? (
          <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
            {t("noAgentUsage")}
          </div>
        ) : (
          <div className="divide-y divide-border rounded-md border border-border">
            {topAgents.map((agent) => (
              <div key={agent.agent_id} className="flex items-center justify-between px-4 py-2.5">
                <div className="flex items-center gap-2">
                  <Bot className="h-4 w-4 text-muted-foreground shrink-0" />
                  <span className="text-sm font-medium text-foreground truncate max-w-[200px]">
                    {agent.name}
                  </span>
                </div>
                <span className="text-sm text-muted-foreground tabular-nums">
                  {t("convCount", { count: agent.count.toLocaleString() })}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <Separator />

      {/* Section 4 — Token Usage: Model (left) + Agent (right) */}
      <div className="grid grid-cols-2 gap-6">
        {/* Model token usage (donut) */}
        <div className="space-y-4">
          <div>
            <h3 className="text-base font-medium">{t("tokenUsageByModel")}</h3>
            <p className="text-sm text-muted-foreground">{t("tokenUsageByModelDesc")}</p>
          </div>

          {isLoading ? (
            <Skeleton className="h-[220px] rounded" />
          ) : topModels.length === 0 ? (
            <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
              {t("noModelUsage")}
            </div>
          ) : topModels.length === 1 ? (
            <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
              {t("allTokensByModel", { model: topModels[0].model, tokens: formatTokens(topModels[0].count) })}
            </div>
          ) : (
            <div className="h-[220px]">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={topModels}
                    dataKey="count"
                    nameKey="model"
                    cx="50%"
                    cy="50%"
                    innerRadius={55}
                    outerRadius={85}
                  >
                    {topModels.map((entry, index) => (
                      <Cell key={`cell-${entry.model}`} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip content={<PieTooltip tokenLabel={t("tokens")} />} />
                  <Legend
                    iconType="circle"
                    iconSize={8}
                    wrapperStyle={{ fontSize: "12px" }}
                    className="text-muted-foreground"
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        {/* Agent token usage (horizontal bar) */}
        <div className="space-y-4">
          <div>
            <h3 className="text-base font-medium">{t("tokensByAgent")}</h3>
            <p className="text-sm text-muted-foreground">{t("tokensByAgentDesc")}</p>
          </div>

          {isLoading ? (
            <Skeleton className="h-[220px] rounded" />
          ) : !stats?.tokens_by_agent?.length ? (
            <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
              {t("noTokenUsage")}
            </div>
          ) : (
            <div className="text-muted-foreground h-[220px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={stats.tokens_by_agent.map((a) => ({
                    name: a.name,
                    tokens: a.total_tokens,
                  }))}
                  layout="vertical"
                  margin={{ top: 4, right: 4, left: 0, bottom: 4 }}
                >
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" horizontal={false} />
                  <XAxis type="number" tick={TICK_STYLE} tickLine={false} axisLine={false} allowDecimals={false} />
                  <YAxis type="category" dataKey="name" width={120} tick={TICK_STYLE} tickLine={false} axisLine={false} />
                  <Tooltip content={<BarTooltip />} cursor={{ fill: "rgba(128,128,128,0.1)" }} />
                  <Bar dataKey="tokens" name={t("chartTokens")} fill={CHART_COLORS[2]} radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      {/* Section 5 — Resource Lifecycle */}
      {resourceOverview && resourceOverview.resources.length > 0 && (
        <>
          <Separator />
          <div className="space-y-4">
            <div>
              <h3 className="text-base font-medium">{t("resourceLifecycle")}</h3>
              <p className="text-sm text-muted-foreground">{t("resourceLifecycleDesc")}</p>
            </div>

            <div className="grid grid-cols-3 gap-4">
              <StatCard icon={Package} label={t("totalResources")} value={resourceOverview.total_resources} />
              <StatCard icon={Zap} label={t("activeResources")} value={resourceOverview.total_active} />
              <StatCard icon={AlertCircle} label={t("inactiveResources")} value={resourceOverview.total_inactive} />
            </div>

            <div className="rounded-md border border-border overflow-x-auto">
              <table className="w-full min-w-max text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/40">
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("resourceType")}</th>
                    <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("rlTotal")}</th>
                    <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("rlActive")}</th>
                    <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("rlInactive")}</th>
                    <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("rlStale")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {resourceOverview.resources.map((r) => (
                    <tr key={r.resource_type} className="hover:bg-muted/20 transition-colors">
                      <td className="px-4 py-3 font-medium text-foreground capitalize">{r.resource_type.replace("_", " ")}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{r.total}</td>
                      <td className="px-4 py-3 text-right tabular-nums text-green-600 dark:text-green-400">{r.active}</td>
                      <td className="px-4 py-3 text-right tabular-nums text-yellow-600 dark:text-yellow-400">{r.inactive}</td>
                      <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">{r.stale_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
