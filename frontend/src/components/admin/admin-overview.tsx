"use client"

import { useState, useEffect } from "react"
import { Users, MessageSquare, Zap, Bot, Database, BookOpen, FileText, Hash, Plug } from "lucide-react"
import { Separator } from "@/components/ui/separator"
import { apiFetch } from "@/lib/api"
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
  "hsl(217, 91%, 60%)",
  "hsl(217, 91%, 72%)",
  "hsl(217, 91%, 50%)",
  "hsl(199, 89%, 60%)",
  "hsl(245, 75%, 65%)",
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

function PieTooltip({ active, payload }: { active?: boolean; payload?: { name: string; value: number }[] }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-1.5 text-xs text-popover-foreground shadow-md">
      <p className="font-medium">{payload[0].name}</p>
      <p className="text-muted-foreground">tokens: <span className="text-popover-foreground font-medium">{formatTokens(payload[0].value)}</span></p>
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
    <div className="rounded-lg border border-border bg-card p-4 space-y-2 animate-pulse">
      <div className="h-4 w-24 rounded bg-muted" />
      <div className="h-8 w-16 rounded bg-muted" />
    </div>
  )
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return n.toString()
}

function formatDate(dateStr: string): string {
  try {
    const d = new Date(dateStr)
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
  } catch {
    return dateStr
  }
}

export function AdminOverview() {
  const [stats, setStats] = useState<AdminStats | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<AdminStats>("/api/admin/stats")
      .then((data) => setStats(data))
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load stats"))
      .finally(() => setIsLoading(false))
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
    ? stats.recent_days.map((d) => ({ ...d, label: formatDate(d.date) }))
    : []

  return (
    <div className="space-y-8">
      {/* Section 1 — System Stats */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">System Stats</h3>
          <p className="text-sm text-muted-foreground">High-level usage overview across all users.</p>
        </div>

        {isLoading ? (
          <div className="grid grid-cols-3 gap-4">
            {Array.from({ length: 9 }).map((_, i) => <SkeletonCard key={i} />)}
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-4">
            <StatCard icon={Users} label="Total Users" value={stats?.total_users ?? 0} />
            <StatCard
              icon={MessageSquare}
              label="Conversations"
              value={stats?.total_conversations ?? 0}
              secondary={stats?.today_conversations ? `${stats.today_conversations} today` : undefined}
            />
            <StatCard icon={Database} label="Messages" value={(stats?.total_messages ?? 0).toLocaleString()} />
            <StatCard
              icon={Zap}
              label="Total Tokens"
              value={formatTokens(stats?.total_tokens ?? 0)}
              secondary={stats?.total_fast_llm_tokens ? `Fast LLM: ${formatTokens(stats.total_fast_llm_tokens)}` : undefined}
            />
            <StatCard icon={Bot} label="Agents" value={stats?.total_agents ?? 0} />
            <StatCard icon={BookOpen} label="Knowledge Bases" value={stats?.total_kbs ?? 0} />
            <StatCard icon={FileText} label="Documents" value={(stats?.total_documents ?? 0).toLocaleString()} />
            <StatCard icon={Hash} label="Chunks" value={(stats?.total_chunks ?? 0).toLocaleString()} />
            <StatCard icon={Plug} label="Connectors" value={stats?.total_connectors ?? 0} />
          </div>
        )}
      </div>

      <Separator />

      {/* Section 2 — Recent Activity (bar chart) */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">Recent Activity</h3>
          <p className="text-sm text-muted-foreground">Daily conversation volume over the last 14 days.</p>
        </div>

        {isLoading ? (
          <div className="h-[180px] rounded bg-muted animate-pulse" />
        ) : recentDays.length === 0 ? (
          <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
            No recent activity yet.
          </div>
        ) : (
          <div className="h-[180px] text-muted-foreground">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={recentDays} margin={{ top: 4, right: 4, left: 0, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-border" vertical={false} />
                <XAxis dataKey="label" tick={TICK_STYLE} tickLine={false} axisLine={false} />
                <YAxis width={28} tick={TICK_STYLE} tickLine={false} axisLine={false} allowDecimals={false} />
                <Tooltip content={<BarTooltip />} cursor={{ fill: "rgba(128,128,128,0.1)" }} />
                <Bar dataKey="count" name="Conversations" fill={CHART_COLORS[0]} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      <Separator />

      {/* Section 3 — Top Agents */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">Top Agents</h3>
          <p className="text-sm text-muted-foreground">Most used agents by conversation count.</p>
        </div>

        {isLoading ? (
          <div className="space-y-2 animate-pulse">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-10 rounded bg-muted" />
            ))}
          </div>
        ) : topAgents.length === 0 ? (
          <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
            No agent usage data yet.
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
                  {agent.count.toLocaleString()} conv.
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <Separator />

      {/* Section 4 — Model Usage (donut chart) */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">Token Usage by Model</h3>
          <p className="text-sm text-muted-foreground">Token consumption distribution across LLM models.</p>
        </div>

        {isLoading ? (
          <div className="h-[220px] rounded bg-muted animate-pulse" />
        ) : topModels.length === 0 ? (
          <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
            No model usage data yet.
          </div>
        ) : topModels.length === 1 ? (
          <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
            All tokens consumed by <span className="font-medium text-foreground">{topModels[0].model}</span> ({formatTokens(topModels[0].count)})
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
                <Tooltip content={<PieTooltip />} />
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

      <Separator />

      {/* Section 5 -- Tokens by Agent */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">Tokens by Agent</h3>
          <p className="text-sm text-muted-foreground">Top agents by total token consumption.</p>
        </div>

        {isLoading ? (
          <div className="h-[200px] rounded bg-muted animate-pulse" />
        ) : !stats?.tokens_by_agent?.length ? (
          <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
            No token usage data yet.
          </div>
        ) : (
          <div className="text-muted-foreground" style={{ height: Math.max(180, stats.tokens_by_agent.length * 36) }}>
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
                <Bar dataKey="tokens" name="Tokens" fill={CHART_COLORS[2]} radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  )
}
