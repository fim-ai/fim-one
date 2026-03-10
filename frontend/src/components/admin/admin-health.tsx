"use client"

import { useState, useEffect } from "react"
import { useTranslations } from "next-intl"
import { CheckCircle2, XCircle, AlertTriangle, Info, Loader2 } from "lucide-react"
import { adminApi } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { IntegrationHealth } from "@/types/admin"

const GROUPS: Record<string, string[]> = {
  infrastructure: ["database", "redis"],
  ai: ["llm", "fast_llm"],
  retrieval: ["embedding", "reranker"],
  web: ["web_search", "web_fetch"],
  email: ["smtp"],
  media: ["image_gen"],
  oauth: ["oauth_github", "oauth_google", "oauth_discord", "oauth_feishu"],
  analytics: ["analytics_ga4", "analytics_umami", "analytics_plausible"],
}

export function AdminHealth() {
  const t = useTranslations("admin.health")
  const [items, setItems] = useState<IntegrationHealth[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    adminApi
      .getSystemHealth()
      .then(setItems)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground text-sm">
        <Loader2 className="h-4 w-4 animate-spin" />
        {t("loading")}
      </div>
    )
  }

  const itemMap = Object.fromEntries(items.map((i) => [i.key, i]))

  const groupEntries = Object.entries(GROUPS)
    .map(([group, keys]) => ({
      group,
      items: keys.map((k) => itemMap[k]).filter(Boolean),
    }))
    .filter((g) => g.items.length > 0)

  // Catch any items not in a group
  const groupedKeys = new Set(Object.values(GROUPS).flat())
  const ungrouped = items.filter((i) => !groupedKeys.has(i.key))

  const totalCount = items.length
  const configuredCount = items.filter((i) => i.configured).length

  const requiredItems = items.filter((i) => i.level === "required")
  const recommendedItems = items.filter((i) => i.level === "recommended")
  const requiredOk = requiredItems.every((i) => i.configured)
  const recommendedOk = recommendedItems.every((i) => i.configured)

  // Summary status: red if required missing, amber if recommended missing, green otherwise
  const summaryStatus = !requiredOk ? "error" : !recommendedOk ? "warning" : "success"

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {/* Summary bar */}
      <div
        className={cn(
          "rounded-md border px-4 py-3 text-sm flex items-center gap-3",
          summaryStatus === "success" && "border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950/30",
          summaryStatus === "warning" && "border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/30",
          summaryStatus === "error" && "border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950/30",
        )}
      >
        <span
          className={cn(
            "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold shrink-0",
            summaryStatus === "success" && "bg-green-100 text-green-700 dark:bg-green-900/50 dark:text-green-300",
            summaryStatus === "warning" && "bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-300",
            summaryStatus === "error" && "bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-300",
          )}
        >
          {t("configuredCount", { count: configuredCount, total: totalCount })}
        </span>
        <span
          className={cn(
            "text-sm",
            summaryStatus === "success" && "text-green-700 dark:text-green-300",
            summaryStatus === "warning" && "text-amber-700 dark:text-amber-300",
            summaryStatus === "error" && "text-red-700 dark:text-red-300",
          )}
        >
          {summaryStatus === "success"
            ? t("allReady")
            : summaryStatus === "error"
              ? t("requiredNeedsConfig", { count: requiredItems.filter((i) => !i.configured).length })
              : t("recommendedNeedsConfig", { count: recommendedItems.filter((i) => !i.configured).length })}
        </span>
      </div>

      {/* Group sections */}
      <div className="space-y-6">
        {groupEntries.map(({ group, items: groupItems }) => (
          <div key={group}>
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">
              {t(`group.${group}`)}
            </div>
            <div className="divide-y divide-border rounded-md border border-border">
              {groupItems.map((item) => (
                <HealthRow key={item.key} item={item} />
              ))}
            </div>
          </div>
        ))}

        {ungrouped.length > 0 && (
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">
              {t("group.other")}
            </div>
            <div className="divide-y divide-border rounded-md border border-border">
              {ungrouped.map((item) => (
                <HealthRow key={item.key} item={item} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

const LEVEL_BADGE_STYLES: Record<string, string> = {
  required: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  recommended: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  optional: "bg-muted text-muted-foreground",
}

function HealthRow({ item }: { item: IntegrationHealth }) {
  const t = useTranslations("admin.health")

  // Determine icon and colors for unconfigured items based on level
  const unconfiguredIcon =
    item.level === "required" ? (
      <XCircle className="h-4 w-4 text-red-500 shrink-0" />
    ) : item.level === "recommended" ? (
      <AlertTriangle className="h-4 w-4 text-amber-500 shrink-0" />
    ) : (
      <Info className="h-4 w-4 text-muted-foreground shrink-0" />
    )

  const unconfiguredTextClass =
    item.level === "required"
      ? "text-red-600 dark:text-red-400"
      : item.level === "recommended"
        ? "text-amber-600 dark:text-amber-400"
        : "text-muted-foreground"

  const levelLabelKey =
    item.level === "required"
      ? "levelRequired"
      : item.level === "recommended"
        ? "levelRecommended"
        : "levelOptional"

  return (
    <div className="px-4 py-3 space-y-1.5">
      <div className="flex items-center gap-2">
        {item.configured ? (
          <CheckCircle2 className="h-4 w-4 text-green-500 shrink-0" />
        ) : (
          unconfiguredIcon
        )}
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <span className="text-sm font-medium shrink-0">{item.label}</span>
          <span
            className={cn(
              "inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium leading-none shrink-0",
              LEVEL_BADGE_STYLES[item.level],
            )}
          >
            {t(levelLabelKey)}
          </span>
          {item.detail && (
            <span className="text-xs text-muted-foreground truncate min-w-0 flex-1">
              {item.detail}
            </span>
          )}
          <span
            className={cn(
              "ml-auto text-xs shrink-0",
              item.configured ? "text-green-600 dark:text-green-400" : unconfiguredTextClass,
            )}
          >
            {item.configured ? t("configured") : t("notConfigured")}
          </span>
        </div>
      </div>
      {!item.configured && item.impact && (
        <div
          className={cn(
            "flex items-start gap-2 rounded-md px-3 py-2",
            item.level === "optional"
              ? "bg-muted/50 border border-border"
              : item.level === "required"
                ? "bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800"
                : "bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800",
          )}
        >
          {item.level === "optional" ? (
            <Info className="h-3.5 w-3.5 text-muted-foreground mt-0.5 shrink-0" />
          ) : item.level === "required" ? (
            <XCircle className="h-3.5 w-3.5 text-red-600 dark:text-red-400 mt-0.5 shrink-0" />
          ) : (
            <AlertTriangle className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
          )}
          <p
            className={cn(
              "text-xs",
              item.level === "optional"
                ? "text-muted-foreground"
                : item.level === "required"
                  ? "text-red-700 dark:text-red-300"
                  : "text-amber-700 dark:text-amber-300",
            )}
          >
            {t("impactLabel")}: {t.has(`impact.${item.key}`) ? t(`impact.${item.key}`) : item.impact}
          </p>
        </div>
      )}
    </div>
  )
}
