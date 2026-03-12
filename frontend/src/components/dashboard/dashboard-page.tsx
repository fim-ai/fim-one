"use client"

import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { useTranslations, useLocale } from "next-intl"
import { Loader2, MessageSquare, Bot, Database, Plug, TrendingUp, TrendingDown, Minus, ArrowRight, Activity, Library, Zap, Clock } from "lucide-react"
import { formatDistanceToNow } from "date-fns"
import { zhCN, enUS } from "date-fns/locale"
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { useAuth } from "@/contexts/auth-context"
import { UserAvatar as SharedUserAvatar } from "@/components/shared/user-avatar"
import { dashboardApi, type DashboardStats } from "@/lib/api"
import { formatTokens, cn } from "@/lib/utils"

// Reuse chart colors from admin-overview
const CHART_COLORS = [
  "hsl(217, 91%, 60%)",
  "hsl(217, 91%, 72%)",
  "hsl(217, 91%, 50%)",
  "hsl(199, 89%, 60%)",
  "hsl(245, 75%, 65%)",
]

const TICK_STYLE = { fill: "currentColor", fontSize: 11 } as const

// ---- Helper: format date string "YYYY-MM-DD" → abbreviated label ----
function formatDateLabel(dateStr: string, locale?: string): string {
  try {
    const d = new Date(dateStr)
    return d.toLocaleDateString(locale, { month: "short", day: "numeric" })
  } catch {
    return dateStr
  }
}

// ---- Helper: relative time ----
function relativeTime(dateStr: string, locale: string): string {
  try {
    const date = new Date(dateStr)
    const dateFnsLocale = locale.startsWith("zh") ? zhCN : enUS
    return formatDistanceToNow(date, { addSuffix: true, locale: dateFnsLocale })
  } catch {
    return dateStr
  }
}

// ---- Helper: today formatted string ----
function formatToday(locale: string): string {
  try {
    return new Date().toLocaleDateString(locale, {
      weekday: "long",
      year: "numeric",
      month: "long",
      day: "numeric",
    })
  } catch {
    return new Date().toLocaleDateString()
  }
}

// ---- Skeleton card for loading state ----
function DashboardSkeletonCard() {
  return (
    <Card>
      <CardContent className="p-6 space-y-3 animate-pulse">
        <div className="h-4 w-28 rounded bg-muted" />
        <div className="h-8 w-20 rounded bg-muted" />
        <div className="h-3 w-24 rounded bg-muted" />
      </CardContent>
    </Card>
  )
}

// ---- Bar chart tooltip ----
function ActivityTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean
  payload?: { name: string; value: number }[]
  label?: string
}) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-1.5 text-xs text-popover-foreground shadow-md">
      <p className="mb-0.5 font-medium">{label}</p>
      {payload.map((p, i) => (
        <p key={i} className="text-muted-foreground">
          {p.name}: <span className="font-medium text-popover-foreground">{p.value}</span>
        </p>
      ))}
    </div>
  )
}

// ---- Trend badge ----
function TrendBadge({ value, t }: { value: number; t: ReturnType<typeof useTranslations> }) {
  if (value === 0) {
    return (
      <span className="flex items-center gap-1 text-xs text-muted-foreground">
        <Minus className="h-3 w-3" />
        {t("trendNeutral")}
      </span>
    )
  }
  if (value > 0) {
    return (
      <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
        <TrendingUp className="h-3 w-3" />
        {t("trendUp", { value: Math.abs(value).toFixed(1) })}
      </span>
    )
  }
  return (
    <span className="flex items-center gap-1 text-xs text-red-500 dark:text-red-400">
      <TrendingDown className="h-3 w-3" />
      {t("trendDown", { value: `-${Math.abs(value).toFixed(1)}` })}
    </span>
  )
}

// ---- Status dot for connector health ----
function StatusDot({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "inline-block h-2 w-2 rounded-full shrink-0",
        status === "active" && "bg-emerald-500",
        status === "inactive" && "bg-amber-400",
        status === "error" && "bg-red-500",
        status !== "active" && status !== "inactive" && status !== "error" && "bg-muted-foreground",
      )}
    />
  )
}

// ---- Agent icon circle ----
function AgentIcon({ icon, name }: { icon: string | null; name: string }) {
  if (icon && /^\p{Emoji}/u.test(icon)) {
    return (
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent text-base">
        {icon}
      </span>
    )
  }
  const initials = name.slice(0, 1).toUpperCase()
  const hue = (name.charCodeAt(0) * 37) % 360
  return (
    <span
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-semibold text-white"
      style={{ background: `hsl(${hue}, 55%, 52%)` }}
    >
      {initials}
    </span>
  )
}


// ============================================================
// Main component
// ============================================================

export function DashboardPage() {
  const t = useTranslations("dashboard")
  const locale = useLocale()
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()

  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  // Fetch dashboard stats
  useEffect(() => {
    if (!user) return
    setLoading(true)
    dashboardApi
      .stats()
      .then((data) => setStats(data))
      .catch((err) => setError(err instanceof Error ? err.message : t("error")))
      .finally(() => setLoading(false))
  }, [user]) // eslint-disable-line react-hooks/exhaustive-deps

  // While auth is resolving or user is not available
  if (authLoading || !user) return null

  const displayName = user.display_name ?? user.username ?? user.email ?? "User"
  const todayStr = formatToday(locale)

  // Prepare activity chart data
  const activityData =
    stats?.activity_trend.map((d) => ({
      ...d,
      label: formatDateLabel(d.date, locale),
    })) ?? []
  const allZero = activityData.every((d) => d.count === 0)

  // ---- Render: error state ----
  if (error) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <Card className="w-full max-w-sm">
          <CardContent className="p-6 text-center space-y-4">
            <p className="text-sm text-destructive">{error}</p>
            <Button
              variant="outline"
              onClick={() => {
                setError(null)
                setLoading(true)
                dashboardApi
                  .stats()
                  .then((data) => setStats(data))
                  .catch((err) => setError(err instanceof Error ? err.message : t("error")))
                  .finally(() => setLoading(false))
              }}
            >
              {t("retry")}
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="flex flex-1 flex-col overflow-y-auto">
      <div className="w-full space-y-6 p-6">

        {/* ---- 1. Welcome Banner ---- */}
        <div className="rounded-xl bg-gradient-to-br from-primary/10 via-background to-background border border-border px-6 py-8">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-4">
              <SharedUserAvatar
                avatar={user.avatar ?? null}
                fallback={displayName.charAt(0).toUpperCase()}
                userId={user.id}
                className="h-12 w-12"
                iconClassName="h-6 w-6"
              />
              <div>
                <h1 className="text-xl font-semibold text-foreground">
                  {t("welcomeTitle", { name: displayName })}
                </h1>
                <p className="text-sm text-muted-foreground mt-0.5">{t("today", { date: todayStr })}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button asChild>
                <Link href="/new">
                  <MessageSquare className="mr-2 h-4 w-4" />
                  {t("newChat")}
                </Link>
              </Button>
              <Button variant="outline" asChild>
                <Link href="/agents/new">
                  <Bot className="mr-2 h-4 w-4" />
                  {t("newAgent")}
                </Link>
              </Button>
            </div>
          </div>
        </div>

        {/* ---- 2. Stats Row ---- */}
        {loading ? (
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <DashboardSkeletonCard key={i} />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            {/* Conversations */}
            <Card>
              <CardContent className="p-6 space-y-2">
                <div className="flex items-center gap-2 text-muted-foreground">
                  <MessageSquare className="h-4 w-4" />
                  <span className="text-xs font-medium uppercase tracking-wide">
                    {t("statsConversations")}
                  </span>
                </div>
                <p className="text-2xl font-semibold text-foreground">
                  {(stats?.total_conversations ?? 0).toLocaleString()}
                </p>
                {stats && (
                  <TrendBadge value={stats.conversations_week_trend} t={t} />
                )}
              </CardContent>
            </Card>

            {/* Agents */}
            <Card>
              <CardContent className="p-6 space-y-2">
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Bot className="h-4 w-4" />
                  <span className="text-xs font-medium uppercase tracking-wide">
                    {t("statsAgents")}
                  </span>
                </div>
                <p className="text-2xl font-semibold text-foreground">
                  {(stats?.total_agents ?? 0).toLocaleString()}
                </p>
              </CardContent>
            </Card>

            {/* Tokens */}
            <Card>
              <CardContent className="p-6 space-y-2">
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Database className="h-4 w-4" />
                  <span className="text-xs font-medium uppercase tracking-wide">
                    {t("statsTokens")}
                  </span>
                </div>
                <p className="text-2xl font-semibold text-foreground">
                  {formatTokens(stats?.total_tokens ?? 0)}
                </p>
                {stats && (
                  <TrendBadge value={stats.tokens_week_trend} t={t} />
                )}
              </CardContent>
            </Card>

            {/* Active Connectors */}
            <Card>
              <CardContent className="p-6 space-y-2">
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Plug className="h-4 w-4" />
                  <span className="text-xs font-medium uppercase tracking-wide">
                    {t("statsConnectors")}
                  </span>
                </div>
                <p className="text-2xl font-semibold text-foreground">
                  {(stats?.active_connectors ?? 0).toLocaleString()}
                </p>
              </CardContent>
            </Card>
          </div>
        )}

        {/* ---- 3. Activity Trend Chart ---- */}
        {loading ? (
          <div className="h-[260px] rounded-xl bg-muted animate-pulse" />
        ) : (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base font-medium">
                <Activity className="h-4 w-4 text-muted-foreground" />
                {t("activityTitle")}
              </CardTitle>
              <p className="text-sm text-muted-foreground">{t("activitySubtitle")}</p>
            </CardHeader>
            <CardContent>
              {allZero || activityData.length === 0 ? (
                <div className="flex h-[200px] items-center justify-center text-sm text-muted-foreground">
                  {t("activityEmpty")}
                </div>
              ) : (
                <div className="h-[200px] text-muted-foreground">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={activityData}
                      margin={{ top: 4, right: 4, left: 0, bottom: 4 }}
                    >
                      <CartesianGrid
                        strokeDasharray="3 3"
                        className="stroke-border"
                        vertical={false}
                      />
                      <XAxis
                        dataKey="label"
                        tick={TICK_STYLE}
                        tickLine={false}
                        axisLine={false}
                      />
                      <YAxis
                        width={28}
                        tick={TICK_STYLE}
                        tickLine={false}
                        axisLine={false}
                        allowDecimals={false}
                      />
                      <Tooltip
                        content={<ActivityTooltip />}
                        cursor={{ fill: "rgba(128,128,128,0.1)" }}
                      />
                      <Bar
                        dataKey="count"
                        name={t("statsConversations")}
                        fill={CHART_COLORS[0]}
                        radius={[4, 4, 0, 0]}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* ---- 4 + 5. Two two-column grids ---- */}
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <>
            {/* Row A: Recent Conversations + My Agents */}
            <div className="grid grid-cols-1 gap-6 md:grid-cols-2">

              {/* Recent Conversations */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2 text-base font-medium">
                    <Clock className="h-4 w-4 text-muted-foreground" />
                    {t("recentTitle")}
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                  {!stats?.recent_conversations.length ? (
                    <div className="flex flex-col items-center gap-3 px-6 py-8 text-sm text-muted-foreground">
                      <p>{t("recentEmpty")}</p>
                      <Button variant="outline" size="sm" asChild>
                        <Link href="/new">{t("recentEmptyCta")}</Link>
                      </Button>
                    </div>
                  ) : (
                    <ul className="divide-y divide-border">
                      {stats.recent_conversations.slice(0, 6).map((conv) => (
                        <li key={conv.id} className="flex items-center gap-3 px-4 py-3">
                          <div className="flex-1 min-w-0">
                            <p className="truncate text-sm font-medium text-foreground">
                              {conv.title || t("untitled")}
                            </p>
                            <div className="flex items-center gap-2 mt-0.5">
                              {conv.agent_name && (
                                <Badge
                                  variant="secondary"
                                  className="h-4 px-1.5 text-[10px] font-normal"
                                >
                                  {conv.agent_name}
                                </Badge>
                              )}
                              <span className="text-xs text-muted-foreground">
                                {relativeTime(conv.updated_at ?? conv.created_at, locale)}
                              </span>
                            </div>
                          </div>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="shrink-0 h-7 gap-1 text-xs"
                            asChild
                          >
                            <Link href={`/?c=${conv.id}`}>
                              {t("recentResume")}
                              <ArrowRight className="h-3 w-3" />
                            </Link>
                          </Button>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>

              {/* My Agents */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2 text-base font-medium">
                    <Bot className="h-4 w-4 text-muted-foreground" />
                    {t("agentsTitle")}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {!stats?.top_agents.length ? (
                    <div className="flex flex-col items-center gap-3 py-6 text-sm text-muted-foreground">
                      <p>{t("agentsEmpty")}</p>
                      <Button variant="outline" size="sm" asChild>
                        <Link href="/agents/new">{t("agentsCreate")}</Link>
                      </Button>
                    </div>
                  ) : (
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      {stats.top_agents.slice(0, 4).map((agent) => (
                        <Link
                          key={agent.id}
                          href={`/agents/${agent.id}`}
                          className="flex items-start gap-3 rounded-lg border border-border p-3 transition-colors hover:bg-accent/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                        >
                          <AgentIcon icon={agent.icon} name={agent.name} />
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-medium text-foreground">
                              {agent.name}
                            </p>
                            {agent.description && (
                              <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                                {agent.description}
                              </p>
                            )}
                            <Badge
                              variant="secondary"
                              className="mt-1.5 h-4 px-1.5 text-[10px] font-normal"
                            >
                              {t("agentsConvCount", { count: agent.conversation_count })}
                            </Badge>
                          </div>
                        </Link>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>

            {/* Row B: Knowledge Bases + Connector Health */}
            <div className="grid grid-cols-1 gap-6 md:grid-cols-2">

              {/* Knowledge Bases */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2 text-base font-medium">
                    <Library className="h-4 w-4 text-muted-foreground" />
                    {t("kbTitle")}
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                  {!stats?.top_kbs.length ? (
                    <div className="px-6 py-8 text-center text-sm text-muted-foreground">
                      {t("kbEmpty")}
                    </div>
                  ) : (
                    <ul className="divide-y divide-border">
                      {stats.top_kbs.slice(0, 3).map((kb) => (
                        <li key={kb.id}>
                          <Link
                            href={`/kb/${kb.id}`}
                            className="flex items-center justify-between px-4 py-3 transition-colors hover:bg-accent/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary rounded-sm"
                          >
                            <div className="flex items-center gap-2 min-w-0">
                              <Database className="h-4 w-4 shrink-0 text-muted-foreground" />
                              <span className="truncate text-sm font-medium text-foreground">
                                {kb.name}
                              </span>
                            </div>
                            <div className="flex items-center gap-2 shrink-0 ml-2">
                              <Badge variant="secondary" className="text-xs font-normal">
                                {t("kbDocs", { count: kb.document_count })}
                              </Badge>
                              <Badge variant="outline" className="text-xs font-normal">
                                {t("kbChunks", { count: kb.total_chunks })}
                              </Badge>
                            </div>
                          </Link>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>

              {/* Connector Health */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2 text-base font-medium">
                    <Zap className="h-4 w-4 text-muted-foreground" />
                    {t("connectorsTitle")}
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                  {!stats?.connector_health.length ? (
                    <div className="px-6 py-8 text-center text-sm text-muted-foreground">
                      {t("connectorsEmpty")}
                    </div>
                  ) : (
                    <ul className="divide-y divide-border">
                      {stats.connector_health.map((connector) => (
                        <li
                          key={connector.id}
                          className="flex items-center gap-3 px-4 py-3"
                        >
                          <Plug className="h-4 w-4 shrink-0 text-muted-foreground" />
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-medium text-foreground">
                              {connector.name}
                            </p>
                            <Badge
                              variant="outline"
                              className="mt-0.5 h-4 px-1.5 text-[10px] font-normal"
                            >
                              {connector.type}
                            </Badge>
                          </div>
                          <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                            {t("connectorCallsToday", { count: connector.call_count_today })}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
