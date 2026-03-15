"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useTranslations, useLocale } from "next-intl"
import { cn } from "@/lib/utils"
import { toast } from "sonner"
import {
  Loader2,
  Bell,
  Settings2,
  Send,
  AlertTriangle,
  Plug,
  Calendar,
  Shield,
  Zap,
  Info,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { adminApi, type AdminNotificationConfig, type AdminNotificationEvent } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"

const PAGE_SIZE = 20

type SubView = "events" | "config"

const EVENT_ICONS: Record<string, React.ElementType> = {
  quota_hit: AlertTriangle,
  connector_failure: Plug,
  schedule_failure: Calendar,
  login_anomaly: Shield,
}

export function AdminNotifications() {
  const t = useTranslations("admin.notifications")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const locale = useLocale()

  const [view, setView] = useState<SubView>("events")

  // --- Events ---
  const [events, setEvents] = useState<AdminNotificationEvent[]>([])
  const [evTotal, setEvTotal] = useState(0)
  const [evPage, setEvPage] = useState(1)
  const [evPages, setEvPages] = useState(1)
  const [evLoading, setEvLoading] = useState(true)

  // --- Config ---
  const [config, setConfig] = useState<AdminNotificationConfig | null>(null)
  const [configLoading, setConfigLoading] = useState(true)
  const [isMutating, setIsMutating] = useState(false)

  // --- Auto-refresh ---
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // --- Load events ---
  const loadEvents = useCallback(async () => {
    setEvLoading(true)
    try {
      const data = await adminApi.listNotificationEvents({ page: evPage, size: PAGE_SIZE })
      setEvents(data.items ?? [])
      setEvTotal(data.total ?? 0)
      setEvPages(Math.max(1, Math.ceil((data.total ?? 0) / PAGE_SIZE)))
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setEvLoading(false)
    }
  }, [evPage, tError])

  // --- Load config ---
  const loadConfig = useCallback(async () => {
    setConfigLoading(true)
    try {
      const data = await adminApi.getNotificationConfig()
      setConfig(data)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setConfigLoading(false)
    }
  }, [tError])

  useEffect(() => {
    if (view === "events") {
      loadEvents()
      // Auto-refresh every 30s
      intervalRef.current = setInterval(loadEvents, 30000)
      return () => { if (intervalRef.current) clearInterval(intervalRef.current) }
    }
  }, [view, loadEvents])

  useEffect(() => {
    if (view === "config") loadConfig()
  }, [view, loadConfig])

  // --- Update config ---
  const handleConfigToggle = async (key: keyof AdminNotificationConfig) => {
    if (!config) return
    const updated = { ...config, [key]: !config[key] }
    setConfig(updated)
    try {
      await adminApi.updateNotificationConfig(updated)
      toast.success(t("configSaved"))
    } catch (err) {
      setConfig(config) // rollback
      toast.error(getErrorMessage(err, tError))
    }
  }

  // --- Send test ---
  const handleSendTest = async () => {
    setIsMutating(true)
    try {
      await adminApi.sendTestNotification()
      toast.success(t("testSent"))
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  const getEventTypeBadge = (type: string) => {
    const typeKey = type as keyof typeof EVENT_ICONS
    const label = t(`type${type.charAt(0).toUpperCase() + type.slice(1).replace(/_([a-z])/g, (_, c) => c.toUpperCase())}` as Parameters<typeof t>[0])
    const colors: Record<string, string> = {
      quota_hit: "border-yellow-500/40 text-yellow-600 dark:text-yellow-400",
      connector_failure: "border-red-500/40 text-red-600 dark:text-red-400",
      schedule_failure: "border-red-500/40 text-red-600 dark:text-red-400",
      login_anomaly: "border-orange-500/40 text-orange-600 dark:text-orange-400",
    }
    return (
      <Badge variant="outline" className={colors[type] || ""}>
        {label}
      </Badge>
    )
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5"
          onClick={handleSendTest}
          disabled={isMutating}
        >
          {isMutating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          {t("sendTest")}
        </Button>
      </div>

      {/* Sub-tab toggle */}
      <div className="inline-flex items-center rounded-md border border-border bg-muted/40 p-0.5 gap-0.5">
        <button
          onClick={() => setView("events")}
          className={cn(
            "inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-sm transition-colors font-medium",
            view === "events" ? "bg-background shadow-xs text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Bell className="h-3.5 w-3.5" />
          {t("eventsTab")}
        </button>
        <button
          onClick={() => setView("config")}
          className={cn(
            "inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-sm transition-colors font-medium",
            view === "config" ? "bg-background shadow-xs text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Settings2 className="h-3.5 w-3.5" />
          {t("configTab")}
        </button>
      </div>

      {/* ===================== EVENTS ===================== */}
      {view === "events" && (
        <>
          <p className="text-xs text-muted-foreground">{t("autoRefresh")}</p>

          {evLoading && (!events || events.length === 0) ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : !events || events.length === 0 ? (
            <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
              {t("noEvents")}
            </div>
          ) : (
            <div className="rounded-md border border-border overflow-x-auto">
              <table className="w-full min-w-max text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/40">
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colType")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colDescription")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colUser")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colTime")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {events.map((ev) => (
                    <tr key={ev.id} className="hover:bg-muted/20 transition-colors">
                      <td className="px-4 py-3">{getEventTypeBadge(ev.type)}</td>
                      <td className="px-4 py-3 text-foreground">{ev.description}</td>
                      <td className="px-4 py-3 text-muted-foreground">{ev.user || "--"}</td>
                      <td className="px-4 py-3 text-muted-foreground text-xs whitespace-nowrap tabular-nums">
                        {new Date(ev.created_at).toLocaleString(locale, {
                          month: "short", day: "numeric",
                          hour: "2-digit", minute: "2-digit", second: "2-digit",
                        })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {!evLoading && events.length > 0 && evPages > 1 && (
            <div className="flex items-center justify-between text-sm text-muted-foreground">
              <span>{t("totalItems", { count: evTotal })}</span>
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" disabled={evPage <= 1} onClick={() => setEvPage((p) => Math.max(1, p - 1))}>
                  {t("previous")}
                </Button>
                <span>{t("pageOf", { page: evPage, pages: evPages })}</span>
                <Button variant="outline" size="sm" disabled={evPage >= evPages} onClick={() => setEvPage((p) => Math.min(evPages, p + 1))}>
                  {tc("next")}
                </Button>
              </div>
            </div>
          )}
        </>
      )}

      {/* ===================== COMING SOON BANNER ===================== */}
      <div className="rounded-md border border-amber-500/30 bg-amber-50 dark:bg-amber-950/20 px-4 py-3 flex items-start gap-3">
        <Info className="h-4 w-4 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
        <div>
          <p className="text-sm font-medium text-amber-800 dark:text-amber-200">{t("comingSoonTitle")}</p>
          <p className="text-sm text-amber-700 dark:text-amber-300 mt-0.5">{t("comingSoonBody")}</p>
        </div>
      </div>

      {/* ===================== CONFIGURATION ===================== */}
      {view === "config" && (
        <>
          {configLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : config ? (
            <div className="rounded-md border border-border p-4 space-y-6">
              <div>
                <h3 className="text-sm font-semibold">{t("configTitle")}</h3>
                <p className="text-xs text-muted-foreground">{t("configSubtitle")}</p>
              </div>

              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <AlertTriangle className="h-4 w-4 text-yellow-600" />
                    <Label className="cursor-pointer">{t("quotaHitToggle")}</Label>
                  </div>
                  <Switch checked={config.quota_hit} onCheckedChange={() => handleConfigToggle("quota_hit")} />
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <Plug className="h-4 w-4 text-red-600" />
                    <Label className="cursor-pointer">{t("connectorFailToggle")}</Label>
                  </div>
                  <Switch checked={config.connector_failure} onCheckedChange={() => handleConfigToggle("connector_failure")} />
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <Calendar className="h-4 w-4 text-red-600" />
                    <Label className="cursor-pointer">{t("scheduleFailToggle")}</Label>
                  </div>
                  <Switch checked={config.schedule_failure} onCheckedChange={() => handleConfigToggle("schedule_failure")} />
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <Shield className="h-4 w-4 text-orange-600" />
                    <Label className="cursor-pointer">{t("loginAnomalyToggle")}</Label>
                  </div>
                  <Switch checked={config.login_anomaly} onCheckedChange={() => handleConfigToggle("login_anomaly")} />
                </div>
              </div>
            </div>
          ) : null}
        </>
      )}
    </div>
  )
}
