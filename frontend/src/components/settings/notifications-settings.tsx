"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import { Loader2, Bell, Info } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Input } from "@/components/ui/input"
import { apiFetch } from "@/lib/api"

interface NotifPref {
  event: string
  email: boolean
  webhook: boolean
  webhook_url?: string
}

const EVENT_KEYS = [
  "quota_warning",
  "task_failure",
  "review_result",
  "security_alert",
  "system_update",
] as const

const EVENT_LABEL_KEYS: Record<string, string> = {
  quota_warning: "eventQuotaWarning",
  task_failure: "eventTaskFailure",
  review_result: "eventReviewResult",
  security_alert: "eventSecurityAlert",
  system_update: "eventSystemUpdate",
}

export function NotificationsSettings() {
  const t = useTranslations("settings.notifications")
  const [preferences, setPreferences] = useState<NotifPref[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const loadPreferences = useCallback(async () => {
    try {
      const data = await apiFetch<{ preferences: NotifPref[] }>("/api/me/notifications")
      if (data.preferences && data.preferences.length > 0) {
        setPreferences(data.preferences)
      } else {
        // Initialize with defaults
        setPreferences(
          EVENT_KEYS.map((event) => ({
            event,
            email: false,
            webhook: false,
            webhook_url: "",
          })),
        )
      }
    } catch {
      // Initialize with defaults on error
      setPreferences(
        EVENT_KEYS.map((event) => ({
          event,
          email: false,
          webhook: false,
          webhook_url: "",
        })),
      )
      toast.error(t("loadFailed"))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    loadPreferences()
  }, [loadPreferences])

  const updatePref = (event: string, field: keyof NotifPref, value: boolean | string) => {
    setPreferences((prev) =>
      prev.map((p) =>
        p.event === event ? { ...p, [field]: value } : p,
      ),
    )
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await apiFetch("/api/me/notifications", {
        method: "PUT",
        body: JSON.stringify({ preferences }),
      })
      toast.success(t("saved"))
    } catch {
      toast.error(t("saveFailed"))
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("description")}</p>
      </div>

      {/* Coming Soon Banner */}
      <div className="rounded-md border border-blue-500/30 bg-blue-50 dark:bg-blue-950/20 px-4 py-3 flex items-start gap-3">
        <Info className="h-4 w-4 text-blue-600 dark:text-blue-400 mt-0.5 shrink-0" />
        <p className="text-sm text-blue-700 dark:text-blue-300">
          {t("comingSoonBanner")}
        </p>
      </div>

      {/* Preference Matrix */}
      <div className="rounded-md border border-border overflow-x-auto">
        <table className="w-full min-w-max text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/40">
              <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("eventLabel")}</th>
              <th className="px-4 py-2.5 text-center font-medium text-muted-foreground">{t("emailLabel")}</th>
              <th className="px-4 py-2.5 text-center font-medium text-muted-foreground">{t("webhookLabel")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {preferences.map((pref) => (
              <tr key={pref.event} className="hover:bg-muted/20 transition-colors">
                <td className="px-4 py-3 font-medium text-foreground">
                  {t(EVENT_LABEL_KEYS[pref.event] || pref.event)}
                </td>
                <td className="px-4 py-3 text-center">
                  <Switch
                    checked={pref.email}
                    onCheckedChange={(checked) => updatePref(pref.event, "email", checked)}
                  />
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center justify-center gap-2">
                    <Switch
                      checked={pref.webhook}
                      onCheckedChange={(checked) => updatePref(pref.event, "webhook", checked)}
                    />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Webhook URL (shown if any webhook is enabled) */}
      {preferences.some((p) => p.webhook) && (
        <div className="space-y-2 max-w-md">
          <label className="text-sm font-medium">{t("webhookUrlLabel")}</label>
          <Input
            type="url"
            value={preferences.find((p) => p.webhook)?.webhook_url ?? ""}
            onChange={(e) => {
              // Apply webhook URL to all webhook-enabled prefs
              setPreferences((prev) =>
                prev.map((p) =>
                  p.webhook ? { ...p, webhook_url: e.target.value } : p,
                ),
              )
            }}
            placeholder={t("webhookUrlPlaceholder")}
          />
        </div>
      )}

      <Button onClick={handleSave} disabled={saving}>
        {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
        {t("savePreferences")}
      </Button>
    </div>
  )
}
