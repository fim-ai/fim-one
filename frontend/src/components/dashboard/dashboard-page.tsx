"use client"

import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { useTranslations, useLocale } from "next-intl"
import { MessageSquare, Bot, Database, Plug, TrendingUp, TrendingDown, Minus, Activity, Library, Clock, ChevronRight, ShoppingBag } from "lucide-react"
import { formatDistanceToNow } from "date-fns"
import { zhCN, enUS } from "date-fns/locale"
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { useAuth } from "@/contexts/auth-context"
import { useConversation } from "@/contexts/conversation-context"
import { UserAvatar as SharedUserAvatar } from "@/components/shared/user-avatar"
import { dashboardApi, marketApi, type DashboardStats, type MarketItem } from "@/lib/api"
import { formatTokens } from "@/lib/utils"

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

// ---- Area chart tooltip (token usage) ----
function TokenTooltip({
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
          {p.name}: <span className="font-medium text-popover-foreground">{formatTokens(p.value)}</span>
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

// ---- Agent icon circle ----
function AgentIcon({ icon, name }: { icon: string | null; name: string }) {
  if (icon && /^\p{Emoji}/u.test(icon)) {
    return (
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted text-base">
        {icon}
      </span>
    )
  }
  const initials = name.slice(0, 1).toUpperCase()
  const hue = (name.charCodeAt(0) * 37) % 360
  return (
    <span
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-sm font-semibold text-white"
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
  const { conversations } = useConversation()
  const router = useRouter()

  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [marketItems, setMarketItems] = useState<MarketItem[]>([])

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  // Fetch dashboard stats + market items
  useEffect(() => {
    if (!user) return
    setLoading(true)
    dashboardApi
      .stats()
      .then((data) => setStats(data))
      .catch((err) => setError(err instanceof Error ? err.message : t("error")))
      .finally(() => setLoading(false))
    marketApi.browse({ size: 4 }).then((res) => setMarketItems(res.items)).catch(() => {})
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
  const allTokensZero = activityData.every((d) => d.tokens === 0)

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

  // Stat card config
  const statCards = [
    {
      Icon: MessageSquare,
      label: t("statsConversations"),
      value: (stats?.total_conversations ?? 0).toLocaleString(),
      trend: stats ? <TrendBadge value={stats.conversations_week_trend} t={t} /> : null,
      href: "/chats" as string | undefined,
    },
    {
      Icon: Bot,
      label: t("statsAgents"),
      value: (stats?.total_agents ?? 0).toLocaleString(),
      trend: stats ? (
        <span className="text-xs text-muted-foreground">
          {stats.agent_conversations_today > 0
            ? t("agentChatsToday", { count: stats.agent_conversations_today })
            : t("agentChatsNoneToday")}
        </span>
      ) : null,
      href: "/agents" as string | undefined,
    },
    {
      Icon: Database,
      label: t("statsTokens"),
      value: formatTokens(stats?.total_tokens ?? 0),
      trend: stats ? <TrendBadge value={stats.tokens_week_trend} t={t} /> : null,
      href: undefined as string | undefined,
    },
    {
      Icon: Plug,
      label: t("statsConnectors"),
      value: (stats?.active_connectors ?? 0).toLocaleString(),
      trend: stats ? (
        <span className="text-xs text-muted-foreground">
          {stats.connector_calls_today > 0
            ? t("connectorCallsTodayTotal", { count: stats.connector_calls_today })
            : t("connectorCallsNoneToday")}
        </span>
      ) : null,
      href: "/connectors" as string | undefined,
    },
  ]

  return (
    <div className="flex flex-1 flex-col overflow-y-auto">
      <div className="w-full space-y-4 p-6">

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

        {/* ---- 2. Stats Row (watermark icon design) ---- */}
        {loading ? (
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton.StatCard key={i} />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            {statCards.map((card, i) => {
              const cardContent = (
                <>
                  <CardContent className="px-5 space-y-1.5 relative z-10">
                    <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      {card.label}
                    </span>
                    <p className="text-2xl font-semibold text-foreground">{card.value}</p>
                    {card.trend}
                  </CardContent>
                  {/* Watermark icon -- large, faded, bottom-right */}
                  <card.Icon className="absolute -bottom-3 -right-3 h-20 w-20 text-muted-foreground/[0.06] pointer-events-none" />
                </>
              )
              return card.href ? (
                <Link key={i} href={card.href} className="rounded-xl focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">
                  <Card className="overflow-hidden relative py-3 gap-0 transition-colors hover:bg-accent/30 cursor-pointer h-full">
                    {cardContent}
                  </Card>
                </Link>
              ) : (
                <Card key={i} className="overflow-hidden relative py-3 gap-0">
                  {cardContent}
                </Card>
              )
            })}
          </div>
        )}

        {/* ---- 3. Activity + Token Trend Charts ---- */}
        {loading ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Card className="gap-1 py-3">
              <CardHeader className="px-5">
                <Skeleton className="h-5 w-36" />
                <Skeleton className="h-4 w-52 mt-1" />
              </CardHeader>
              <CardContent className="px-5 pb-1 pt-4">
                <Skeleton.ChartBars height={200} />
              </CardContent>
            </Card>
            <Card className="gap-1 py-3">
              <CardHeader className="px-5">
                <Skeleton className="h-5 w-32" />
                <Skeleton className="h-4 w-56 mt-1" />
              </CardHeader>
              <CardContent className="px-5 pb-1 pt-4">
                <Skeleton.ChartBars height={200} />
              </CardContent>
            </Card>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {/* Left: Activity Trend (bar chart) */}
            <Card className="gap-1 py-3">
              <CardHeader className="px-5">
                <CardTitle className="flex items-center gap-2 text-base font-medium">
                  <Activity className="h-4 w-4 text-muted-foreground" />
                  {t("activityTitle")}
                </CardTitle>
                <p className="text-sm text-muted-foreground">{t("activitySubtitle")}</p>
              </CardHeader>
              <CardContent className="px-5 pb-1">
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
                        <defs>
                          <linearGradient id="barGold" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor="#c89a3d" stopOpacity={0.9} />
                            <stop offset="95%" stopColor="#8b6520" stopOpacity={0.75} />
                          </linearGradient>
                        </defs>
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
                          fill="url(#barGold)"
                          radius={[4, 4, 0, 0]}
                        />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Right: Token Usage Trend (area chart) */}
            <Card className="gap-1 py-3">
              <CardHeader className="px-5">
                <CardTitle className="flex items-center gap-2 text-base font-medium">
                  <Database className="h-4 w-4 text-muted-foreground" />
                  {t("tokenTrendTitle")}
                </CardTitle>
                <p className="text-sm text-muted-foreground">{t("tokenTrendSubtitle")}</p>
              </CardHeader>
              <CardContent className="px-5 pb-1">
                {allTokensZero || activityData.length === 0 ? (
                  <div className="flex h-[200px] items-center justify-center text-sm text-muted-foreground">
                    {t("tokenTrendEmpty")}
                  </div>
                ) : (
                  <div className="h-[200px] text-muted-foreground">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart
                        data={activityData}
                        margin={{ top: 4, right: 4, left: 0, bottom: 4 }}
                      >
                        <defs>
                          <linearGradient id="areaIndigo" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                            <stop offset="95%" stopColor="#4338ca" stopOpacity={0.05} />
                          </linearGradient>
                        </defs>
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
                          width={36}
                          tick={TICK_STYLE}
                          tickLine={false}
                          axisLine={false}
                          allowDecimals={false}
                          tickFormatter={(v: number) => {
                            if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
                            if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`
                            return String(v)
                          }}
                        />
                        <Tooltip
                          content={<TokenTooltip />}
                          cursor={{ stroke: "rgba(128,128,128,0.3)", strokeDasharray: "3 3" }}
                        />
                        <Area
                          type="monotone"
                          dataKey="tokens"
                          name={t("tokenTrendTitle")}
                          stroke="#6366f1"
                          strokeWidth={2}
                          fill="url(#areaIndigo)"
                          fillOpacity={0.3}
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        )}

        {/* ---- 4. Content Cards ---- */}
        {loading ? (
          <>
            {/* Row A skeleton */}
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              {/* Recent Conversations skeleton */}
              <Card className="gap-0 py-2">
                <CardHeader className="px-5 py-3">
                  <Skeleton className="h-5 w-40" />
                </CardHeader>
                <CardContent className="px-0 pb-1">
                  <ul className="divide-y divide-border">
                    {Array.from({ length: 5 }).map((_, i) => (
                      <li key={i}><Skeleton.ListRow /></li>
                    ))}
                  </ul>
                </CardContent>
              </Card>

              {/* My Agents skeleton */}
              <Card className="gap-0 py-2">
                <CardHeader className="px-5 py-3">
                  <Skeleton className="h-5 w-28" />
                </CardHeader>
                <CardContent className="px-5 pb-4 pt-1">
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    {Array.from({ length: 4 }).map((_, i) => <Skeleton.AgentCard key={i} />)}
                  </div>
                </CardContent>
              </Card>
            </div>

            {/* Market Spotlight skeleton */}
            <Card className="gap-0 py-2">
              <CardHeader className="px-5 py-3">
                <Skeleton className="h-5 w-36" />
              </CardHeader>
              <CardContent className="px-5 pb-4 pt-1">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-4">
                  {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="rounded-lg border border-border p-3 space-y-2">
                      <div className="flex items-center gap-2">
                        <Skeleton className="h-8 w-8 rounded-lg" />
                        <Skeleton className="h-4 w-24" />
                      </div>
                      <Skeleton className="h-3 w-full" />
                      <Skeleton className="h-3 w-2/3" />
                      <Skeleton className="h-5 w-16 rounded-full" />
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </>
        ) : (
          <>
            {/* Row A: Recent Conversations + My Agents */}
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">

              {/* Recent Conversations */}
              <Card className="gap-0 py-2">
                <CardHeader className="px-5 py-3">
                  <Link href="/chats" className="group flex items-center justify-between rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">
                    <CardTitle className="flex items-center gap-2 text-base font-medium">
                      <Clock className="h-4 w-4 text-muted-foreground" />
                      {t("recentTitle")}
                    </CardTitle>
                    <ChevronRight className="h-4 w-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                  </Link>
                </CardHeader>
                <CardContent className="px-0 pb-1">
                  {!conversations.length ? (
                    <div className="flex flex-col items-center gap-3 px-6 py-8 text-sm text-muted-foreground">
                      <p>{t("recentEmpty")}</p>
                      <Button variant="outline" size="sm" asChild>
                        <Link href="/new">{t("recentEmptyCta")}</Link>
                      </Button>
                    </div>
                  ) : (
                    <ul className="divide-y divide-border">
                      {conversations.slice(0, 6).map((conv) => (
                        <li key={conv.id}>
                          <Link
                            href={`/?c=${conv.id}`}
                            className="flex items-center gap-3 px-4 py-2 transition-colors hover:bg-accent/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                          >
                            <span className="flex h-8 w-8 flex-none items-center justify-center rounded-lg bg-muted">
                              <MessageSquare className="h-3.5 w-3.5 text-muted-foreground" />
                            </span>
                            <p className="flex-1 min-w-0 truncate text-sm font-medium text-foreground">
                              {conv.title || t("untitled")}
                            </p>
                            <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                              {relativeTime(conv.updated_at ?? conv.created_at, locale)}
                            </span>
                          </Link>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>

              {/* My Agents */}
              <Card className="gap-0 py-2">
                <CardHeader className="px-5 py-3">
                  <Link href="/agents" className="group flex items-center justify-between rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">
                    <CardTitle className="flex items-center gap-2 text-base font-medium">
                      <Bot className="h-4 w-4 text-muted-foreground" />
                      {t("agentsTitle")}
                    </CardTitle>
                    <ChevronRight className="h-4 w-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                  </Link>
                </CardHeader>
                <CardContent className="px-5 pb-4 pt-1">
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
                            <Badge
                              variant="secondary"
                              className="mt-1 h-5 px-2 text-xs font-normal"
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

            {/* Market Spotlight */}
            <Card className="gap-0 py-2">
              <CardHeader className="px-5 py-3">
                <Link href="/market" className="group flex items-center justify-between rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">
                  <CardTitle className="flex items-center gap-2 text-base font-medium">
                    <ShoppingBag className="h-4 w-4 text-muted-foreground" />
                    {t("marketTitle")}
                  </CardTitle>
                  <ChevronRight className="h-4 w-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                </Link>
              </CardHeader>
              <CardContent className="px-5 pb-4 pt-1">
                {!marketItems.length ? (
                  <div className="flex flex-col items-center gap-3 py-6 text-sm text-muted-foreground">
                    <p>{t("marketEmpty")}</p>
                    <Button variant="outline" size="sm" asChild>
                      <Link href="/market">{t("marketEmptyCta")}</Link>
                    </Button>
                  </div>
                ) : (
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-4">
                    {marketItems.slice(0, 4).map((item) => (
                      <Link
                        key={item.id}
                        href="/market"
                        className="flex flex-col gap-2 rounded-lg border border-border p-3 transition-colors hover:bg-accent/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                      >
                        <div className="flex items-center gap-2">
                          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted text-base">
                            {item.icon || <ShoppingBag className="h-3.5 w-3.5 text-muted-foreground" />}
                          </span>
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-medium text-foreground">{item.name}</p>
                          </div>
                        </div>
                        <p className="line-clamp-2 text-xs text-muted-foreground">{item.description || ""}</p>
                        <div className="flex items-center gap-2 mt-auto">
                          <Badge variant="secondary" className="text-xs font-normal">{item.resource_type.replace("_", " ")}</Badge>
                          {item.is_subscribed && <Badge variant="outline" className="text-xs font-normal text-emerald-600">{t("marketSubscribed")}</Badge>}
                        </div>
                      </Link>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </div>
  )
}
