"use client"

import { useCallback, useEffect, useState } from "react"
import { useTranslations } from "next-intl"
import { Loader2, ShieldCheck } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useAuditActionLabel } from "@/hooks/use-audit-action-label"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import { apiFetch } from "@/lib/api"
import { describeUserAgent } from "@/lib/audit-records"

const PAGE_SIZE = 20

interface SecurityEvent {
  id: string
  created_at: string
  action: string
  outcome: string
  target_label: string | null
  detail: string | null
  /** A platform administrator did this to the account; no address is shown for it. */
  by_administrator: boolean
  ip_address: string | null
  user_agent: string | null
}

interface SecurityEventPage {
  items: SecurityEvent[]
  total: number
  page: number
  size: number
  pages: number
}

/**
 * The security events of the signed-in user's own account: password,
 * two-factor, sign-in providers, API keys, session revocation.
 */
export function SecurityEventsSection() {
  const t = useTranslations("settings.account")
  const ta = useTranslations("admin.audit")
  const tc = useTranslations("common")
  const { formatDateTime } = useDateFormatter()
  const actionLabel = useAuditActionLabel()

  const [data, setData] = useState<SecurityEventPage | null>(null)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await apiFetch<SecurityEventPage>(`/api/me/security-events?page=${page}&size=${PAGE_SIZE}`))
    } catch {
      toast.error(t("securityEventsLoadFailed"))
    } finally {
      setLoading(false)
    }
  }, [page, t])

  useEffect(() => { load() }, [load])

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-medium">{t("securityEventsTitle")}</h3>
        <p className="text-sm text-muted-foreground">{t("securityEventsDescription")}</p>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : !data || data.items.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-6 text-center">
          <ShieldCheck className="mx-auto h-6 w-6 text-muted-foreground/50 mb-2" />
          <p className="text-sm text-muted-foreground">{t("securityEventsNoData")}</p>
        </div>
      ) : (
        <>
          <div className="rounded-md border border-border overflow-x-auto">
            <table className="w-full min-w-max text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/40">
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("securityEventsEvent")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("sessionsDevice")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("sessionsIp")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("sessionsTime")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.items.map((event) => (
                  <tr key={event.id} className="hover:bg-muted/20 transition-colors">
                    <td className="px-4 py-3 text-foreground">
                      <span className="font-medium">{actionLabel(event.action)}</span>
                      {event.action.startsWith("api_key.") && event.target_label && (
                        <span className="ml-1.5 text-xs text-muted-foreground">{event.target_label}</span>
                      )}
                      {event.by_administrator && (
                        <Badge variant="outline" className="ml-2 border-amber-500/30 bg-amber-50 text-amber-700 dark:bg-amber-950/20 dark:text-amber-400">
                          {t("securityEventsByAdministrator")}
                        </Badge>
                      )}
                      {event.outcome !== "success" && (
                        <Badge variant="outline" className="ml-2 border-red-500/30 bg-red-50 text-red-700 dark:bg-red-950/20 dark:text-red-400">
                          {event.outcome === "denied" ? ta("outcomeDenied") : ta("outcomeFailed")}
                        </Badge>
                      )}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {describeUserAgent(event.user_agent) ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground font-mono text-xs">
                      {event.ip_address ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground text-xs">
                      {formatDateTime(event.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {data.pages > 1 && (
            <div className="flex items-center justify-end gap-2 text-sm text-muted-foreground">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                {ta("previous")}
              </Button>
              <span>{ta("pageOf", { page, pages: data.pages })}</span>
              <Button variant="outline" size="sm" disabled={page >= data.pages} onClick={() => setPage((p) => p + 1)}>
                {tc("next")}
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
