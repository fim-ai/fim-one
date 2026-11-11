"use client"

import { useCallback, useEffect, useState } from "react"
import { useTranslations } from "next-intl"
import { Loader2, RefreshCw } from "lucide-react"
import { toast } from "sonner"

import { CallRecordsPanel } from "@/components/audit/call-records-panel"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { useAuditActionLabel } from "@/hooks/use-audit-action-label"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import { apiFetch } from "@/lib/api"
import {
  ALL,
  AUDIT_OUTCOMES,
  type AuditRecordPage,
  type RawAuditRecord,
  buildAuditQuery,
  formatAuditChange,
  normalizeAuditPage,
} from "@/lib/audit-records"
import { getErrorMessage } from "@/lib/error-utils"
import { cn } from "@/lib/utils"

const PAGE_SIZE = 50

interface OrgAuditSheetProps {
  open: boolean
  onClose: () => void
  org: { id: string; name: string } | null
}

/**
 * An organization's audit records, for its owner and administrators: the
 * events that belong to it and the calls made through its agents and
 * workflows. The server sends no address and no device for this list.
 */
export function OrgAuditSheet({ open, onClose, org }: OrgAuditSheetProps) {
  const t = useTranslations("organizations")
  const ta = useTranslations("admin.audit")
  const [tab, setTab] = useState<"events" | "calls">("events")

  useEffect(() => {
    if (open) setTab("events")
  }, [open, org?.id])

  return (
    <Sheet open={open} onOpenChange={(next) => { if (!next) onClose() }}>
      <SheetContent side="right" className="w-full sm:max-w-4xl overflow-y-auto">
        <SheetHeader>
          <SheetTitle>{t("auditLogTitle", { name: org?.name ?? "" })}</SheetTitle>
          <SheetDescription>{t("auditLogDescription")}</SheetDescription>
        </SheetHeader>

        <div className="space-y-4 px-4 pb-6">
          <div className="inline-flex items-center rounded-md border border-border bg-muted/40 p-0.5 gap-0.5">
            {(["events", "calls"] as const).map((name) => (
              <button
                key={name}
                onClick={() => setTab(name)}
                className={cn(
                  "px-3 py-1.5 text-sm rounded-sm transition-colors font-medium",
                  tab === name
                    ? "bg-background shadow-xs text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {name === "events" ? t("auditEvents") : ta("callLog")}
              </button>
            ))}
          </div>

          {org && open && tab === "events" && <OrgEventsPanel orgId={org.id} />}
          {org && open && tab === "calls" && (
            <CallRecordsPanel endpoint={`/api/orgs/${org.id}/audit-log`} viaSource />
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

function OrgEventsPanel({ orgId }: { orgId: string }) {
  const t = useTranslations("organizations")
  const ta = useTranslations("admin.audit")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const { formatDateTimeFull } = useDateFormatter()
  const actionLabel = useAuditActionLabel()

  const [data, setData] = useState<AuditRecordPage | null>(null)
  const [page, setPage] = useState(1)
  const [outcome, setOutcome] = useState(ALL)
  const [isLoading, setIsLoading] = useState(true)

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const params = buildAuditQuery({ page, size: PAGE_SIZE, outcome })
      const res = await apiFetch<{ items: RawAuditRecord[]; total: number; page: number; size: number; pages: number }>(
        `/api/orgs/${orgId}/audit-log?${params}`,
      )
      setData(normalizeAuditPage(res))
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [orgId, page, outcome, tError])

  useEffect(() => { load() }, [load])

  const outcomeLabel = (value: string): string =>
    value === "denied" ? ta("outcomeDenied") : value === "failed" ? ta("outcomeFailed") : value === "success" ? ta("outcomeSuccess") : value

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">{ta("filterOutcome")}</label>
          <Select value={outcome} onValueChange={(value) => { setOutcome(value); setPage(1) }}>
            <SelectTrigger className="w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{ta("filterOutcomeAll")}</SelectItem>
              {AUDIT_OUTCOMES.map((value) => (
                <SelectItem key={value} value={value}>
                  {outcomeLabel(value)}
                </SelectItem>
              ))}
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
          {outcome !== ALL ? ta("noResults") : t("auditNoEvents")}
        </div>
      ) : (
        <>
          <div className="rounded-md border border-border overflow-x-auto">
            <table className="w-full min-w-max text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/40">
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground whitespace-nowrap">{ta("timeColumn")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{ta("adminColumn")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{ta("actionColumn")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{ta("outcomeColumn")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{ta("targetColumn")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{ta("detailColumn")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.items.map((record) => (
                  <tr key={record.id} className="hover:bg-muted/20 transition-colors align-top">
                    <td className="px-4 py-2.5 text-xs text-muted-foreground whitespace-nowrap tabular-nums">
                      {formatDateTimeFull(record.created_at)}
                    </td>
                    <td className="px-4 py-2.5 font-medium text-foreground">
                      {record.actor_label ?? <span className="text-muted-foreground/50">&mdash;</span>}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="inline-flex items-center rounded-md border border-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
                        {actionLabel(record.action)}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <span
                        className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${
                          record.outcome === "success"
                            ? "border-green-500/30 bg-green-500/15 text-green-700 dark:text-green-400"
                            : "border-red-500/30 bg-red-500/15 text-red-700 dark:text-red-400"
                        }`}
                      >
                        {outcomeLabel(record.outcome)}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-xs">
                      {record.target_label
                        ? <span className="font-medium text-foreground">{record.target_label}</span>
                        : <span className="text-muted-foreground/50">&mdash;</span>}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted-foreground max-w-[320px]">
                      {record.detail && <span className="block break-words">{record.detail}</span>}
                      {record.changes && Object.entries(record.changes).map(([field, change]) => (
                        <span key={field} className="block break-words">
                          <span className="font-mono">{field}</span>{" "}
                          {formatAuditChange(change, ta("changedValue"))}
                        </span>
                      ))}
                      {!record.detail && !record.changes && "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>{ta("totalEntries", { count: data.total })}</span>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                {ta("previous")}
              </Button>
              <span>{ta("pageOf", { page, pages: data.pages })}</span>
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
