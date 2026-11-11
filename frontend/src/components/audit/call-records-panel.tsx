"use client"

import { useCallback, useEffect, useState } from "react"
import { useTranslations } from "next-intl"
import { Loader2, RefreshCw } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import { apiFetch } from "@/lib/api"
import {
  ALL,
  type AuditRecordPage,
  type RawAuditRecord,
  buildAuditQuery,
  callDurationMs,
  callRequestLine,
  callStatus,
  normalizeAuditPage,
} from "@/lib/audit-records"
import { getErrorMessage } from "@/lib/error-utils"

const PAGE_SIZE = 50

interface CallRecordsPanelProps {
  /**
   * The list to read, without a query string: `/api/admin/call-log`, or an
   * organization's `/api/orgs/{id}/audit-log` (read with `source=call`).
   */
  endpoint: string
  /** True for an organization's list, which takes the source as a parameter. */
  viaSource?: boolean
  /** The organization of a row, as a name. Left out, the column is not shown. */
  orgName?: (orgId: string | null) => string | null
}

/** The calls tools made to connectors, databases and MCP servers, one row each. */
export function CallRecordsPanel({ endpoint, viaSource = false, orgName }: CallRecordsPanelProps) {
  const t = useTranslations("admin.audit")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const { formatDateTimeFull } = useDateFormatter()

  const [data, setData] = useState<AuditRecordPage | null>(null)
  const [page, setPage] = useState(1)
  const [outcome, setOutcome] = useState(ALL)
  const [isLoading, setIsLoading] = useState(true)

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const params = buildAuditQuery({
        page,
        size: PAGE_SIZE,
        source: viaSource ? "call" : undefined,
        outcome,
      })
      const res = await apiFetch<{ items: RawAuditRecord[]; total: number; page: number; size: number; pages: number }>(
        `${endpoint}?${params}`,
      )
      setData(normalizeAuditPage(res))
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [endpoint, viaSource, page, outcome, tError])

  useEffect(() => { load() }, [load])

  const kindLabel = (kind: string | null) => {
    const key = `callKinds.${kind ?? "connector"}` as Parameters<typeof t>[0]
    return t.has(key) ? t(key) : (kind ?? "")
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">{t("filterOutcome")}</label>
          <Select
            value={outcome}
            onValueChange={(value) => { setOutcome(value); setPage(1) }}
          >
            <SelectTrigger className="w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("filterOutcomeAll")}</SelectItem>
              <SelectItem value="success">{t("outcomeSuccess")}</SelectItem>
              <SelectItem value="failed">{t("outcomeFailed")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={isLoading} className="h-9 self-end">
          <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${isLoading ? "animate-spin" : ""}`} />
          {tc("refresh")}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : !data || data.items.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-6 text-sm text-muted-foreground text-center">
          {outcome !== ALL ? t("noCallResults") : t("noCallEntries")}
        </div>
      ) : (
        <>
          <div className="rounded-md border border-border overflow-x-auto">
            <table className="w-full min-w-max text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/40">
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground whitespace-nowrap">{t("timeColumn")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colCaller")}</th>
                  {orgName && (
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colOrg")}</th>
                  )}
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colCalled")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colCallAction")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colRequest")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colStatus")}</th>
                  <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("colDuration")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.items.map((record) => {
                  const status = callStatus(record)
                  const duration = callDurationMs(record)
                  const actionName = typeof record.extra.action_name === "string" ? record.extra.action_name : null
                  return (
                    <tr key={record.id} className="hover:bg-muted/20 transition-colors">
                      <td className="px-4 py-2.5 text-xs text-muted-foreground whitespace-nowrap tabular-nums">
                        {formatDateTimeFull(record.created_at)}
                      </td>
                      <td className="px-4 py-2.5 text-xs">
                        {record.actor_label
                          ? <span className="font-medium text-foreground">{record.actor_label}</span>
                          : record.actor_id
                            ? <span className="font-mono text-muted-foreground">{record.actor_id.slice(0, 8)}</span>
                            : <span className="text-muted-foreground/50">&mdash;</span>}
                      </td>
                      {orgName && (
                        <td className="px-4 py-2.5 text-xs text-muted-foreground">
                          {orgName(record.org_id) ?? <span className="text-muted-foreground/50">&mdash;</span>}
                        </td>
                      )}
                      <td className="px-4 py-2.5 text-xs">
                        <span className="font-medium text-foreground">{record.target_label ?? "—"}</span>
                        <span className="ml-1.5 text-muted-foreground">{kindLabel(record.target_type)}</span>
                      </td>
                      <td className="px-4 py-2.5 text-xs text-muted-foreground">{actionName ?? "—"}</td>
                      <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground max-w-[320px] truncate" title={callRequestLine(record) ?? undefined}>
                        {callRequestLine(record) ?? "—"}
                      </td>
                      <td className="px-4 py-2.5">
                        <span
                          className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${
                            record.outcome === "success"
                              ? "border-green-500/30 bg-green-500/15 text-green-700 dark:text-green-400"
                              : "border-red-500/30 bg-red-500/15 text-red-700 dark:text-red-400"
                          }`}
                        >
                          {record.outcome === "success" ? t("outcomeSuccess") : t("outcomeFailed")}
                          {status !== null && <span className="ml-1 tabular-nums">{status}</span>}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-xs text-muted-foreground text-right tabular-nums">
                        {duration !== null ? t("durationMs", { ms: duration }) : "—"}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>{t("totalEntries", { count: data.total })}</span>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                {t("previous")}
              </Button>
              <span>{t("pageOf", { page, pages: data.pages })}</span>
              <Button variant="outline" size="sm" disabled={page >= data.pages} onClick={() => setPage((p) => p + 1)}>
                {tc("next")}
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
